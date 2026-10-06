"""Command centre (read model): the planner's first screen, built from the connected records.

Not a dashboard of activity counts but an exception and decision view, answering:
  How is the city doing?        service level over the last 7 days (backend.performance)
  Where are the failures?       open planning alerts, recurring and critical GVPs, bins full or
                                overflowing, sectors below the service target, plans short of capacity
  What needs attention today?   today's demands by sector and status, high-priority ones still open,
                                sectors with no adopted route or work plan, and those plans' exceptions
  Why?                          per sector: today's demand, the plan's feasibility and its reasons, the
                                cleaning hours needed against those available, and its alerts

Nothing is stored here and nothing is worked out twice: every figure comes from the module that owns
it (demands, plans, work plans, operations, performance, Clean City, GVPs, resources).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date as Date, timedelta

from backend import cleancity as CC
from backend import demand as D
from backend import performance as PERF

SERVICE_TARGET_PCT = 95  # a planning target for "below target", not a regulation


def _adopted(con, table: str, pilot: str, day: str) -> dict:
    try:
        return {r["sector"]: {"id": r["id"], "feasibility": r["feasibility"], "exceptions": json.loads(r["exceptions"])}
                for r in con.execute(f"SELECT id, sector, feasibility, exceptions FROM {table} WHERE pilot = ? AND service_date = ? AND status = 'adopted'",
                                     (pilot, day))}
    except sqlite3.OperationalError:  # a table made when its module is first used
        return {}


def _agent_pending(con, pilot: str) -> int:
    try:
        return con.execute("SELECT COUNT(*) FROM agent_run WHERE pilot = ? AND status = 'recommended'", (pilot,)).fetchone()[0]
    except sqlite3.OperationalError:  # the agent's tables are made when it first runs
        return 0


def overview(con, pilot: str, sectors: list[str], day: str | None = None, survey_db=None) -> dict:
    from backend.survey import gvp
    day = day or D.today()
    start = (Date.fromisoformat(day) - timedelta(days=6)).isoformat()
    # Today's demands follow from the schedules: make them for sectors that have none yet (idempotent).
    missing = [s for s in sectors if not con.execute("SELECT 1 FROM service_demand WHERE pilot = ? AND sector = ? AND service_date = ? LIMIT 1",
                                                     (pilot, s, day)).fetchone()]
    if missing:
        D.generate(con, pilot, day, missing, survey_db=survey_db)

    # How is the city doing: delivered over recorded work, collection by kg and cleaning by metres, last 7 days.
    perf = PERF.service(con, pilot, start, day)
    level = {}
    for unit, work in (("kg", "collection"), ("m", "cleaning")):
        rows = [r for r in perf["rows"] if r["work"] == work]
        got, rec = sum(r["delivered"] for r in rows), sum(r["delivered"] + r["missed"] for r in rows)
        level[work] = {"pct": round(100 * got / rec, 1) if rec else None, "recorded_pct": round(100 * rec / sum(r["required"] for r in rows), 1) if rows and sum(r["required"] for r in rows) else None}
    by_sector_level = {}
    for r in perf["rows"]:
        if r["service_level_pct"] is not None:
            by_sector_level.setdefault(r["sector"], {})[r["work"]] = r["service_level_pct"]

    # Failures.
    alerts = PERF.alerts(con, pilot, "open")
    gvps = gvp.list_gvps(pilot, None, survey_db)
    open_gvps = [g for g in gvps if g["status"] in gvp.OPEN]
    bins = [b for b in CC.bins(con, pilot) if b["status"] in ("full", "overflowing")]
    below = sorted({s for s, v in by_sector_level.items() if any(x < SERVICE_TARGET_PCT for x in v.values())})

    # Today, and why, per sector.
    routes, works = _adopted(con, "route_plan", pilot, day), _adopted(con, "work_plan", pilot, day)
    sectors_out = []
    high_open = 0
    for s in sectors:
        ds = D.demands(con, pilot, day, s, None, D.STATUSES)
        ds = [d for d in ds if d["status"] != "cancelled"]
        coll = [d for d in ds if d["kind"] == "collection"]
        clean = [d for d in ds if d["kind"] == "cleaning"]
        # Urgent: high and critical GVPs (to clear or to collect) and bins that need emptying; routine
        # sweeping of busy streets has a high street priority but is not urgent.
        high_open += sum(1 for d in ds if d["status"] == "open" and ((d["source_type"] == "gvp" and d["priority"] >= 3) or d["source_type"] == "bin"))
        wl = CC.workload(con, pilot, s)
        status = {k: sum(1 for d in ds if d["status"] == k) for k in ("open", "planned", "done", "missed")}
        why = []
        if not ds:
            why.append("No demands made for today yet: plan the day in the route builder or make a work plan.")
        if coll and s not in routes:
            why.append("No adopted route plan for today.")
        if clean and s not in works:
            why.append("No adopted work plan for today's cleaning.")
        for p, label in ((routes.get(s), "Route plan"), (works.get(s), "Work plan")):
            if p:
                why += [f"{label}: {e['reason']}" for e in p["exceptions"] if e.get("blocking")]
        if wl["staff_known"] and wl["gap_hours"] < 0:
            why.append(f"Cleaning needs {wl['required_hours']:,.0f} worker-hours a day; {wl['available_hours']:,.0f} are available.")
        sectors_out.append({
            "sector": s, "collection_t": round(sum(d["quantity_kg"] or 0 for d in coll) / 1000, 2), "collection_demands": len(coll),
            "sweeping_km": round(sum(d["length_m"] or 0 for d in clean if d["source_type"] == "street") / 1000, 1),
            "gvps_to_clear": sum(1 for d in clean if d["source_type"] == "gvp"), "status": status,
            "route_plan": routes.get(s, {}).get("feasibility"), "work_plan": works.get(s, {}).get("feasibility"),
            "service_level": by_sector_level.get(s, {}),
            "cleaning_hours": {"required": wl["required_hours"], "available": wl["available_hours"] if wl["staff_known"] else None},
            "open_gvps": sum(1 for g in open_gvps if g["sector"] == s), "alerts": sum(1 for a in alerts if a["sector"] == s),
            "why": why})
    staff_short = [x for x in sectors_out if x["cleaning_hours"]["available"] is not None and x["cleaning_hours"]["available"] < x["cleaning_hours"]["required"]]
    need = sum(x["cleaning_hours"]["required"] for x in staff_short)
    have = sum(x["cleaning_hours"]["available"] for x in staff_short)
    return {
        "date": day, "target_pct": SERVICE_TARGET_PCT,
        "city": {"service_level": level, "period": {"from": start, "to": day}},
        "failures": {"alerts": len(alerts), "alerts_by_kind": {k: sum(1 for a in alerts if a["kind"] == k) for k in PERF.ALERT_KINDS},
                     "recurring_gvps": sum(1 for g in gvps if g["recurrences"] >= 2), "critical_gvps": sum(1 for g in open_gvps if g["severity"] == "critical"),
                     "bins_full_or_overflowing": len(bins), "sectors_below_target": below,
                     "plans_short": sorted({s for s, p in list(routes.items()) + list(works.items()) if p["feasibility"] == "infeasible"}),
                     "workforce_short_pct": round(100 * (need - have) / need, 1) if need else 0.0},
        "today": {"urgent_open": high_open, "open_gvps": len(open_gvps),
                  "missed": sum(x["status"]["missed"] for x in sectors_out),
                  "sectors_without_route_plan": [x["sector"] for x in sectors_out if x["collection_demands"] and not x["route_plan"]]},
        "sectors": sectors_out,
        "top_alerts": alerts[:6],
        "agent_pending": _agent_pending(con, pilot),
    }
