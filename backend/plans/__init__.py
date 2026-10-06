"""Route plans (data/ops.db): what the route optimiser produced, kept as records.

The optimiser (backend.routing.twotier) takes a day's collection demands, the vehicles and the
places, and returns routes. Here each run becomes:

  route_plan   one run for a sector and date: its inputs, summary, feasibility and exceptions
  route        one vehicle's work in the plan (door-to-door vehicle or truck), with the vehicle and
               crew assigned from Resources when the plan is adopted
  route_stop   the collection points (and transfer-station unloads) of each trip, in order

Feasibility follows the route optimiser contract: a plan that leaves demand unserved, runs a vehicle
past its shift or leaves waste at a transfer station is not quietly returned as a good plan. It is
marked infeasible with the reasons and the actions that would fix it. Missing crew or a hypothetical
fleet are warnings.

Adopting a plan (planner) makes it the plan for that sector and date: the collection demands its
routes serve become `planned`, a previously adopted plan is superseded (its demands not in the new
plan go back to `open`), and vehicles and crews are assigned from what Resources has available,
skipping any already assigned that day. A vehicle's current route is derived from these records,
not stored on the vehicle. Plans are never deleted; status changes go to route_plan_log.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import demand as D
from backend import resources

STATUSES = ("draft", "adopted", "superseded")
FEASIBILITY = ("feasible", "feasible_with_warnings", "infeasible")
PLANNERS = ("planner", "admin")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS route_plan (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    sector TEXT NOT NULL,
    service_date TEXT,                        -- NULL: a what-if plan (every stream, one day's waste)
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN {STATUSES}),
    feasibility TEXT NOT NULL CHECK (feasibility IN {FEASIBILITY}),
    exceptions TEXT NOT NULL DEFAULT '[]',    -- JSON
    inputs TEXT NOT NULL,                     -- JSON: fleet, places, streams, shift
    summary TEXT NOT NULL,                    -- JSON: points, kg, km, minutes, unserved
    created_at TEXT NOT NULL, created_by TEXT,
    adopted_at TEXT, adopted_by TEXT
);
CREATE INDEX IF NOT EXISTS route_plan_day ON route_plan (pilot, sector, service_date, status);
CREATE TABLE IF NOT EXISTS route (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES route_plan(id),
    tier TEXT NOT NULL CHECK (tier IN ('primary', 'secondary')),
    label TEXT NOT NULL,                      -- the optimiser's name, e.g. "E-loader three-wheeler 2"
    vehicle_type TEXT NOT NULL,
    vehicle_id TEXT,                          -- assigned on adoption (resources.vehicle)
    crew TEXT NOT NULL DEFAULT '[]',          -- JSON: staff ids assigned on adoption
    trips INTEGER NOT NULL, km REAL NOT NULL, minutes REAL NOT NULL, kg REAL NOT NULL,
    within_shift INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS route_by_plan ON route (plan_id);
CREATE TABLE IF NOT EXISTS route_stop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id TEXT NOT NULL REFERENCES route(id),
    seq INTEGER NOT NULL,
    trip TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('collect', 'unload', 'load', 'deliver')),
    point_id TEXT,                            -- collection point (street run, BIN:..., GVP:...) or station id
    kg REAL
);
CREATE INDEX IF NOT EXISTS route_stop_by_route ON route_stop (route_id);
CREATE TABLE IF NOT EXISTS route_plan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS route_plan_log_no_update BEFORE UPDATE ON route_plan_log
BEGIN SELECT RAISE(ABORT, 'route_plan_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS route_plan_log_no_delete BEFORE DELETE ON route_plan_log
BEGIN SELECT RAISE(ABORT, 'route_plan_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS route_plan_no_delete BEFORE DELETE ON route_plan
BEGIN SELECT RAISE(ABORT, 'route plans are superseded, never deleted'); END;
"""


class PlanError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = D.connect(db_path)
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(con, plan_id: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO route_plan_log (plan_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                (plan_id, _now(), actor.get("name"), actor.get("role"), json.dumps(change)))


def base_point(point_id: str) -> str:
    """A collection point id without the '#n' suffix the optimiser adds when it splits a large point."""
    return str(point_id).split("#")[0]


def demand_key(point_id: str) -> tuple[str, str]:
    """(source_type, source_id) of the demand a route collection point serves."""
    pid = base_point(point_id)
    if pid.startswith("GVP:"):
        return "gvp", pid[4:]
    if pid.startswith("BIN:"):
        return "bin", pid[4:]
    return "collection_point", pid


def with_basis(result: dict, available: dict) -> dict:
    """Say whether the plan's vehicles and crews were checked against the inventory or are hypothetical,
    and how many drivers and collectors the plan's vehicles need (available: resources.available)."""
    inv = available["inventory"]
    result["fleet_basis"] = "inventory" if inv["vehicle"] else "hypothetical"
    result["crew_basis"] = "inventory" if inv["staff"] else "hypothetical"
    vehicles = [{"type": v["type"], "count": 1} for v in result["primary"]["vehicles"] + result["secondary"]["trucks"]]
    result["crew"] = {"needed": resources.crew_needed(vehicles, available["crew_per_vehicle"]),
                      "available": {"drivers": available["staff"].get("driver", {}).get("count", 0),
                                    "collectors": available["staff"].get("waste_collector", {}).get("count", 0)}}
    return result


# ---------- Feasibility: the route optimiser contract ----------

def assess(result: dict) -> tuple[str, list[dict]]:
    """Feasibility and the exceptions of an optimiser result, each with its reason and actions."""
    s = result["summary"]
    shift_h = s["shift_min"] / 60
    out = []
    if s["uncollected_kg"] > 0:
        total = s["kg_collected"] + s["uncollected_kg"]
        out.append({"code": "unserved_demand", "blocking": True,
                    "kg": s["uncollected_kg"], "points": len(s["uncollected_points"]),
                    "share_pct": round(100 * s["uncollected_kg"] / total, 1) if total else 0,
                    "reason": f"{s['uncollected_kg']:,.0f} kg at {len(s['uncollected_points'])} point(s) cannot be collected "
                              f"with these vehicles within the {shift_h:g} h window (or the streets are too narrow for them).",
                    "actions": ["Add vehicles (Suggest fleet finds how many)", "Extend the collection window",
                                "Split the sector's collection over two windows", "Add a narrower vehicle for tight streets"]})
    if s["vehicles_over_shift"]:
        out.append({"code": "over_shift", "blocking": True, "vehicles": s["vehicles_over_shift"],
                    "reason": f"{len(s['vehicles_over_shift'])} vehicle(s) work past the {shift_h:g} h window.",
                    "actions": ["Add vehicles", "Extend the collection window", "Add a transfer station closer to the far points"]})
    if s["left_at_stations_kg"] > 0:
        out.append({"code": "left_at_stations", "blocking": True, "kg": s["left_at_stations_kg"],
                    "reason": f"{s['left_at_stations_kg']:,.0f} kg stays at transfer stations: the trucks cannot haul it all to the MRF in time.",
                    "actions": ["Add trucks", "Use larger trucks", "Extend the trucks' window"]})
    over = [x["id"] for x in result.get("stations", []) if x.get("kg", 0) > result.get("station_capacity_kg", float("inf"))]
    if over:
        out.append({"code": "stations_over_capacity", "blocking": False, "stations": over,
                    "reason": f"{', '.join(over)} receive more than a transfer station holds.",
                    "actions": ["Add a transfer station near them", "Move a station to share the load"]})
    crew = result.get("crew")
    if crew and result.get("crew_basis") == "inventory":
        short = {k: crew["needed"][k] - crew["available"][k] for k in ("drivers", "collectors")
                 if crew["needed"][k] > crew["available"][k]}
        if short:
            out.append({"code": "crew_short", "blocking": False, "short": short,
                        "reason": "Not enough people to crew these vehicles: " + ", ".join(f"{v:g} more {k}" for k, v in short.items()) + ".",
                        "actions": ["Add staff in Resources", "Plan with fewer, larger vehicles"]})
    if result.get("fleet_basis") == "hypothetical":
        out.append({"code": "hypothetical_fleet", "blocking": False,
                    "reason": "No vehicles are entered in Resources, so this fleet may not exist.",
                    "actions": ["Enter the vehicles in Resources"]})
    if any(x["blocking"] for x in out):
        return "infeasible", out
    return ("feasible_with_warnings" if out else "feasible"), out


# ---------- Saving ----------

def save(con, pilot: str, sector: str, day: str | None, inputs: dict, result: dict, actor: dict) -> dict:
    """Store an optimiser result as a draft plan with its routes and stops. Returns {id, feasibility, exceptions}."""
    feasibility, exceptions = assess(result)
    pid = uuid.uuid4().hex
    s = result["summary"]
    summary = {"points": s["points"], "points_served": s["points_served"], "kg_collected": s["kg_collected"],
               "uncollected_kg": s["uncollected_kg"], "uncollected_points": s["uncollected_points"],
               "time_to_complete_min": s["time_to_complete_min"], "shift_min": s["shift_min"],
               "km": round(result["primary"]["km"] + result["secondary"]["km"], 1),
               "vehicles": len(result["primary"]["vehicles"]), "trucks": len(result["secondary"]["trucks"])}
    with con:
        con.execute("INSERT INTO route_plan (id, pilot, sector, service_date, feasibility, exceptions, inputs, summary, created_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, pilot, sector, day, feasibility, json.dumps(exceptions), json.dumps(inputs), json.dumps(summary),
                     _now(), actor.get("name")))
        for v in result["primary"]["vehicles"]:
            if not v["trips"]:
                continue
            rid = uuid.uuid4().hex
            con.execute("INSERT INTO route (id, plan_id, tier, label, vehicle_type, trips, km, minutes, kg, within_shift)"
                        " VALUES (?, ?, 'primary', ?, ?, ?, ?, ?, ?, ?)",
                        (rid, pid, v["id"], v["type"], len(v["trips"]), v["km"], v["total_min"],
                         round(sum(t["kg"] for t in v["trips"]), 1), int(v["within_shift"])))
            seq = 0
            for t in v["trips"]:
                for point in t["point_ids"]:
                    seq += 1
                    con.execute("INSERT INTO route_stop (route_id, seq, trip, kind, point_id) VALUES (?, ?, ?, 'collect', ?)",
                                (rid, seq, t["trip_id"], point))
                seq += 1
                con.execute("INSERT INTO route_stop (route_id, seq, trip, kind, point_id, kg) VALUES (?, ?, ?, 'unload', ?, ?)",
                            (rid, seq, t["trip_id"], t["station"], t["kg"]))
        for tk in result["secondary"]["trucks"]:
            if not tk["trips"]:
                continue
            rid = uuid.uuid4().hex
            con.execute("INSERT INTO route (id, plan_id, tier, label, vehicle_type, trips, km, minutes, kg, within_shift)"
                        " VALUES (?, ?, 'secondary', ?, ?, ?, ?, ?, ?, ?)",
                        (rid, pid, tk["id"], tk["type"], len(tk["trips"]), tk["km"], tk["total_min"],
                         round(sum(t.get("kg", 0) for t in tk["trips"]), 1), int(tk["total_min"] <= s["shift_min"])))
            for i, t in enumerate(tk["trips"], start=1):
                for st in t.get("stations") or [t.get("station")]:
                    if st:
                        con.execute("INSERT INTO route_stop (route_id, seq, trip, kind, point_id) VALUES (?, ?, ?, 'load', ?)",
                                    (rid, i, f"H{i}", st if isinstance(st, str) else st.get("id")))
                con.execute("INSERT INTO route_stop (route_id, seq, trip, kind, point_id, kg) VALUES (?, ?, ?, 'deliver', 'MRF', ?)",
                            (rid, i, f"H{i}", t.get("kg")))
        _log(con, pid, {"created": {"feasibility": feasibility, "date": day}}, actor)
    return {"id": pid, "feasibility": feasibility, "exceptions": exceptions}


# ---------- Reading ----------

def _plan_row(r) -> dict:
    d = dict(r)
    for k in ("exceptions", "inputs", "summary"):
        d[k] = json.loads(d[k])
    return d


def get(con, pilot: str, plan_id: str, stops: bool = True) -> dict:
    r = con.execute("SELECT * FROM route_plan WHERE pilot = ? AND id = ?", (pilot, plan_id)).fetchone()
    if r is None:
        raise PlanError("Unknown route plan.", 404)
    p = _plan_row(r)
    p["routes"] = []
    for rt in con.execute("SELECT * FROM route WHERE plan_id = ? ORDER BY tier, label", (plan_id,)):
        x = dict(rt)
        x["crew"] = json.loads(x["crew"])
        x["within_shift"] = bool(x["within_shift"])
        if stops:
            x["stops"] = [dict(s) for s in con.execute("SELECT seq, trip, kind, point_id, kg FROM route_stop WHERE route_id = ? ORDER BY seq, id", (x["id"],))]
        p["routes"].append(x)
    p["history"] = [{**dict(h), "change": json.loads(h["change"])} for h in
                    con.execute("SELECT at, user_name, user_role, change FROM route_plan_log WHERE plan_id = ? ORDER BY id", (plan_id,))]
    return p


def plans(con, pilot: str, sector: str | None = None, day: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM route_plan WHERE pilot = ?", [pilot]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    if day:
        q += " AND service_date = ?"
        args.append(day)
    return [_plan_row(r) for r in con.execute(q + " ORDER BY created_at DESC LIMIT 200", args)]


def adopted(con, pilot: str, day: str, sector: str | None = None) -> list[dict]:
    """The adopted plans of a day (one per sector at most), with routes and stops."""
    q, args = "SELECT id FROM route_plan WHERE pilot = ? AND service_date = ? AND status = 'adopted'", [pilot, day]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    return [get(con, pilot, r["id"]) for r in con.execute(q + " ORDER BY sector", args)]


def served_points(plan: dict) -> set[str]:
    return {base_point(s["point_id"]) for r in plan["routes"] if r["tier"] == "primary"
            for s in r["stops"] if s["kind"] == "collect"}


# ---------- Adopting ----------

def _assign(con, pilot: str, sector: str, day: str, plan: dict) -> list[dict]:
    """Give each route a vehicle of its type and its crew from what Resources has for the sector,
    skipping vehicles and people already on another adopted route that day."""
    busy_v, busy_s = set(), set()
    for other in adopted(con, pilot, day):
        if other["id"] == plan["id"]:
            continue
        for r in other["routes"]:
            if r["vehicle_id"]:
                busy_v.add(r["vehicle_id"])
            busy_s.update(r["crew"])
    vehicles = [v for v in resources.items(con, "vehicle", pilot, sector) if v["status"] in resources.PLANNABLE and v["id"] not in busy_v]
    staff = [s for s in resources.items(con, "staff", pilot, sector) if s["status"] in resources.PLANNABLE and s["id"] not in busy_s]
    av = resources.available(con, pilot, sector)
    notes = []
    for r in plan["routes"]:
        v = next((x for x in vehicles if x["type"] == r["vehicle_type"]), None)
        if v:
            vehicles.remove(v)
        crew_need = av["crew_per_vehicle"].get(r["vehicle_type"], {"drivers": 1, "collectors": 1})
        crew = []
        for role, n in (("driver", crew_need["drivers"]), ("waste_collector", crew_need["collectors"])):
            for _ in range(int(round(n))):
                s = next((x for x in staff if x["role"] == role), None)
                if s:
                    staff.remove(s)
                    crew.append(s["id"])
                else:
                    notes.append(f"No {role.replace('_', ' ')} free for {r['label']}.")
        if not v:
            notes.append(f"No {r['vehicle_type'].replace('_', ' ')} free for {r['label']}.")
        con.execute("UPDATE route SET vehicle_id = ?, crew = ? WHERE id = ?", (v["id"] if v else None, json.dumps(crew), r["id"]))
    return notes


def adopt(con, pilot: str, plan_id: str, actor: dict, accept_exceptions: bool = False) -> dict:
    """Make a draft plan the sector's plan for its date. An infeasible plan needs accept_exceptions."""
    if actor.get("role") not in PLANNERS:
        raise PlanError("Only a planner or an admin can adopt a route plan.", 403)
    plan = get(con, pilot, plan_id)
    if plan["status"] != "draft":
        raise PlanError(f"This plan is {plan['status']}; only a draft can be adopted.", 409)
    if not plan["service_date"]:
        raise PlanError("A what-if plan cannot be adopted: plan a date.", 409)
    if plan["feasibility"] == "infeasible" and not accept_exceptions:
        raise PlanError("This plan is infeasible: some demand is left unserved or a vehicle runs past its window. "
                        "Fix the exceptions, or adopt it knowingly (accept_exceptions).", 409)
    day, sector = plan["service_date"], plan["sector"]
    served = served_points(plan)
    with con:
        for old in con.execute("SELECT id FROM route_plan WHERE pilot = ? AND sector = ? AND service_date = ? AND status = 'adopted'",
                               (pilot, sector, day)).fetchall():
            con.execute("UPDATE route_plan SET status = 'superseded' WHERE id = ?", (old["id"],))
            _log(con, old["id"], {"status": ["adopted", "superseded"], "by_plan": plan_id}, actor)
        con.execute("UPDATE route_plan SET status = 'adopted', adopted_at = ?, adopted_by = ? WHERE id = ?",
                    (_now(), actor.get("name"), plan_id))
        streams = set(plan["inputs"].get("streams") or D.STREAMS)
        changed = {"planned": 0, "open": 0}
        for d in D.demands(con, pilot, day, sector, "collection"):
            pid = {"collection_point": d["source_id"], "gvp": f"GVP:{d['source_id']}", "bin": f"BIN:{d['source_id']}"}[d["source_type"]]
            want = "planned" if pid in served and d["stream"] in streams else "open"
            if d["status"] != want:
                D.set_status(con, d["id"], want, actor, f"Route plan {plan_id[:8]}" if want == "planned" else "Not in the adopted route plan.")
                changed[want] += 1
        notes = _assign(con, pilot, sector, day, plan)
        _log(con, plan_id, {"status": ["draft", "adopted"], "demands": changed, "assignment_notes": notes,
                            "accepted_exceptions": plan["feasibility"] == "infeasible"}, actor)
    out = get(con, pilot, plan_id)
    out["demands_changed"], out["assignment_notes"] = changed, notes
    return out


def vehicle_routes(con, pilot: str, day: str) -> dict:
    """Each vehicle's routes on a day, from the adopted plans: {vehicle_id: [{plan_id, sector, route_id, label, km, minutes}]}."""
    out: dict = {}
    for p in adopted(con, pilot, day):
        for r in p["routes"]:
            if r["vehicle_id"]:
                out.setdefault(r["vehicle_id"], []).append({"plan_id": p["id"], "sector": p["sector"], "route_id": r["id"],
                                                            "label": r["label"], "km": r["km"], "minutes": r["minutes"]})
    return out
