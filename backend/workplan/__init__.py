"""Work planner for city cleaning (data/ops.db): a day's cleaning demands given to people and equipment.

Cleaning is a people-and-equipment system, kept apart from vehicle collection. The planner takes the
day's cleaning demands (backend.demand: streets due for sweeping, verified GVPs to clear), the
cleaning staff available in the sector (backend.resources: sweepers, cleaning workers, GVP response
workers, with their hours per day) and the equipment, and fills each person's day:

  minutes needed   street: metres / productivity; GVP: clearing hours by severity
                   (reference/clean_city.json, estimates until timed in HSR)
  order            GVPs first (most severe first, to GVP response workers where there are any), then
                   streets by priority, taken in map order so one person's streets sit together
  long streets     split between people when longer than what one person has left
  equipment        a GVP takes the kit its severity needs (clean_city.json gvp_equipment), if free

What does not fit is not dropped silently: it is a workforce shortage with the hours missing. With no
cleaning staff entered the plan uses hypothetical 8-hour workers and says so.

work_plan / work_assignment are kept like route plans: drafts, one adopted plan per sector and day
(its demands become `planned`), older ones superseded, never deleted, changes in work_plan_log.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import cleancity as CC
from backend import demand as D
from backend import resources

STATUSES = ("draft", "adopted", "superseded")
PLANNERS = ("planner", "admin")
HYPOTHETICAL_HOURS = 8.0

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS work_plan (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    sector TEXT NOT NULL,
    service_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN {STATUSES}),
    feasibility TEXT NOT NULL,
    exceptions TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL, created_by TEXT,
    adopted_at TEXT, adopted_by TEXT
);
CREATE TABLE IF NOT EXISTS work_assignment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL REFERENCES work_plan(id),
    worker TEXT NOT NULL,                     -- staff id, or "hypothetical-n"
    worker_label TEXT NOT NULL,
    seq INTEGER NOT NULL,
    demand_id TEXT NOT NULL,
    kind TEXT NOT NULL,                       -- street or gvp
    label TEXT,
    metres REAL, minutes REAL NOT NULL,
    equipment TEXT NOT NULL DEFAULT '[]'      -- JSON: equipment ids (or types still missing)
);
CREATE INDEX IF NOT EXISTS work_assignment_by_plan ON work_assignment (plan_id);
CREATE TABLE IF NOT EXISTS work_plan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL, at TEXT NOT NULL, user_name TEXT, user_role TEXT, change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS work_plan_log_no_update BEFORE UPDATE ON work_plan_log
BEGIN SELECT RAISE(ABORT, 'work_plan_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS work_plan_log_no_delete BEFORE DELETE ON work_plan_log
BEGIN SELECT RAISE(ABORT, 'work_plan_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS work_plan_no_delete BEFORE DELETE ON work_plan
BEGIN SELECT RAISE(ABORT, 'work plans are superseded, never deleted'); END;
"""


class WorkPlanError(Exception):
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
    con.execute("INSERT INTO work_plan_log (plan_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                (plan_id, _now(), actor.get("name"), actor.get("role"), json.dumps(change)))


def minutes_for(d: dict, std: dict) -> float:
    """Worker-minutes a cleaning demand takes (estimates from reference/clean_city.json)."""
    prod = std["productivity"]
    if d["source_type"] == "street":
        return d["length_m"] / prod["street_m_per_worker_hour"] * 60
    sev = d["detail"].get("severity") or "medium"
    return prod["gvp_clearing_worker_hours"][sev] * 60


def _workers(con, pilot: str, sector: str, roles: list[str]) -> list[dict]:
    staff = [s for s in resources.items(con, "staff", pilot, sector) if s["role"] in roles and s["status"] in resources.PLANNABLE]
    return [{"id": s["id"], "label": f"{s['name']} ({s['role'].replace('_', ' ')})", "role": s["role"],
             "left": float(s["hours_per_day"] or HYPOTHETICAL_HOURS) * 60} for s in staff]


def build(con, pilot: str, sector: str, day: str) -> dict:
    """The day's work plan (not stored): assignments per worker, unassigned work, exceptions."""
    std = CC.standards()
    D.generate(con, pilot, day, [sector])
    todo = [d for d in D.demands(con, pilot, day, sector, "cleaning") if d["status"] == "open" or d["status"] == "planned"]
    gvps = sorted([d for d in todo if d["source_type"] == "gvp"], key=lambda d: (-d["priority"], d["service_date"]))
    # Streets by priority, then in map order (a coarse grid, west to east, south to north) so
    # consecutive streets given to one person are near each other.
    streets = sorted([d for d in todo if d["source_type"] == "street"],
                     key=lambda d: (-d["priority"], round(d["detail"].get("lon", 0) * 200), d["detail"].get("lat", 0)))
    workers = _workers(con, pilot, sector, std["cleaning_roles"])
    hypothetical = not workers
    need_min = sum(minutes_for(d, std) for d in todo)
    if hypothetical:
        n = max(1, math.ceil(need_min / (HYPOTHETICAL_HOURS * 60)))
        workers = [{"id": f"hypothetical-{i + 1}", "label": f"Worker {i + 1} (hypothetical)", "role": "cleaning_worker",
                    "left": HYPOTHETICAL_HOURS * 60} for i in range(n)]
    kit = [e for e in resources.items(con, "equipment", pilot, sector) if e["status"] in resources.PLANNABLE]
    jobs: dict[str, list] = {w["id"]: [] for w in workers}
    unassigned = []

    def give(w, d, minutes, metres=None):
        jobs[w["id"]].append({"demand": d, "minutes": minutes, "metres": metres})
        w["left"] -= minutes

    for d in gvps:
        m = minutes_for(d, std)
        pool = sorted(workers, key=lambda w: (w["role"] != "gvp_response_worker", -w["left"]))
        w = next((x for x in pool if x["left"] >= m), None)
        if w is None:
            unassigned.append({"demand": d, "minutes": m})
            continue
        give(w, d, m)
    wi = 0
    for d in streets:
        m, metres = minutes_for(d, std), d["length_m"]
        while m > 0.01 and wi < len(workers):
            w = workers[wi]
            if w["left"] <= 1:
                wi += 1
                continue
            part = min(m, w["left"])
            give(w, d, part, round(metres * part / m, 1))
            metres -= metres * part / m
            m -= part
        if m > 0.01:
            unassigned.append({"demand": d, "minutes": m, "metres": round(metres, 1)})
    # Equipment for GVPs, by severity.
    free = list(kit)
    for wid, js in jobs.items():
        for j in js:
            j["equipment"] = []
            if j["demand"]["source_type"] != "gvp":
                continue
            for t in std["gvp_equipment"].get(j["demand"]["detail"].get("severity") or "medium", []):
                e = next((x for x in free if x["type"] == t), None)
                if e:
                    free.remove(e)
                    j["equipment"].append(e["id"])
                else:
                    j["equipment"].append(f"missing:{t}")
    short_min = sum(u["minutes"] for u in unassigned)
    exceptions = []
    if short_min > 0:
        exceptions.append({"code": "workforce_short", "blocking": True, "hours": round(short_min / 60, 1),
                           "workers_at_8h": math.ceil(short_min / 480), "demands": len(unassigned),
                           "reason": f"{short_min / 60:,.1f} worker-hours of cleaning do not fit: {len(unassigned)} demand(s) "
                                     f"would be left (about {math.ceil(short_min / 480)} more worker(s) on 8-hour shifts).",
                           "actions": ["Add cleaning staff in Resources", "Move staff from a sector with spare hours",
                                       "Lower the sweeping frequency of low-intensity streets"]})
    missing = sorted({e[8:] for js in jobs.values() for j in js for e in j["equipment"] if e.startswith("missing:")})
    if missing:
        exceptions.append({"code": "equipment_short", "blocking": False, "types": missing,
                           "reason": "GVP clearing needs equipment that is not free: " + ", ".join(m.replace("_", " ") for m in missing) + ".",
                           "actions": ["Add the equipment in Resources", "Clear the GVP with what is available and record it"]})
    if hypothetical:
        exceptions.append({"code": "hypothetical_workforce", "blocking": False,
                           "reason": f"No cleaning staff are entered for {sector}, so this plan uses {len(workers)} hypothetical 8-hour workers.",
                           "actions": ["Enter the cleaning staff in Resources"]})
    feasibility = "infeasible" if any(e["blocking"] for e in exceptions) else ("feasible_with_warnings" if exceptions else "feasible")
    used = [w for w in workers if jobs[w["id"]]]
    available_min = sum(w["left"] for w in workers) + sum(j["minutes"] for js in jobs.values() for j in js)
    return {"sector": sector, "date": day, "feasibility": feasibility, "exceptions": exceptions, "hypothetical": hypothetical,
            "workers": [{"id": w["id"], "label": w["label"], "minutes": round(sum(j["minutes"] for j in jobs[w["id"]]), 1),
                         "metres": round(sum(j["metres"] or 0 for j in jobs[w["id"]]), 1),
                         "jobs": [{"demand_id": j["demand"]["id"], "kind": j["demand"]["source_type"],
                                   "label": j["demand"]["detail"].get("label"), "metres": j["metres"],
                                   "minutes": round(j["minutes"], 1), "equipment": j["equipment"],
                                   "lon": j["demand"]["detail"].get("lon"), "lat": j["demand"]["detail"].get("lat")}
                                  for j in jobs[w["id"]]]} for w in used],
            "unassigned": [{"demand_id": u["demand"]["id"], "kind": u["demand"]["source_type"], "label": u["demand"]["detail"].get("label"),
                            "minutes": round(u["minutes"], 1), "metres": u.get("metres")} for u in unassigned],
            "summary": {"demands": len(todo), "required_hours": round(need_min / 60, 1),
                        "available_hours": round(available_min / 60, 1), "assigned_hours": round((need_min - short_min) / 60, 1),
                        "short_hours": round(short_min / 60, 1), "workers_used": len(used), "workers": len(workers),
                        "street_m": round(sum(d["length_m"] for d in streets), 1), "gvps": len(gvps)}}


def save(con, pilot: str, plan: dict, actor: dict) -> dict:
    pid = uuid.uuid4().hex
    with con:
        con.execute("INSERT INTO work_plan (id, pilot, sector, service_date, feasibility, exceptions, summary, created_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, pilot, plan["sector"], plan["date"], plan["feasibility"], json.dumps(plan["exceptions"]),
                     json.dumps(plan["summary"] | {"unassigned": plan["unassigned"], "hypothetical": plan["hypothetical"]}),
                     _now(), actor.get("name")))
        for w in plan["workers"]:
            for i, j in enumerate(w["jobs"], start=1):
                con.execute("INSERT INTO work_assignment (plan_id, worker, worker_label, seq, demand_id, kind, label, metres, minutes, equipment)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (pid, w["id"], w["label"], i, j["demand_id"], j["kind"], j["label"], j["metres"], j["minutes"],
                             json.dumps(j["equipment"])))
        _log(con, pid, {"created": {"feasibility": plan["feasibility"], "date": plan["date"]}}, actor)
    return get(con, pilot, pid)


def get(con, pilot: str, plan_id: str) -> dict:
    r = con.execute("SELECT * FROM work_plan WHERE pilot = ? AND id = ?", (pilot, plan_id)).fetchone()
    if r is None:
        raise WorkPlanError("Unknown work plan.", 404)
    p = dict(r)
    p["exceptions"], p["summary"] = json.loads(p["exceptions"]), json.loads(p["summary"])
    workers: dict = {}
    for a in con.execute("SELECT * FROM work_assignment WHERE plan_id = ? ORDER BY worker, seq", (plan_id,)):
        w = workers.setdefault(a["worker"], {"id": a["worker"], "label": a["worker_label"], "minutes": 0.0, "metres": 0.0, "jobs": []})
        w["jobs"].append({"demand_id": a["demand_id"], "kind": a["kind"], "label": a["label"], "metres": a["metres"],
                          "minutes": a["minutes"], "equipment": json.loads(a["equipment"])})
        w["minutes"] += a["minutes"]
        w["metres"] += a["metres"] or 0
    p["workers"] = list(workers.values())
    p["history"] = [{**dict(h), "change": json.loads(h["change"])} for h in
                    con.execute("SELECT at, user_name, user_role, change FROM work_plan_log WHERE plan_id = ? ORDER BY id", (plan_id,))]
    return p


def plans(con, pilot: str, sector: str | None = None, day: str | None = None) -> list[dict]:
    q, args = "SELECT id FROM work_plan WHERE pilot = ?", [pilot]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    if day:
        q += " AND service_date = ?"
        args.append(day)
    out = []
    for r in con.execute(q + " ORDER BY created_at DESC LIMIT 100", args).fetchall():
        p = get(con, pilot, r["id"])
        p.pop("workers")
        out.append(p)
    return out


def adopt(con, pilot: str, plan_id: str, actor: dict, accept_exceptions: bool = False) -> dict:
    if actor.get("role") not in PLANNERS:
        raise WorkPlanError("Only a planner or an admin can adopt a work plan.", 403)
    p = get(con, pilot, plan_id)
    if p["status"] != "draft":
        raise WorkPlanError(f"This work plan is {p['status']}; only a draft can be adopted.", 409)
    if p["feasibility"] == "infeasible" and not accept_exceptions:
        raise WorkPlanError("This work plan leaves cleaning undone. Fix the shortage, or adopt it knowingly (accept_exceptions).", 409)
    assigned = {j["demand_id"] for w in p["workers"] for j in w["jobs"]}
    with con:
        for old in con.execute("SELECT id FROM work_plan WHERE pilot = ? AND sector = ? AND service_date = ? AND status = 'adopted'",
                               (pilot, p["sector"], p["service_date"])).fetchall():
            con.execute("UPDATE work_plan SET status = 'superseded' WHERE id = ?", (old["id"],))
            _log(con, old["id"], {"status": ["adopted", "superseded"], "by_plan": plan_id}, actor)
        con.execute("UPDATE work_plan SET status = 'adopted', adopted_at = ?, adopted_by = ? WHERE id = ?", (_now(), actor.get("name"), plan_id))
        changed = {"planned": 0, "open": 0}
        for d in D.demands(con, pilot, p["service_date"], p["sector"], "cleaning"):
            want = "planned" if d["id"] in assigned else "open"
            if d["status"] != want:
                D.set_status(con, d["id"], want, actor, f"Work plan {plan_id[:8]}" if want == "planned" else "Not in the adopted work plan.")
                changed[want] += 1
        _log(con, plan_id, {"status": ["draft", "adopted"], "demands": changed}, actor)
    out = get(con, pilot, plan_id)
    out["demands_changed"] = changed
    return out
