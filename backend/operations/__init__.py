"""Operations (data/ops.db): what actually happened, against what was planned.

Every completed (or failed) piece of work produces an actual record in `operation`, append-only:
  demand   a collection or cleaning demand: done, partly done or missed; actual kg or metres where
           known; the reason when it was not done as planned
  route    a vehicle route of an adopted route plan: actual km, minutes and kg against planned, and
           the stops missed (their demands are recorded missed, the rest done)

Each record keeps its context (date, weekday, sector, kind, stream, generator, vehicle type, street
segment or point) next to the planned and actual figures, so the records are the operational memory
later steps learn from: demand, travel and service time, and what fails where. Recording an outcome
moves the demand's status (done / missed), with the record's id in its history.

Records are never edited or deleted: a correction is a new record for the same thing, and the latest
one counts. Figures are as reported by the person recording (`basis`: reported, weighed, gps).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import demand as D
from backend import plans as PL

OUTCOMES = ("done", "partial", "missed")
REASONS = ("traffic", "loading_delay", "more_waste_than_expected", "less_waste_than_expected", "road_closed",
           "access_blocked", "vehicle_breakdown", "worker_absent", "facility_queue", "not_put_out", "weather", "other")
BASES = ("reported", "weighed", "gps")
RECORDERS = ("planner", "admin", "operations_supervisor")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS operation (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    subject TEXT NOT NULL CHECK (subject IN ('demand', 'route')),
    ref TEXT NOT NULL,                        -- demand id or route id
    service_date TEXT NOT NULL, weekday TEXT NOT NULL, sector TEXT,
    kind TEXT,                                -- collection / cleaning / primary / secondary
    stream TEXT, generator TEXT, vehicle_type TEXT, place TEXT,   -- context: point, segment or route label
    outcome TEXT NOT NULL CHECK (outcome IN {OUTCOMES}),
    planned_kg REAL, actual_kg REAL,
    planned_m REAL, actual_m REAL,
    planned_km REAL, actual_km REAL,
    planned_min REAL, actual_min REAL,
    reason TEXT CHECK (reason IS NULL OR reason IN {REASONS}),
    basis TEXT NOT NULL DEFAULT 'reported' CHECK (basis IN {BASES}),
    note TEXT,
    recorded_at TEXT NOT NULL, recorded_by TEXT, recorded_role TEXT
);
CREATE INDEX IF NOT EXISTS operation_day ON operation (pilot, service_date, sector, subject);
CREATE INDEX IF NOT EXISTS operation_ref ON operation (ref);
CREATE TRIGGER IF NOT EXISTS operation_no_update BEFORE UPDATE ON operation
BEGIN SELECT RAISE(ABORT, 'operation records are append-only: record a correction'); END;
CREATE TRIGGER IF NOT EXISTS operation_no_delete BEFORE DELETE ON operation
BEGIN SELECT RAISE(ABORT, 'operation records are append-only'); END;
"""


class OperationError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = PL.connect(db_path)
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(actor: dict) -> None:
    if actor.get("role") not in RECORDERS:
        raise OperationError("Only a planner, an operations supervisor or an admin can record operations.", 403)


def _num(v, name: str, hi: float):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError) as err:
        raise OperationError(f"{name} must be a number.") from err
    if not 0 <= x <= hi:
        raise OperationError(f"{name} must be between 0 and {hi:,.0f}.")
    return x


def _clean(data: dict) -> dict:
    if data.get("outcome") not in OUTCOMES:
        raise OperationError(f"outcome must be one of {OUTCOMES}")
    if data.get("reason") not in (None, "", *REASONS):
        raise OperationError(f"reason must be one of {REASONS}")
    if data.get("basis", "reported") not in BASES:
        raise OperationError(f"basis must be one of {BASES}")
    if data["outcome"] != "done" and not data.get("reason"):
        raise OperationError("Say why it was not done as planned (reason).")
    return {"outcome": data["outcome"], "reason": data.get("reason") or None, "basis": data.get("basis", "reported"),
            "note": (data.get("note") or "").strip() or None,
            "actual_kg": _num(data.get("actual_kg"), "actual_kg", 100000), "actual_m": _num(data.get("actual_m"), "actual_m", 100000),
            "actual_km": _num(data.get("actual_km"), "actual_km", 2000), "actual_min": _num(data.get("actual_min"), "actual_min", 24 * 60)}


def _insert(con, pilot: str, subject: str, ref: str, ctx: dict, vals: dict, actor: dict) -> str:
    oid = uuid.uuid4().hex
    row = {"id": oid, "pilot": pilot, "subject": subject, "ref": ref, **ctx, **vals,
           "recorded_at": _now(), "recorded_by": actor.get("name"), "recorded_role": actor.get("role")}
    con.execute(f"INSERT INTO operation ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    return oid


def _demand_ctx(d: dict) -> dict:
    return {"service_date": d["service_date"], "weekday": D.weekday(d["service_date"]), "sector": d["sector"], "kind": d["kind"],
            "stream": d["stream"] or None, "generator": d["generator"] or None, "vehicle_type": None,
            "place": f"{d['source_type']}:{d['source_id']}",
            "planned_kg": d["quantity_kg"], "planned_m": d["length_m"], "planned_km": None, "planned_min": None}


def record_demand(con, pilot: str, demand_id: str, data: dict, actor: dict, *, commit: bool = True) -> dict:
    """Record what happened to one demand; its status follows (done, or missed when missed or partial)."""
    _check(actor)
    d = D.get(con, pilot, demand_id)
    if d["status"] == "cancelled":
        raise OperationError("This demand was cancelled; there is nothing to record.", 409)
    vals = _clean(data)
    oid = _insert(con, pilot, "demand", demand_id, _demand_ctx(d), vals, actor)
    status = "done" if vals["outcome"] == "done" else "missed"
    note = f"Recorded {vals['outcome']}" + (f" ({vals['reason'].replace('_', ' ')})" if vals["reason"] else "") + f", record {oid[:8]}."
    D.set_status(con, demand_id, status, actor, note)
    if commit:
        con.commit()
    return {"id": oid, "demand_id": demand_id, "status": status}


def record_route(con, pilot: str, route_id: str, data: dict, actor: dict) -> dict:
    """Record a route's actual km, minutes and kg; stops listed in missed_points are recorded missed
    (with the route's reason), the other stops' demands done."""
    _check(actor)
    r = con.execute("SELECT r.*, p.pilot, p.sector, p.service_date, p.status AS plan_status, p.inputs FROM route r"
                    " JOIN route_plan p ON p.id = r.plan_id WHERE r.id = ? AND p.pilot = ?", (route_id, pilot)).fetchone()
    if r is None:
        raise OperationError("Unknown route.", 404)
    if r["plan_status"] != "adopted":
        raise OperationError("Only routes of an adopted plan are run and recorded.", 409)
    vals = _clean(data)
    missed = {PL.base_point(x) for x in data.get("missed_points") or []}
    stops = [s["point_id"] for s in con.execute("SELECT point_id FROM route_stop WHERE route_id = ? AND kind = 'collect'", (route_id,))]
    served = {PL.base_point(p) for p in stops}
    if not missed <= served:
        raise OperationError("missed_points must be stops of this route.")
    if missed and not vals["reason"]:
        raise OperationError("Say why stops were missed (reason).")
    ctx = {"service_date": r["service_date"], "weekday": D.weekday(r["service_date"]), "sector": r["sector"], "kind": r["tier"],
           "stream": None, "generator": None, "vehicle_type": r["vehicle_type"], "place": r["label"],
           "planned_kg": r["kg"], "planned_m": None, "planned_km": r["km"], "planned_min": r["minutes"]}
    with con:
        oid = _insert(con, pilot, "route", route_id, ctx, vals, actor)
        n = {"done": 0, "missed": 0}
        if r["tier"] == "primary":
            streams = set(json.loads(r["inputs"]).get("streams") or D.STREAMS)
            for d in D.demands(con, pilot, r["service_date"], r["sector"], "collection", ("planned", "open", "done", "missed")):
                pid = {"collection_point": d["source_id"], "gvp": f"GVP:{d['source_id']}", "bin": f"BIN:{d['source_id']}"}[d["source_type"]]
                if pid not in served or d["stream"] not in streams:
                    continue
                miss = pid in missed
                record_demand(con, pilot, d["id"], {"outcome": "missed" if miss else "done", "reason": vals["reason"] if miss else None,
                                                    "note": f"Route {r['label']}"}, actor, commit=False)
                n["missed" if miss else "done"] += 1
    return {"id": oid, "route_id": route_id, "demands": n}


def records(con, pilot: str, day: str | None = None, sector: str | None = None, subject: str | None = None,
            latest_only: bool = True) -> list[dict]:
    """Operation records; by default only the latest record for each demand or route (corrections win)."""
    q, args = "SELECT * FROM operation WHERE pilot = ?", [pilot]
    for col, v in (("service_date", day), ("sector", sector), ("subject", subject)):
        if v:
            q += f" AND {col} = ?"
            args.append(v)
    rows = [dict(r) for r in con.execute(q + " ORDER BY recorded_at, rowid", args)]
    if not latest_only:
        return rows
    last = {}
    for r in rows:
        last[(r["subject"], r["ref"])] = r
    return list(last.values())


def day_sheet(con, pilot: str, day: str, sector: str) -> dict:
    """What to record for a day: the adopted routes and the cleaning demands, with what is recorded so far."""
    done = {(r["subject"], r["ref"]): r for r in records(con, pilot, day, sector)}
    labels = {}
    for d in D.demands(con, pilot, day, sector, "collection", D.STATUSES):
        pid = {"collection_point": d["source_id"], "gvp": f"GVP:{d['source_id']}", "bin": f"BIN:{d['source_id']}"}[d["source_type"]]
        labels.setdefault(pid, d["detail"].get("label") or pid)
    routes = []
    for p in PL.adopted(con, pilot, day, sector):
        for r in p["routes"]:
            routes.append({"route_id": r["id"], "plan_id": p["id"], "label": r["label"], "tier": r["tier"],
                           "vehicle_type": r["vehicle_type"], "vehicle_id": r["vehicle_id"], "planned_km": r["km"],
                           "planned_min": r["minutes"], "planned_kg": r["kg"],
                           "stops": [{"id": pid, "label": labels.get(pid, pid)} for pid in
                                     dict.fromkeys(PL.base_point(s["point_id"]) for s in r["stops"] if s["kind"] == "collect")],
                           "recorded": done.get(("route", r["id"]))})
    cleaning = [{"demand_id": d["id"], "label": d["detail"].get("label"), "source_type": d["source_type"], "status": d["status"],
                 "planned_m": d["length_m"], "planned_kg": d["quantity_kg"], "recorded": done.get(("demand", d["id"]))}
                for d in D.demands(con, pilot, day, sector, "cleaning", ("open", "planned", "done", "missed"))]
    return {"date": day, "sector": sector, "routes": routes, "cleaning": cleaning,
            "reasons": REASONS, "outcomes": OUTCOMES}
