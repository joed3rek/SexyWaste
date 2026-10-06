"""Collection Intelligence Agent (data/ops.db): the planning and review loop over the collection system.

Not a chatbot. A background orchestrator over the engines that already exist; it adds no data model of
its own beyond the record of what it did (agent_run) and what it was asked to look at (agent_trigger).

  plan a day     demand (backend.demand) -> capacity check -> resource check -> constraint check ->
                 scenarios -> route optimisation (backend.routing.twotier, OR-Tools) -> evaluation ->
                 recommended plan (backend.plans, a draft) -> human approval -> adoption (deployed)
  review a day   planned vs actual (backend.operations) -> deviations -> causes where the records support
                 them, UNKNOWN otherwise -> patterns over the operational history -> proposals (never
                 applied by the agent)

Statuses of a planning run follow the brief: generated and validated while it runs, then `recommended`
(pending approval), `approved` (the plan is adopted: deployed) or `rejected`; `no_feasible_plan`,
`insufficient_data` or `nothing_to_plan` when it cannot recommend, and `superseded` when a newer run for
the same sector and day replaces it. Every run keeps its checks, the scenarios it tried with their
metrics, the reasons, the settings version (reference/agent.json) and who approved what and when.

Triggers: the schedule (plan the next day, review the day), conditions found on adopted plans (a vehicle
no longer available, new collection demand above a threshold) and manual requests. Every figure in a
recommendation comes from the records or the optimiser; where data is missing the agent says so.
"""

from __future__ import annotations

import io
import json
import logging
import math
import sqlite3
import threading
import uuid
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path

from backend import cycle as C
from backend import demand as D
from backend import facilities as F
from backend import operations as O
from backend import plans as PL
from backend import resources as R
from backend.config import ROOT
from backend.routing import points as P

log = logging.getLogger(__name__)
AGENT = {"name": "CityLoom agent", "role": "system"}
APPROVERS = ("planner", "admin")
RUN_STATUSES = ("running", "recommended", "approved", "rejected", "superseded", "no_feasible_plan", "insufficient_data",
                "nothing_to_plan", "reviewed", "failed")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS agent_run (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('plan', 'review')),
    sector TEXT,
    service_date TEXT NOT NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'condition', 'manual')),
    trigger_note TEXT,
    status TEXT NOT NULL CHECK (status IN {RUN_STATUSES}),
    config_version TEXT NOT NULL,
    checks TEXT NOT NULL DEFAULT '[]',        -- JSON: capacity, resource and constraint checks
    scenarios TEXT NOT NULL DEFAULT '[]',     -- JSON: scenarios tried, their plans and metrics
    recommendation TEXT,                      -- JSON: chosen plan, reasons, comparison, risks, alternatives
    findings TEXT,                            -- JSON: review findings
    plan_id TEXT,                             -- the recommended route plan (backend.plans)
    decided_by TEXT, decided_role TEXT, decided_at TEXT, decision_note TEXT,
    started_at TEXT NOT NULL, finished_at TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS agent_run_day ON agent_run (pilot, service_date, sector, kind);
CREATE TABLE IF NOT EXISTS agent_trigger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot TEXT NOT NULL, kind TEXT NOT NULL, sector TEXT, service_date TEXT NOT NULL,
    trigger TEXT NOT NULL, note TEXT,
    created_at TEXT NOT NULL, taken_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS agent_run_no_delete BEFORE DELETE ON agent_run
BEGIN SELECT RAISE(ABORT, 'agent runs are kept'); END;
"""


class AgentError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def config() -> dict:
    return json.load(io.open(ROOT / "reference" / "agent.json", encoding="utf-8"))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = PL.connect(db_path)
    con.executescript(F._SCHEMA + O._SCHEMA + _SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(name: str, kind: str, status: str, detail: str, **facts) -> dict:
    """One line of the capacity, resource or constraint check. kind: hard or soft; status: pass, warn, fail, unknown."""
    return {"name": name, "kind": kind, "status": status, "detail": detail, **facts}


# ---------- Capacity: a bound that no plan can beat ----------

def capacity_bound(fleet: list[dict], window_min: float, unload_min: float) -> dict:
    """The most door-to-door vehicles could collect in the window if they never drove: each trip at least
    loads a full body at the class's loading rate and unloads it. A sound upper bound, not an estimate:
    demand above it cannot be collected, whatever the routes."""
    total, rows = 0.0, []
    for r in fleet:
        c = P.vehicle_class(r["type"])
        cap, rate = float(c["payload_kg"]["value"]), float(c["service"]["kg_per_min"])
        trip_min = cap / rate + unload_min
        trips = math.floor(window_min / trip_min) if trip_min > 0 else 0
        kg = r["count"] * trips * cap
        rows.append({"type": r["type"], "count": r["count"], "payload_kg": cap, "max_trips": trips, "max_kg": round(kg)})
        total += kg
    return {"max_kg": round(total), "by_type": rows}


def observed_rate(con, pilot: str, cfg: dict) -> dict:
    """kg a door-to-door vehicle actually collected per hour, from recorded routes (operational memory)."""
    since = (Date.today() - timedelta(days=cfg["review"]["lookback_days"])).isoformat()
    rows = [r for r in O.records(con, pilot, subject="route") if r["kind"] == "primary" and r["service_date"] >= since
            and r["actual_kg"] and r["actual_min"]]
    if len(rows) < cfg["review"]["pattern_min_routes"]:
        return {"kg_per_vehicle_hour": None, "routes": len(rows), "confidence": "insufficient data"}
    rate = sum(r["actual_kg"] for r in rows) / (sum(r["actual_min"] for r in rows) / 60)
    return {"kg_per_vehicle_hour": round(rate, 1), "routes": len(rows), "confidence": "low" if len(rows) < 20 else "medium"}


# ---------- Planning a day ----------

def _fleets(av: dict, cfg: dict, demand_kg: float, window_min: float, unload: float) -> tuple[list, list, bool]:
    """The fleet available to the sector, by tier; with no vehicles entered, a hypothetical fleet sized from
    the capacity bound (twice the bound's minimum, as the bound ignores driving) and flagged as such."""
    classes = R.vehicle_classes()
    if av["inventory"]["vehicle"]:
        prim = [{"type": t, "count": n} for t, n in sorted(av["vehicles"].items()) if classes[t]["tier"] == "primary"]
        sec = [{"type": t, "count": n} for t, n in sorted(av["vehicles"].items()) if classes[t]["tier"] == "secondary"]
        return prim, sec, False
    t = cfg["planning"]["default_primary_type"]
    per = capacity_bound([{"type": t, "count": 1}], window_min, unload)["max_kg"] or 1
    n = max(1, math.ceil(2 * demand_kg / per))
    return [{"type": t, "count": n}], [{"type": cfg["planning"]["default_secondary_type"], "count": 1}], True


def _more_vehicles(prim: list, add: int, main: str) -> list:
    more = [dict(r, count=r["count"] + (add if r["type"] == main else 0)) for r in prim]
    if not any(r["type"] == main for r in more):
        more.append({"type": main, "count": add})
    return more


def _first_scenarios(prim: list, sec: list, bound: dict, demand_kg: float, window_h: float, cfg: dict, hypothetical: bool) -> list[dict]:
    """Stage 1: the fleet as available, unless the capacity bound already proves it cannot carry the demand;
    then straight to more vehicles (sized from the gap in the bound) and a longer window."""
    pc = cfg["planning"]
    if demand_kg <= bound["max_kg"]:
        return [{"key": "available", "label": "Fleet as available" + (" (hypothetical)" if hypothetical else ""),
                 "primary": prim, "secondary": sec, "window_h": window_h, "extra": {}}]
    main = max(prim, key=lambda r: r["count"])["type"]
    per = capacity_bound([{"type": main, "count": 1}], window_h * 60, pc["unload_min"])["max_kg"] or 1
    add = min(pc["max_extra_vehicles"], max(1, math.ceil((demand_kg - bound["max_kg"]) / per)))
    return [{"key": "more_vehicles", "label": f"{add} more {main.replace('_', ' ')}", "primary": _more_vehicles(prim, add, main),
             "secondary": sec, "window_h": window_h, "extra": {main: add}}] + _longer_window(prim, sec, window_h, cfg)


def _longer_window(prim, sec, window_h, cfg) -> list[dict]:
    ext = min(16.0, window_h + cfg["planning"]["max_window_extension_h"])
    return [{"key": "longer_window", "label": f"Window extended to {ext:g} h", "primary": prim, "secondary": sec,
             "window_h": ext, "extra": {}}] if ext > window_h else []


def _follow_ups(base: dict, prim: list, sec: list, window_h: float, cfg: dict) -> list[dict]:
    """Stage 2, from how the first plan actually turned out: over the window or demand left -> more vehicles
    (sized from the overrun) and a longer window; comfortably inside the window -> one vehicle fewer."""
    m, pc = base["metrics"], cfg["planning"]
    n = sum(r["count"] for r in prim)
    main = max(prim, key=lambda r: r["count"])["type"]
    if m["feasibility"] == "infeasible" or m["overtime_min"] > 0 or m["unserved_kg"] > 0:
        ratio = max(m["duration_min"] / m["window_min"] if m["window_min"] else 1, 1 + m["unserved_kg"] / max(1.0, m.get("collected_kg", 1.0)))
        add = min(pc["max_extra_vehicles"], max(1, math.ceil(n * (ratio - 1))))
        return [{"key": "more_vehicles", "label": f"{add} more {main.replace('_', ' ')}", "primary": _more_vehicles(prim, add, main),
                 "secondary": sec, "window_h": window_h, "extra": {main: add}}] + _longer_window(prim, sec, window_h, cfg)
    if m["slack_pct"] >= cfg["risk"]["medium_if_slack_below_pct"] and n > 1:
        fewer = [dict(r) for r in prim]
        big = max(fewer, key=lambda r: r["count"])
        big["count"] -= 1
        return [{"key": "fewer_vehicles", "label": f"One {big['type'].replace('_', ' ')} fewer",
                 "primary": [r for r in fewer if r["count"] > 0], "secondary": sec, "window_h": window_h, "extra": {}}]
    return []


def _metrics(result: dict, plan: dict, sc: dict, cfg: dict) -> dict:
    s = result["summary"]
    total = s["kg_collected"] + s["uncollected_kg"]
    window = s["shift_min"]
    dur = s["time_to_complete_min"]
    trips = [t for v in result["primary"]["vehicles"] for t in v["trips"]]
    fills = [t.get("fill_pct") for t in trips if t.get("fill_pct") is not None]
    mins = [v["total_min"] for v in result["primary"]["vehicles"] if v["trips"]]
    slack = 100 * (window - dur) / window if window else 0
    rk = cfg["risk"]
    risk = "high" if (s["uncollected_kg"] > 0 or slack < rk["high_if_slack_below_pct"]) else "medium" if slack < rk["medium_if_slack_below_pct"] else "low"
    return {"coverage_pct": round(100 * s["kg_collected"] / total, 1) if total else 100.0, "unserved_kg": s["uncollected_kg"],
            "km": round(result["primary"]["km"] + result["secondary"]["km"], 1), "duration_min": dur, "window_min": window,
            "overtime_min": round(max(0.0, dur - window), 1), "slack_pct": round(slack, 1), "collected_kg": s["kg_collected"],
            "unserved_points": s["uncollected_points"],
            "vehicles": len([v for v in result["primary"]["vehicles"] if v["trips"]]), "trucks": len(result["secondary"]["trucks"]),
            "trips": len(trips), "mean_fill_pct": round(sum(fills) / len(fills)) if fills else None,
            "imbalance_min": round(max(mins) - min(mins), 1) if mins else 0.0, "risk": risk,
            "feasibility": plan["feasibility"], "exceptions": [e["code"] for e in plan["exceptions"]],
            "extra_vehicles_needed": sum(sc["extra"].values())}


def _score(m: dict, cfg: dict) -> float:
    w = cfg["objective"]
    return round(w["unserved_kg"] * m["unserved_kg"] + w["overtime_min"] * m["overtime_min"] + w["km"] * m["km"]
                 + w["vehicles"] * (m["vehicles"] + m["trucks"]) + w["extra_vehicles_needed"] * m["extra_vehicles_needed"]
                 + w["imbalance_min"] * m["imbalance_min"] + w["risk"][m["risk"]], 1)


def _start(con, pilot: str, kind: str, sector: str | None, day: str, trigger: str, note: str | None, cfg: dict) -> str:
    rid = uuid.uuid4().hex
    with con:
        con.execute("INSERT INTO agent_run (id, pilot, kind, sector, service_date, trigger, trigger_note, status, config_version, started_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)", (rid, pilot, kind, sector, day, trigger, note, cfg["version"], _now()))
    return rid


def _finish(con, rid: str, status: str, **cols) -> None:
    cols = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in cols.items()}
    sets = ", ".join(f"{k} = ?" for k in cols)
    with con:
        con.execute(f"UPDATE agent_run SET status = ?, finished_at = ?{', ' + sets if sets else ''} WHERE id = ?",
                    [status, _now(), *cols.values(), rid])


def plan_day(con, pilot: str, sector: str, day: str, trigger: str = "manual", note: str | None = None) -> dict:
    """Run the planning workflow for one sector and day; the result is a recommendation awaiting approval."""
    from backend.routing import twotier
    cfg = config()
    pc = cfg["planning"]
    rid = _start(con, pilot, "plan", sector, day, trigger, note, cfg)
    checks: list = []
    try:
        # 1. Demand.
        D.generate(con, pilot, day, [sector])
        dem = [d for d in D.demands(con, pilot, day, sector, "collection") if d["status"] in ("open", "planned")]
        by_stream = {}
        for d in dem:
            by_stream[d["stream"]] = by_stream.get(d["stream"], 0.0) + (d["quantity_kg"] or 0)
        demand_kg = sum(by_stream.values())
        checks.append(_check("Collection demand", "hard", "pass" if dem else "unknown",
                             f"{len(dem)} demand(s), {demand_kg / 1000:,.2f} t (estimates)" if dem else "Nothing to collect on this day.",
                             kg_by_stream={k: round(v, 1) for k, v in by_stream.items()}))
        if not dem:
            _finish(con, rid, "nothing_to_plan", checks=checks)
            return get(con, pilot, rid)
        # 2. The window: the collection cycle's for the day, else the default.
        plan = C.day_plan(con, pilot, sector, D.weekday(day))
        if plan["scheduled"] and plan["window"]:
            window_h, wsrc = plan["window"]["hours"], f"collection cycle, {plan['window']['start']}-{plan['window']['end']}"
        else:
            window_h, wsrc = pc["default_window_h"], "default window (no collection cycle for the day)"
        checks.append(_check("Collection window", "hard", "pass", f"{window_h:g} h ({wsrc})", window_h=window_h))
        # 3. Places.
        places = F.plan_places(con, pilot, sector)
        missing = [k for k in ("depot", "mrf") if not places[k]]
        checks.append(_check("Depot and MRF", "hard", "fail" if missing else "pass",
                             "Not saved: " + ", ".join(missing) + ". Save them in the route builder." if missing
                             else f"{places['depot']['name']}, {places['mrf']['name']}"))
        if missing:
            _finish(con, rid, "insufficient_data", checks=checks)
            return get(con, pilot, rid)
        stations = [(s["lon"], s["lat"]) for s in places["transfer_stations"]]
        if not stations:
            stations = [(s["lon"], s["lat"]) for s in twotier.suggest_stations(pilot, sector, pc["station_radius_m"])]
            checks.append(_check("Transfer stations", "soft", "warn", f"None saved; {len(stations)} suggested on main roads for this run."))
        else:
            checks.append(_check("Transfer stations", "soft", "pass", f"{len(stations)} saved"))
        # 4. Resources and capacity.
        av = R.available(con, pilot, sector)
        prim, sec, hypothetical = _fleets(av, cfg, demand_kg, window_h * 60, pc["unload_min"])
        if not prim:
            checks.append(_check("Door-to-door vehicles", "hard", "fail", "No door-to-door vehicle is available for this sector."))
            _finish(con, rid, "no_feasible_plan", checks=checks)
            return get(con, pilot, rid)
        checks.append(_check("Vehicles", "hard", "warn" if hypothetical else "pass",
                             ("No vehicles entered in Resources: a hypothetical fleet of " if hypothetical else "Available: ")
                             + ", ".join(f"{r['count']} {r['type'].replace('_', ' ')}" for r in prim + sec), hypothetical=hypothetical))
        bound = capacity_bound(prim, window_h * 60, pc["unload_min"])
        short = demand_kg > bound["max_kg"]
        checks.append(_check("Capacity", "hard", "fail" if short else "pass",
                             f"Demand {demand_kg / 1000:,.2f} t; the most these vehicles could load in {window_h:g} h is "
                             f"{bound['max_kg'] / 1000:,.2f} t" + (f": short by {(demand_kg - bound['max_kg']) / 1000:,.2f} t whatever the routes." if short else "."),
                             demand_kg=round(demand_kg), bound_kg=bound["max_kg"], by_type=bound["by_type"]))
        rate = observed_rate(con, pilot, cfg)
        if rate["kg_per_vehicle_hour"]:
            est = rate["kg_per_vehicle_hour"] * window_h * sum(r["count"] for r in prim)
            checks.append(_check("Capacity from history", "soft", "warn" if demand_kg > est else "pass",
                                 f"Recorded routes collected {rate['kg_per_vehicle_hour']:,.0f} kg per vehicle-hour ({rate['routes']} routes, "
                                 f"{rate['confidence']} confidence): about {est / 1000:,.2f} t in the window.", estimate_kg=round(est)))
        else:
            checks.append(_check("Capacity from history", "soft", "unknown",
                                 f"Insufficient data: {rate['routes']} recorded route(s); the estimate needs {cfg['review']['pattern_min_routes']}."))
        if av["inventory"]["staff"]:
            need = R.crew_needed(prim + sec, av["crew_per_vehicle"])
            have = {"drivers": av["staff"].get("driver", {}).get("count", 0), "collectors": av["staff"].get("waste_collector", {}).get("count", 0)}
            short_crew = {k: round(need[k] - have[k], 1) for k in need if need[k] > have[k]}
            checks.append(_check("Crew", "hard", "fail" if short_crew else "pass",
                                 f"Needed {need['drivers']:g} drivers and {need['collectors']:g} collectors; available {have['drivers']} and {have['collectors']}."))
        else:
            checks.append(_check("Crew", "hard", "unknown", "No staff entered in Resources: crews are not checked."))
        mrf_cap = places["mrf"]["capacity_t_day"]
        checks.append(_check("MRF capacity", "soft", "unknown" if not mrf_cap else "warn" if demand_kg / 1000 > mrf_cap else "pass",
                             "Capacity not recorded." if not mrf_cap else f"{demand_kg / 1000:,.2f} t against {mrf_cap:g} t/day "
                             "(the whole city's deliveries also count)."))
        # 5. Scenarios, optimised and evaluated.
        tried = []

        def run(sc):
            inp = twotier.PlanInput(pilot=pilot, sector=sector, depot=(places["depot"]["lon"], places["depot"]["lat"]),
                                    mrf=(places["mrf"]["lon"], places["mrf"]["lat"]), primary_fleet=sc["primary"], secondary_fleet=sc["secondary"],
                                    stations=stations, truck_depot=(places["truck_yard"]["lon"], places["truck_yard"]["lat"]) if places["truck_yard"] else None,
                                    radius_m=pc["station_radius_m"], shift_h=sc["window_h"], unload_min=pc["unload_min"],
                                    time_limit_s=pc["solver_time_limit_s"], cycle=plan, date=day)
            result = PL.with_basis(twotier.plan(inp), av)
            saved = PL.save(con, pilot, sector, day, {"agent_run": rid, "scenario": sc["key"], "primary_fleet": sc["primary"],
                                                      "secondary_fleet": sc["secondary"], "shift_h": sc["window_h"], "date": day,
                                                      "config_version": cfg["version"]}, result, AGENT)
            m = _metrics(result, saved, sc, cfg)
            t = {**{k: sc[k] for k in ("key", "label", "primary", "secondary", "window_h", "extra")}, "plan_id": saved["id"],
                 "metrics": m, "score": _score(m, cfg), "exceptions": saved["exceptions"]}
            tried.append(t)
            return t

        first = [run(sc) for sc in _first_scenarios(prim, sec, bound, demand_kg, window_h, cfg, hypothetical)]
        if not short:
            for sc in _follow_ups(first[0], prim, sec, window_h, cfg)[: pc["max_scenarios"] - 1]:
                run(sc)
        if short:
            tried.insert(0, {"key": "available", "label": "Fleet as available", "primary": prim, "secondary": sec, "window_h": window_h,
                             "extra": {}, "plan_id": None, "metrics": None, "score": None, "not_run": "Capacity bound below demand: no routing can serve it."})
        rec = _recommend(con, pilot, sector, day, tried, checks, cfg)
        status = "recommended" if rec["plan_id"] and rec["feasible"] else "no_feasible_plan"
        with con:
            for old in con.execute("SELECT id FROM agent_run WHERE pilot = ? AND sector = ? AND service_date = ? AND kind = 'plan'"
                                   " AND status = 'recommended' AND id != ?", (pilot, sector, day, rid)).fetchall():
                con.execute("UPDATE agent_run SET status = 'superseded' WHERE id = ?", (old["id"],))
        _finish(con, rid, status, checks=checks, scenarios=tried, recommendation=rec, plan_id=rec["plan_id"])
    except Exception as err:  # recorded on the run; the scheduler carries on with the next one
        log.exception("Agent planning run %s failed", rid)
        _finish(con, rid, "failed", checks=checks, error=str(err))
    return get(con, pilot, rid)


def _recommend(con, pilot: str, sector: str, day: str, tried: list, checks: list, cfg: dict) -> dict:
    """Pick the plan: feasible and within the resources first, then feasible needing resources, by score.
    The reasons and comparisons are built only from the scenario metrics."""
    ran = [t for t in tried if t["metrics"]]
    feasible = [t for t in ran if t["metrics"]["feasibility"] != "infeasible"]
    base_window = min(x["window_h"] for x in ran) if ran else 0
    within = [t for t in feasible if not t["extra"] and t["window_h"] == base_window]  # no extra vehicles, no longer window
    pool = within or feasible or ran
    best = min(pool, key=lambda t: t["score"]) if pool else None
    if best is None:
        return {"plan_id": None, "feasible": False, "summary": "NO FEASIBLE PLAN: no scenario could be run.", "reasons": [], "alternatives": []}
    m = best["metrics"]
    ok = m["feasibility"] != "infeasible"
    reasons = []
    if ok:
        reasons.append(f"Collects {m['coverage_pct']:g}% of the day's demand within the {m['window_min'] / 60:g} h window "
                       f"({m['duration_min'] / 60:.1f} h, {m['slack_pct']:g}% to spare).")
    else:
        reasons.append(f"No scenario serves all demand within its window; this one comes closest ({m['coverage_pct']:g}% collected, "
                       f"{m['unserved_kg']:,.0f} kg left, {m['overtime_min']:,.0f} min over).")
    if best["extra"]:
        reasons.append("Needs " + ", ".join(f"{n} more {t.replace('_', ' ')}" for t, n in best["extra"].items()) + " than Resources has for the sector.")
    if best.get("window_h") and best["window_h"] > min(x["window_h"] for x in ran):
        reasons.append(f"Needs the collection window extended to {best['window_h']:g} h.")
    reasons.append(f"{m['vehicles']} vehicle(s) and {m['trucks']} truck(s), {m['km']:,.1f} km, average trip fill {m['mean_fill_pct']}%." if m["mean_fill_pct"] is not None
                   else f"{m['vehicles']} vehicle(s) and {m['trucks']} truck(s), {m['km']:,.1f} km.")
    baseline = _baseline(con, pilot, sector, day, best["plan_id"]) or next((t for t in ran if t["key"] == "available" and t is not best), None)
    comparison = None
    if baseline:
        bm = baseline["metrics"]
        comparison = {"against": baseline["label"], "km": round(m["km"] - bm["km"], 1), "duration_min": round(m["duration_min"] - bm["duration_min"], 1),
                      "coverage_pct": round(m["coverage_pct"] - bm["coverage_pct"], 1), "vehicles": m["vehicles"] - bm["vehicles"]}
    alternatives = []
    for t in tried:
        if t is best:
            continue
        if not t["metrics"]:
            why = t.get("not_run", "Not run.")
        elif t["metrics"]["feasibility"] == "infeasible":
            why = f"Infeasible: {t['metrics']['unserved_kg']:,.0f} kg unserved, {t['metrics']['overtime_min']:,.0f} min over the window."
        elif t["extra"] and not best["extra"]:
            why = "Needs vehicles the sector does not have."
        else:
            why = f"Scores worse ({t['score']:,.0f} against {best['score']:,.0f}): " + \
                  f"{t['metrics']['km']:,.1f} km, {t['metrics']['duration_min'] / 60:.1f} h, risk {t['metrics']['risk']}."
        alternatives.append({"label": t["label"], "plan_id": t["plan_id"], "why_not": why, "metrics": t["metrics"]})
    # Demand left in every scenario, even with more vehicles or a longer window, is not a capacity shortfall.
    more = [t for t in ran if t is not best and (t["extra"] or t["window_h"] > base_window)]
    stuck = set(m["unserved_points"])
    for t in more + ([best] if best["extra"] or best["window_h"] > base_window else []):
        stuck &= set(t["metrics"]["unserved_points"])
    if stuck and (more or best["extra"] or best["window_h"] > base_window):
        names = []
        for pid in sorted(stuck)[:5]:
            src, sid = ("gvp", pid[4:]) if pid.startswith("GVP:") else ("bin", pid[4:]) if pid.startswith("BIN:") else ("collection_point", pid)
            row = con.execute("SELECT detail FROM service_demand WHERE pilot = ? AND source_type = ? AND source_id = ? ORDER BY service_date DESC LIMIT 1",
                              (pilot, src, sid)).fetchone()
            names.append((json.loads(row["detail"]).get("label") if row else None) or pid)
        reasons.append(f"{len(stuck)} stop(s) stay uncollected even with more vehicles or a longer window, so this is not a capacity shortfall: "
                       + "; ".join(names) + ". The cause is not established: check access to the stop and its transfer station.")
    risks = [c["detail"] for c in checks if c["status"] in ("warn", "fail", "unknown")]
    risks += [e["reason"] for e in best["exceptions"]]
    if m["risk"] != "low":
        risks.append(f"Risk {m['risk']}: {m['slack_pct']:g}% of the window to spare" + (" and demand left unserved." if m["unserved_kg"] else "."))
    return {"plan_id": best["plan_id"], "scenario": best["key"], "label": best["label"], "feasible": ok,
            "summary": ("Recommended: " if ok else "NO FEASIBLE PLAN. Closest: ") + best["label"],
            "metrics": m, "reasons": reasons, "comparison": comparison, "alternatives": alternatives, "risks": risks,
            "unmet_demand_kg": m["unserved_kg"], "resources_needed": best["extra"],
            "assumptions": ["Quantities are estimates from building use and units (norms.json) until weighed.",
                            "Road speeds and loading rates are the assumptions in reference/vehicles.json.",
                            f"Plan scores use the objective weights of agent settings version {cfg['version']}."]}


def _baseline(con, pilot: str, sector: str, day: str, not_plan: str):
    """The plan already adopted for the day, as the baseline to compare against, if there is one."""
    r = con.execute("SELECT id, summary FROM route_plan WHERE pilot = ? AND sector = ? AND service_date = ? AND status = 'adopted' AND id != ?",
                    (pilot, sector, day, not_plan)).fetchone()
    if not r:
        return None
    s = json.loads(r["summary"])
    total = s["kg_collected"] + s["uncollected_kg"]
    return {"label": "the plan already adopted", "metrics": {"km": s["km"], "duration_min": s["time_to_complete_min"], "vehicles": s["vehicles"],
                                                             "coverage_pct": round(100 * s["kg_collected"] / total, 1) if total else 100.0}}


# ---------- Approval ----------

def decide(con, pilot: str, run_id: str, actor: dict, approve: bool, plan_id: str | None = None,
           accept_exceptions: bool = False, note: str | None = None) -> dict:
    """Approve (adopt the recommended plan, or a chosen alternative) or reject a recommendation."""
    if actor.get("role") not in APPROVERS:
        raise AgentError("Only a planner or an admin can approve or reject the agent's plans.", 403)
    run = get(con, pilot, run_id)
    if run["kind"] != "plan" or run["status"] not in ("recommended", "no_feasible_plan"):
        raise AgentError(f"This run is {run['status']}: there is nothing to decide.", 409)
    if not approve:
        _decided(con, run_id, "rejected", actor, note)
        return get(con, pilot, run_id)
    chosen = plan_id or run["plan_id"]
    allowed = {s["plan_id"] for s in run["scenarios"] if s.get("plan_id")}
    if chosen not in allowed:
        raise AgentError("Choose one of the plans this run made.")
    try:
        PL.adopt(con, pilot, chosen, actor, accept_exceptions=accept_exceptions)
    except PL.PlanError as err:
        raise AgentError(str(err), err.status) from err
    _decided(con, run_id, "approved", actor, note, plan_id=chosen)
    return get(con, pilot, run_id)


def _decided(con, run_id: str, status: str, actor: dict, note: str | None, **cols) -> None:
    sets = {"status": status, "decided_by": actor.get("name"), "decided_role": actor.get("role"), "decided_at": _now(), "decision_note": note, **cols}
    with con:
        con.execute(f"UPDATE agent_run SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), run_id])


# ---------- Review: planned vs actual, causes, patterns ----------

def review_day(con, pilot: str, day: str, trigger: str = "manual") -> dict:
    """After the day: each recorded route's deviations and their cause where the records support it, missed work by
    reason, routes not recorded, and patterns over the operational history. Proposals only; nothing is changed."""
    cfg = config()
    rc = cfg["review"]
    rid = _start(con, pilot, "review", None, day, trigger, None, cfg)
    try:
        recs = {r["ref"]: r for r in O.records(con, pilot, day, subject="route")}
        routes, unrecorded = [], []
        for p in PL.adopted(con, pilot, day):
            for r in p["routes"]:
                rec = recs.get(r["id"])
                if not rec:
                    unrecorded.append({"sector": p["sector"], "route": r["label"]})
                    continue
                dev = {}
                for k, a, pl in (("km", rec["actual_km"], rec["planned_km"]), ("min", rec["actual_min"], rec["planned_min"]),
                                 ("kg", rec["actual_kg"], rec["planned_kg"])):
                    if a is not None and pl:
                        dev[k] = round(100 * (a - pl) / pl, 1)
                big = {k: v for k, v in dev.items() if abs(v) >= rc["deviation_pct"]}
                cause, evidence = _cause(rec, dev, rc["deviation_pct"])
                routes.append({"sector": p["sector"], "route": r["label"], "vehicle_type": r["vehicle_type"], "outcome": rec["outcome"],
                               "deviation_pct": dev, "significant": big, "cause": cause, "evidence": evidence})
        missed = {}
        for r in O.records(con, pilot, day, subject="demand"):
            if r["outcome"] != "done":
                missed[r["reason"] or "unknown"] = missed.get(r["reason"] or "unknown", 0) + 1
        findings = {"routes": routes, "unrecorded_routes": unrecorded, "missed_by_reason": missed,
                    "patterns": patterns(con, pilot, cfg),
                    "data": "INSUFFICIENT DATA: no routes recorded for this day." if not routes and not missed else None}
        _finish(con, rid, "reviewed", findings=findings)
    except Exception as err:
        log.exception("Agent review run %s failed", rid)
        _finish(con, rid, "failed", error=str(err))
    return get(con, pilot, rid)


def _cause(rec: dict, dev: dict, th: float) -> tuple[str, str]:
    """The cause of a route's deviation only as far as its own record supports it; UNKNOWN otherwise."""
    if rec["reason"]:
        return rec["reason"].replace("_", " "), "recorded by " + (rec["recorded_by"] or "the recorder")
    t, k, w = dev.get("min", 0), dev.get("km", 0), dev.get("kg", 0)
    if t >= th and w >= th and k < th:
        return "more waste than expected", f"waste {w:+g}% while distance was {k:+g}%"
    if t >= th and k >= th:
        return "longer distance driven than planned", f"distance {k:+g}% and time {t:+g}%"
    if t >= th:
        return "UNKNOWN", f"time {t:+g}% with distance and waste close to plan: slower travel or loading, not established"
    return ("as planned", "within the deviation threshold") if all(abs(v) < th for v in dev.values()) else ("UNKNOWN", "no recorded reason")


def patterns(con, pilot: str, cfg: dict) -> list[dict]:
    """Systematic errors over the operational history: by vehicle type, mean deviation of recorded routes from
    plan, with the number of routes behind it. A pattern is a proposal for review, never applied by the agent."""
    rc = cfg["review"]
    since = (Date.today() - timedelta(days=rc["lookback_days"])).isoformat()
    by = {}
    for r in O.records(con, pilot, subject="route"):
        if r["service_date"] < since or r["kind"] != "primary":
            continue
        b = by.setdefault(r["vehicle_type"], {"min": [], "km": [], "kg": []})
        for k, a, p in (("min", r["actual_min"], r["planned_min"]), ("km", r["actual_km"], r["planned_km"]), ("kg", r["actual_kg"], r["planned_kg"])):
            if a is not None and p:
                b[k].append(100 * (a - p) / p)
    out = []
    what = {"min": ("time", "service and travel time estimates (reference/vehicles.json)"), "km": ("distance", "road network and routing"),
            "kg": ("waste", "generation norms (backend/buildings/norms.json)")}
    for vt, b in by.items():
        for k, xs in b.items():
            if len(xs) >= rc["pattern_min_routes"]:
                mean = sum(xs) / len(xs)
                if abs(mean) >= rc["pattern_mean_error_pct"]:
                    out.append({"vehicle_type": vt, "measure": what[k][0], "mean_error_pct": round(mean, 1), "routes": len(xs),
                                "confidence": "low" if len(xs) < 20 else "medium",
                                "proposal": f"{vt.replace('_', ' ')} routes run {mean:+.0f}% {what[k][0]} against plan over {len(xs)} routes: "
                                            f"review the {what[k][1]}. Back-test before changing any estimate."})
    return out


# ---------- Reading ----------

def get(con, pilot: str, run_id: str) -> dict:
    r = con.execute("SELECT * FROM agent_run WHERE pilot = ? AND id = ?", (pilot, run_id)).fetchone()
    if r is None:
        raise AgentError("Unknown agent run.", 404)
    d = dict(r)
    for k in ("checks", "scenarios", "recommendation", "findings"):
        d[k] = json.loads(d[k]) if d[k] else ([] if k in ("checks", "scenarios") else None)
    return d


def runs(con, pilot: str, day: str | None = None, status: str | None = None, limit: int = 60) -> list[dict]:
    q, args = "SELECT id FROM agent_run WHERE pilot = ?", [pilot]
    if day:
        q += " AND service_date = ?"
        args.append(day)
    if status:
        q += " AND status = ?"
        args.append(status)
    return [get(con, pilot, r["id"]) for r in con.execute(q + " ORDER BY started_at DESC LIMIT ?", [*args, limit]).fetchall()]


# ---------- Triggers: schedule, conditions, manual ----------

def enqueue(con, pilot: str, kind: str, sector: str | None, day: str, trigger: str, note: str | None = None) -> bool:
    """Queue a run unless the same one is already waiting."""
    if con.execute("SELECT 1 FROM agent_trigger WHERE pilot = ? AND kind = ? AND IFNULL(sector, '') = IFNULL(?, '') AND service_date = ? AND taken_at IS NULL",
                   (pilot, kind, sector, day)).fetchone():
        return False
    with con:
        con.execute("INSERT INTO agent_trigger (pilot, kind, sector, service_date, trigger, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pilot, kind, sector, day, trigger, note, _now()))
    return True


def conditions(con, pilot: str, cfg: dict) -> list[dict]:
    """Adopted plans for today and tomorrow that no longer hold: an assigned vehicle that is not available any more,
    or new collection demand above the threshold that the plan does not cover."""
    rp = cfg["replanning"]
    found = []
    for day in (Date.today().isoformat(), (Date.today() + timedelta(days=1)).isoformat()):
        for p in PL.adopted(con, pilot, day):
            if rp["vehicle_lost"]:
                ids = [r["vehicle_id"] for r in p["routes"] if r["vehicle_id"]]
                lost = [v for v in R.items(con, "vehicle", pilot) if v["id"] in ids and v["status"] not in R.PLANNABLE]
                if lost:
                    found.append({"sector": p["sector"], "day": day, "note": "Vehicle no longer available: " + ", ".join(v["registration"] or v["id"][:8] for v in lost)})
            # Only demand that appeared after adoption: demand the planner knowingly left unserved is not news.
            new_kg = sum(d["quantity_kg"] or 0 for d in D.demands(con, pilot, day, p["sector"], "collection")
                         if d["status"] == "open" and p["adopted_at"] and d["created_at"] > p["adopted_at"])
            if new_kg >= rp["new_collection_demand_kg"]:
                found.append({"sector": p["sector"], "day": day, "note": f"{new_kg:,.0f} kg of new collection demand since the plan was adopted"})
            for f in found:
                f.setdefault("adopted_at", p["adopted_at"])
    return found


def due(now: datetime, state: dict, cfg: dict) -> list[str]:
    """Which scheduled jobs are due at local time `now`, given when each last ran (state: job -> ISO date or time)."""
    s = cfg["schedule"]
    if not s.get("enabled"):
        return []
    out = []
    today = now.date().isoformat()
    for job, at in (("plan_next_day", s["plan_next_day_at"]), ("review_day", s["review_day_at"])):
        if now.strftime("%H:%M") >= at and state.get(job) != today:
            out.append(job)
    last = state.get("conditions")
    if not last or (now - datetime.fromisoformat(last)).total_seconds() >= s["check_conditions_every_min"] * 60:
        out.append("conditions")
    return out


def _state(con) -> dict:
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM agent_state")}


def _set_state(con, key: str, value: str) -> None:
    with con:
        con.execute("INSERT OR REPLACE INTO agent_state (key, value) VALUES (?, ?)", (key, value))


def tick(pilot: str, sectors: list[str], now: datetime | None = None, db_path: Path | None = None) -> dict:
    """One pass of the agent loop: queue what the schedule and the conditions call for, then work the queue."""
    cfg = config()
    now = now or datetime.now()
    con = connect(db_path)
    try:
        st = _state(con)
        queued = 0
        for job in due(now, st, cfg):
            if job == "plan_next_day":
                day = (now.date() + timedelta(days=1)).isoformat()
                for s in sectors:
                    queued += enqueue(con, pilot, "plan", s, day, "schedule", "Next day's plan")
                _set_state(con, job, now.date().isoformat())
            elif job == "review_day":
                queued += enqueue(con, pilot, "review", None, now.date().isoformat(), "schedule", "End of day review")
                _set_state(con, job, now.date().isoformat())
            elif job == "conditions":
                for c in conditions(con, pilot, cfg):
                    # Once per adopted plan: a condition run already made since adoption (approved or not) is the answer.
                    if not con.execute("SELECT 1 FROM agent_run WHERE pilot = ? AND sector = ? AND service_date = ? AND kind = 'plan'"
                                       " AND trigger = 'condition' AND started_at >= ?", (pilot, c["sector"], c["day"], c["adopted_at"] or "")).fetchone():
                        queued += enqueue(con, pilot, "plan", c["sector"], c["day"], "condition", c["note"])
                _set_state(con, job, now.isoformat(timespec="seconds"))
        done = work(con, pilot)
        return {"queued": queued, "ran": done}
    finally:
        con.close()


_WORK_LOCK = threading.Lock()
_LOOP = {"running": False}  # whether this server process runs the scheduled loop


def work(con, pilot: str, limit: int = 50) -> list[str]:
    """Run queued triggers one at a time (one worker at a time, so runs never overlap)."""
    if not _WORK_LOCK.acquire(blocking=False):
        return []
    try:
        ran = []
        for _ in range(limit):
            t = con.execute("SELECT * FROM agent_trigger WHERE pilot = ? AND taken_at IS NULL ORDER BY id LIMIT 1", (pilot,)).fetchone()
            if t is None:
                break
            with con:
                con.execute("UPDATE agent_trigger SET taken_at = ? WHERE id = ?", (_now(), t["id"]))
            r = (plan_day(con, pilot, t["sector"], t["service_date"], t["trigger"], t["note"]) if t["kind"] == "plan"
                 else review_day(con, pilot, t["service_date"], t["trigger"]))
            ran.append(r["id"])
        return ran
    finally:
        _WORK_LOCK.release()


def status(con, pilot: str) -> dict:
    cfg = config()
    return {"config": cfg, "state": _state(con), "loop_running": _LOOP["running"],
            "queue": [dict(r) for r in con.execute("SELECT * FROM agent_trigger WHERE pilot = ? AND taken_at IS NULL ORDER BY id", (pilot,))],
            "pending_approval": con.execute("SELECT COUNT(*) FROM agent_run WHERE pilot = ? AND status = 'recommended'", (pilot,)).fetchone()[0]}


def start_scheduler(pilot: str, sectors_fn, interval_s: int = 60) -> threading.Thread | None:
    """The background loop: a tick every minute while the app runs (when the schedule is enabled)."""
    if not config()["schedule"].get("enabled"):
        return None
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_s):
            try:
                tick(pilot, sectors_fn())
            except Exception:  # logged; the loop goes on
                log.exception("Agent tick failed")
    th = threading.Thread(target=loop, name="cityloom-agent", daemon=True)
    th.stop = stop  # type: ignore[attr-defined]
    th.start()
    _LOOP["running"] = True
    return th
