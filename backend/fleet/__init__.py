"""Fleet inventory (data/ops.db): the vehicles that actually exist, per pilot and sector.

A plan may not use more vehicles of a type than are available where it is planned: vehicles
based in the sector plus the shared pool (vehicles with no home sector). Until any vehicle is
entered for a pilot, plans are not capped and are labelled as using a hypothetical fleet.

Vehicle specifications default to the vehicle class in reference/vehicles.json; a vehicle may
record its own figures (for example a measured payload), which are kept beside the defaults.
Every change is written to vehicle_log, which is append-only.

Login is a dummy for now, so changes record the name and role the person gave.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import ROOT
from backend.regulations import stream_keys
from backend.routing import points as P

DB_PATH = ROOT / "data" / "ops.db"
STATUSES = ("available", "under_repair", "off_road", "retired")  # only "available" can be planned
EDITORS = ("fleet_workforce_manager", "admin")
STREAMS = stream_keys()
# Figures a vehicle may record for itself; otherwise the class value applies.
SPEC_FIELDS = {"payload_kg": "payload_kg", "body_volume_m3": "body_volume_m3", "vehicle_width_m": "vehicle_width_m",
               "min_road_width_m": "min_road_width_m", "compartments": "compartments"}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS vehicle (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    registration TEXT NOT NULL,
    type TEXT NOT NULL,
    home_sector TEXT,                         -- NULL: shared pool for the whole pilot
    status TEXT NOT NULL CHECK (status IN {STATUSES}),
    streams TEXT NOT NULL,                    -- JSON list of waste streams it may carry
    payload_kg REAL, body_volume_m3 REAL, vehicle_width_m REAL, min_road_width_m REAL, compartments INTEGER,
    notes TEXT,
    verified_at TEXT, verified_by TEXT,       -- last physical check of the vehicle and its papers
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT,
    UNIQUE (pilot, registration)
);

CREATE TABLE IF NOT EXISTS vehicle_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT NOT NULL REFERENCES vehicle(id),
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL                      -- JSON object: field -> [old, new]
);
CREATE TRIGGER IF NOT EXISTS vehicle_log_no_update BEFORE UPDATE ON vehicle_log
BEGIN SELECT RAISE(ABORT, 'vehicle_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS vehicle_log_no_delete BEFORE DELETE ON vehicle_log
BEGIN SELECT RAISE(ABORT, 'vehicle_log is append-only'); END;
"""


class FleetError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _classes() -> dict:
    return {c["key"]: c for c in P.reference("vehicles")["classes"]}


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["streams"] = json.loads(d["streams"])
    cls = _classes().get(d["type"], {})
    d["tier"] = cls.get("tier")
    d["label"] = cls.get("label", d["type"])
    # Effective figures: the vehicle's own where recorded, else the class value.
    d["spec"] = {k: {"value": d[k] if d[k] is not None else (cls.get(src) or {}).get("value"), "own": d[k] is not None}
                 for k, src in SPEC_FIELDS.items()}
    return d


def _check_editor(actor: dict) -> None:
    if actor.get("role") not in EDITORS:
        raise FleetError("Only the fleet and workforce manager or an admin can change the fleet.", 403)


def _clean(data: dict, sectors: list[str], partial: bool) -> dict:
    out = {}
    if "type" in data or not partial:
        if data.get("type") not in _classes():
            raise FleetError(f"Unknown vehicle type '{data.get('type')}'")
        out["type"] = data["type"]
    if "registration" in data or not partial:
        reg = " ".join(str(data.get("registration") or "").split()).upper()
        if not reg:
            raise FleetError("Enter the registration number or fleet ID.")
        out["registration"] = reg
    if "home_sector" in data:
        if data["home_sector"] not in (None, "", *sectors):
            raise FleetError(f"Unknown sector '{data['home_sector']}'")
        out["home_sector"] = data["home_sector"] or None
    if "status" in data or not partial:
        status = data.get("status") or "available"
        if status not in STATUSES:
            raise FleetError(f"status must be one of {STATUSES}")
        out["status"] = status
    if "streams" in data or not partial:
        streams = data.get("streams") or list(STREAMS)
        if not set(streams) <= set(STREAMS):
            raise FleetError(f"streams must be from {STREAMS}")
        out["streams"] = [s for s in STREAMS if s in streams]
    for k in SPEC_FIELDS:
        if k in data:
            v = data[k]
            if v not in (None, ""):
                v = float(v)
                if v <= 0:
                    raise FleetError(f"{k} must be more than 0")
                if k == "compartments":
                    v = int(v)
            out[k] = None if v in (None, "") else v
    if "notes" in data:
        out["notes"] = (data["notes"] or "").strip() or None
    return out


def _log(con, vehicle_id: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO vehicle_log (vehicle_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                (vehicle_id, _now(), actor.get("name"), actor.get("role"), json.dumps(change)))


def add_vehicle(con, pilot: str, sectors: list[str], data: dict, actor: dict) -> dict:
    _check_editor(actor)
    v = _clean(data, sectors, partial=False)
    vid, now = uuid.uuid4().hex, _now()
    row = {"id": vid, "pilot": pilot, "home_sector": None, **{k: None for k in SPEC_FIELDS}, "notes": None, **v,
           "created_at": now, "created_by": actor.get("name"), "updated_at": now, "updated_by": actor.get("name")}
    row["streams"] = json.dumps(row["streams"])
    try:
        con.execute(f"INSERT INTO vehicle ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    except sqlite3.IntegrityError as err:
        raise FleetError(f"{v['registration']} is already in the fleet.", 409) from err
    _log(con, vid, {"created": [None, {k: data.get(k) for k in data}]}, actor)
    con.commit()
    return get_vehicle(con, pilot, vid)


def get_vehicle(con, pilot: str, vehicle_id: str) -> dict:
    r = con.execute("SELECT * FROM vehicle WHERE id = ? AND pilot = ?", (vehicle_id, pilot)).fetchone()
    if r is None:
        raise FleetError("Unknown vehicle", 404)
    return _row(r)


def update_vehicle(con, pilot: str, sectors: list[str], vehicle_id: str, data: dict, actor: dict) -> dict:
    """Change some fields; `verify: true` records a physical check by the person signed in."""
    _check_editor(actor)
    old = get_vehicle(con, pilot, vehicle_id)
    v = _clean(data, sectors, partial=True)
    if data.get("verify"):
        v["verified_at"], v["verified_by"] = _now(), actor.get("name")
    change = {k: [old.get(k), x] for k, x in v.items() if old.get(k) != x}
    if not change:
        return old
    sets = {k: (json.dumps(x) if k == "streams" else x) for k, x in v.items()}
    sets.update(updated_at=_now(), updated_by=actor.get("name"))
    try:
        con.execute(f"UPDATE vehicle SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), vehicle_id])
    except sqlite3.IntegrityError as err:
        raise FleetError(f"{v.get('registration')} is already in the fleet.", 409) from err
    _log(con, vehicle_id, change, actor)
    con.commit()
    return get_vehicle(con, pilot, vehicle_id)


def vehicles(con, pilot: str, sector: str | None = None) -> list[dict]:
    """All vehicles of the pilot, or those a plan for `sector` may use (its own and the shared pool)."""
    q, args = "SELECT * FROM vehicle WHERE pilot = ?", [pilot]
    if sector:
        q += " AND (home_sector = ? OR home_sector IS NULL)"
        args.append(sector)
    return [_row(r) for r in con.execute(q + " ORDER BY type, registration", args)]


def history(con, vehicle_id: str) -> list[dict]:
    return [{**dict(r), "change": json.loads(r["change"])}
            for r in con.execute("SELECT * FROM vehicle_log WHERE vehicle_id = ? ORDER BY id", (vehicle_id,))]


def available(con, pilot: str, sector: str) -> dict:
    """Vehicles a plan for this sector may use, counted by type. `inventory` is False while the
    pilot has no vehicles at all, and plans are then not capped."""
    total = con.execute("SELECT COUNT(*) FROM vehicle WHERE pilot = ?", (pilot,)).fetchone()[0]
    by_type: dict[str, int] = {}
    for v in vehicles(con, pilot, sector):
        if v["status"] == "available":
            by_type[v["type"]] = by_type.get(v["type"], 0) + 1
    return {"inventory": total > 0, "by_type": by_type}


def check_plan_fleet(con, pilot: str, sector: str, rows: list[dict]) -> None:
    """Refuse a plan that uses more vehicles of a type than are available for the sector."""
    av = available(con, pilot, sector)
    if not av["inventory"]:
        return
    want: dict[str, int] = {}
    for r in rows:
        want[r["type"]] = want.get(r["type"], 0) + int(r.get("count", 0))
    short = [f"{n} × {_classes()[t]['label']} (available: {av['by_type'].get(t, 0)})"
             for t, n in want.items() if n > av["by_type"].get(t, 0)]
    if short:
        raise FleetError(f"More vehicles than the fleet has for {sector}: {'; '.join(short)}. "
                         "Lower the numbers, or add vehicles on the Fleet page.")
