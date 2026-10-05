"""Resource management (data/ops.db): the vehicles, people and machinery the municipality actually has.

The system never plans with resources that do not exist. Three inventories share one design:
a code (registration, worker ID or equipment ID), where the resource is based (a sector, or the
shared pool for the whole pilot), a status, a shift, a last physical check, and fields of its own.

Status (the same five for every resource):
    available, assigned, in_use   -> can be planned (it exists and works)
    maintenance, unavailable      -> cannot be planned

A plan for a sector may use the plannable resources based in that sector plus the shared pool.
While an inventory is empty, plans are not capped by it and are labelled hypothetical.

Vehicle figures (payload, crew and so on) default to the vehicle class in reference/vehicles.json;
a vehicle may record its own. Every change is written to resource_log, which is append-only.
Login is a dummy for now, so changes record the name and role the person gave.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from backend.config import DATA_DIR
from backend.regulations import stream_keys
from backend.routing import points as P

DB_PATH = DATA_DIR / "ops.db"
STATUSES = ("available", "assigned", "in_use", "maintenance", "unavailable")
PLANNABLE = ("available", "assigned", "in_use")
SHIFTS = ("morning", "afternoon", "night", "general")
# Who changes each inventory: the fleet manager keeps vehicles and machinery, the human resource manager keeps people.
EDITORS = {"vehicle": ("fleet_workforce_manager", "admin"), "equipment": ("fleet_workforce_manager", "admin"),
           "staff": ("hr_manager", "admin")}
STREAMS = stream_keys()
STAFF_ROLES = ("driver", "waste_collector", "sweeper", "cleaning_worker", "gvp_response_worker", "supervisor",
               "facility_worker", "mechanic")
EQUIPMENT_TYPES = ("mechanical_sweeper", "loader", "compactor", "tricycle", "handcart", "pressure_washer",
                   "cleaning_kit", "other")
CONDITIONS = ("good", "fair", "poor")
# Vehicle figures a vehicle may record for itself; otherwise the class value applies.
VEHICLE_SPECS = {"payload_kg": ("payload_kg", float), "body_volume_m3": ("body_volume_m3", float),
                 "vehicle_width_m": ("vehicle_width_m", float), "min_road_width_m": ("min_road_width_m", float),
                 "compartments": ("compartments", int)}

_COMMON = """
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    home_sector TEXT,                         -- NULL: shared pool for the whole pilot
    status TEXT NOT NULL CHECK (status IN {statuses}),
    shift TEXT CHECK (shift IS NULL OR shift IN {shifts}),
    notes TEXT,
    verified_at TEXT, verified_by TEXT,       -- last physical check
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT,
""".format(statuses=STATUSES, shifts=SHIFTS)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS vehicle ({_COMMON}
    registration TEXT NOT NULL,               -- vehicle ID
    type TEXT NOT NULL,                       -- vehicle class (reference/vehicles.json)
    streams TEXT NOT NULL,                    -- JSON list of waste streams it may carry
    payload_kg REAL, body_volume_m3 REAL, vehicle_width_m REAL, min_road_width_m REAL, compartments INTEGER,
    drivers_required INTEGER, collectors_required INTEGER,   -- NULL: the class's crew
    gps INTEGER NOT NULL DEFAULT 0,           -- 1: has a working tracking device
    UNIQUE (pilot, registration)
);
CREATE TABLE IF NOT EXISTS staff ({_COMMON}
    worker_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN {STAFF_ROLES}),
    skills TEXT NOT NULL DEFAULT '[]',        -- JSON list, free text
    hours_per_day REAL NOT NULL DEFAULT 8,
    supervisor TEXT,
    assignment TEXT,                          -- current assignment, free text until Operations exists
    UNIQUE (pilot, worker_id)
);
CREATE TABLE IF NOT EXISTS equipment ({_COMMON}
    equipment_id TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN {EQUIPMENT_TYPES}),
    capacity REAL, capacity_unit TEXT,
    condition TEXT CHECK (condition IS NULL OR condition IN {CONDITIONS}),
    operator_role TEXT CHECK (operator_role IS NULL OR operator_role IN {STAFF_ROLES}),
    last_service TEXT, next_service TEXT,     -- dates
    UNIQUE (pilot, equipment_id)
);
CREATE TABLE IF NOT EXISTS resource_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- vehicle, staff or equipment
    resource_id TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL                      -- JSON object: field -> [old, new]
);
CREATE TRIGGER IF NOT EXISTS resource_log_no_update BEFORE UPDATE ON resource_log
BEGIN SELECT RAISE(ABORT, 'resource_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS resource_log_no_delete BEFORE DELETE ON resource_log
BEGIN SELECT RAISE(ABORT, 'resource_log is append-only'); END;
"""

KINDS = {
    "vehicle": {"table": "vehicle", "code": "registration", "label": "vehicle"},
    "staff": {"table": "staff", "code": "worker_id", "label": "worker"},
    "equipment": {"table": "equipment", "code": "equipment_id", "label": "equipment"},
}


class ResourceError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    _migrate_first_fleet(con)
    con.executescript(_SCHEMA)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _migrate_first_fleet(con: sqlite3.Connection) -> None:
    """The first fleet table had its own statuses and log. Carry its vehicles and history over."""
    row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'vehicle'").fetchone()
    if not row or "under_repair" not in row[0]:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    con.executescript(_SCHEMA.replace("CREATE TABLE IF NOT EXISTS vehicle (", "CREATE TABLE vehicle_new (", 1))
    con.execute("""INSERT INTO vehicle_new (id, pilot, home_sector, status, notes, verified_at, verified_by, created_at, created_by,
                   updated_at, updated_by, registration, type, streams, payload_kg, body_volume_m3, vehicle_width_m,
                   min_road_width_m, compartments)
                   SELECT id, pilot, home_sector,
                          CASE status WHEN 'available' THEN 'available' WHEN 'under_repair' THEN 'maintenance' ELSE 'unavailable' END,
                          CASE status WHEN 'retired' THEN TRIM(COALESCE(notes, '') || ' Retired.') ELSE notes END,
                          verified_at, verified_by, created_at, created_by, updated_at, updated_by, registration, type, streams,
                          payload_kg, body_volume_m3, vehicle_width_m, min_road_width_m, compartments FROM vehicle""")
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'vehicle_log'").fetchone():
        con.execute("INSERT INTO resource_log (kind, resource_id, at, user_name, user_role, change)"
                    " SELECT 'vehicle', vehicle_id, at, user_name, user_role, change FROM vehicle_log ORDER BY id")
        con.executescript("DROP TABLE vehicle_log;")
    con.executescript("DROP TABLE vehicle; ALTER TABLE vehicle_new RENAME TO vehicle;")
    con.commit()


def vehicle_classes() -> dict:
    return {c["key"]: c for c in P.reference("vehicles")["classes"]}


# ---------- Reading ----------

def _row(kind: str, r: sqlite3.Row) -> dict:
    d = dict(r)
    d["kind"] = kind
    d["code"] = d[KINDS[kind]["code"]]
    d["plannable"] = d["status"] in PLANNABLE
    if kind == "vehicle":
        d["streams"] = json.loads(d["streams"])
        d["gps"] = bool(d["gps"])
        cls = vehicle_classes().get(d["type"], {})
        d["tier"], d["label"] = cls.get("tier"), cls.get("label", d["type"])
        d["spec"] = {k: {"value": d[k] if d[k] is not None else (cls.get(src) or {}).get("value"), "own": d[k] is not None}
                     for k, (src, _) in VEHICLE_SPECS.items()}
        crew = cls.get("crew", {"drivers": 1, "collectors": 1})
        d["crew"] = {"drivers": d["drivers_required"] if d["drivers_required"] is not None else crew["drivers"],
                     "collectors": d["collectors_required"] if d["collectors_required"] is not None else crew["collectors"],
                     "own": d["drivers_required"] is not None or d["collectors_required"] is not None}
    if kind == "staff":
        d["skills"] = json.loads(d["skills"])
    if kind == "equipment":
        d["service_due"] = bool(d["next_service"] and d["next_service"] <= date.today().isoformat())
    return d


def get(con, kind: str, pilot: str, rid: str) -> dict:
    k = KINDS[kind]
    r = con.execute(f"SELECT * FROM {k['table']} WHERE id = ? AND pilot = ?", (rid, pilot)).fetchone()
    if r is None:
        raise ResourceError(f"Unknown {k['label']}", 404)
    return _row(kind, r)


def items(con, kind: str, pilot: str, sector: str | None = None) -> list[dict]:
    """All of a kind for the pilot, or those a plan for `sector` may draw on (its own and the shared pool)."""
    k = KINDS[kind]
    q, args = f"SELECT * FROM {k['table']} WHERE pilot = ?", [pilot]
    if sector:
        q += " AND (home_sector = ? OR home_sector IS NULL)"
        args.append(sector)
    order = {"vehicle": "type, registration", "staff": "role, name", "equipment": "type, equipment_id"}[kind]
    return [_row(kind, r) for r in con.execute(f"{q} ORDER BY {order}", args)]


def history(con, kind: str, rid: str) -> list[dict]:
    return [{**dict(r), "change": json.loads(r["change"])}
            for r in con.execute("SELECT * FROM resource_log WHERE kind = ? AND resource_id = ? ORDER BY id", (kind, rid))]


# ---------- Writing ----------

def _check_editor(actor: dict, kind: str) -> None:
    if actor.get("role") not in EDITORS[kind]:
        who = "the human resource manager" if kind == "staff" else "the fleet manager"
        raise ResourceError(f"Only {who} or an admin can change this inventory.", 403)


def _num(v, name: str, cast=float, minimum: float = 0, allow_zero: bool = False):
    if v in (None, ""):
        return None
    try:
        v = cast(float(v))
    except (TypeError, ValueError) as err:
        raise ResourceError(f"{name} must be a number") from err
    if v < minimum or (v == minimum and not allow_zero):
        raise ResourceError(f"{name} must be more than {minimum}")
    return v


def _pick(value, allowed, name):
    if value not in allowed:
        raise ResourceError(f"{name} must be one of {allowed}")
    return value


def _date(v, name):
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError as err:
        raise ResourceError(f"{name} must be a date (YYYY-MM-DD)") from err


def _clean(kind: str, data: dict, sectors: list[str], partial: bool) -> dict:
    out = {}
    need = lambda f: f in data or not partial  # noqa: E731
    code = KINDS[kind]["code"]
    if need(code):
        v = " ".join(str(data.get(code) or "").split()).upper()
        if not v:
            raise ResourceError("Enter the ID.")
        out[code] = v
    if "home_sector" in data:
        if data["home_sector"] not in (None, "", *sectors):
            raise ResourceError(f"Unknown sector '{data['home_sector']}'")
        out["home_sector"] = data["home_sector"] or None
    if need("status"):
        out["status"] = _pick(data.get("status") or "available", STATUSES, "status")
    if "shift" in data:
        out["shift"] = _pick(data["shift"], SHIFTS, "shift") if data["shift"] else None
    if "notes" in data:
        out["notes"] = (data["notes"] or "").strip() or None

    if kind == "vehicle":
        if need("type"):
            out["type"] = _pick(data.get("type"), tuple(vehicle_classes()), "type")
        if need("streams"):
            streams = data.get("streams") or list(STREAMS)
            if not set(streams) <= set(STREAMS):
                raise ResourceError(f"streams must be from {STREAMS}")
            out["streams"] = [s for s in STREAMS if s in streams]
        for f, (_, cast) in VEHICLE_SPECS.items():
            if f in data:
                out[f] = _num(data[f], f, cast)
        for f in ("drivers_required", "collectors_required"):
            if f in data:
                out[f] = _num(data[f], f, int, allow_zero=True)
        if "gps" in data:
            out["gps"] = 1 if data["gps"] else 0
    elif kind == "staff":
        if need("name"):
            name = " ".join(str(data.get("name") or "").split())
            if not name:
                raise ResourceError("Enter the worker's name.")
            out["name"] = name
        if need("role"):
            out["role"] = _pick(data.get("role"), STAFF_ROLES, "role")
        if "skills" in data:
            out["skills"] = [s.strip() for s in (data["skills"] or []) if s and s.strip()]
        if "hours_per_day" in data:
            h = _num(data["hours_per_day"], "hours_per_day")
            if h is not None and h > 16:
                raise ResourceError("hours_per_day must be 16 or less")
            out["hours_per_day"] = h if h is not None else 8
        for f in ("supervisor", "assignment"):
            if f in data:
                out[f] = (data[f] or "").strip() or None
    elif kind == "equipment":
        if need("type"):
            out["type"] = _pick(data.get("type"), EQUIPMENT_TYPES, "type")
        if "capacity" in data:
            out["capacity"] = _num(data["capacity"], "capacity")
        if "capacity_unit" in data:
            out["capacity_unit"] = (data["capacity_unit"] or "").strip() or None
        if "condition" in data:
            out["condition"] = _pick(data["condition"], CONDITIONS, "condition") if data["condition"] else None
        if "operator_role" in data:
            out["operator_role"] = _pick(data["operator_role"], STAFF_ROLES, "operator_role") if data["operator_role"] else None
        for f in ("last_service", "next_service"):
            if f in data:
                out[f] = _date(data[f], f)
    return out


def _log(con, kind: str, rid: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO resource_log (kind, resource_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, rid, _now(), actor.get("name"), actor.get("role"), json.dumps(change, default=str)))


def _db_value(k: str, v):
    return json.dumps(v) if k in ("streams", "skills") else v


def add(con, kind: str, pilot: str, sectors: list[str], data: dict, actor: dict) -> dict:
    _check_editor(actor, kind)
    k = KINDS[kind]
    v = _clean(kind, data, sectors, partial=False)
    rid, now = uuid.uuid4().hex, _now()
    row = {"id": rid, "pilot": pilot, **{f: _db_value(f, x) for f, x in v.items()},
           "created_at": now, "created_by": actor.get("name"), "updated_at": now, "updated_by": actor.get("name")}
    if data.get("verify"):
        row["verified_at"], row["verified_by"] = now, actor.get("name")
    try:
        con.execute(f"INSERT INTO {k['table']} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    except sqlite3.IntegrityError as err:
        raise ResourceError(f"{v[k['code']]} is already registered.", 409) from err
    _log(con, kind, rid, {"created": [None, v]}, actor)
    con.commit()
    return get(con, kind, pilot, rid)


def update(con, kind: str, pilot: str, sectors: list[str], rid: str, data: dict, actor: dict) -> dict:
    """Change some fields; `verify: true` records a physical check by the person signed in."""
    _check_editor(actor, kind)
    k = KINDS[kind]
    old = get(con, kind, pilot, rid)
    v = _clean(kind, data, sectors, partial=True)
    if data.get("verify"):
        v["verified_at"], v["verified_by"] = _now(), actor.get("name")
    raw_old = dict(con.execute(f"SELECT * FROM {k['table']} WHERE id = ?", (rid,)).fetchone())
    sets = {f: _db_value(f, x) for f, x in v.items() if raw_old.get(f) != _db_value(f, x)}
    if not sets:
        return old
    change = {f: [raw_old.get(f), x] for f, x in sets.items()}
    sets.update(updated_at=_now(), updated_by=actor.get("name"))
    try:
        con.execute(f"UPDATE {k['table']} SET {', '.join(f'{f} = ?' for f in sets)} WHERE id = ?", [*sets.values(), rid])
    except sqlite3.IntegrityError as err:
        raise ResourceError(f"{v.get(k['code'])} is already registered.", 409) from err
    _log(con, kind, rid, change, actor)
    con.commit()
    return get(con, kind, pilot, rid)


# ---------- Planning ----------

def available(con, pilot: str, sector: str) -> dict:
    """What a plan for this sector may use: plannable resources based there or in the shared pool.
    `inventory[kind]` is False while the pilot has none of that kind, and plans are then not capped by it."""
    inventory = {kind: con.execute(f"SELECT COUNT(*) FROM {k['table']} WHERE pilot = ?", (pilot,)).fetchone()[0] > 0
                 for kind, k in KINDS.items()}
    vehicles = [v for v in items(con, "vehicle", pilot, sector) if v["plannable"]]
    by_type, crew = {}, {}
    for v in vehicles:
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1
        c = crew.setdefault(v["type"], {"drivers": 0, "collectors": 0})
        c["drivers"] += v["crew"]["drivers"]
        c["collectors"] += v["crew"]["collectors"]
    crew_per_vehicle = {t: {"drivers": c["drivers"] / by_type[t], "collectors": c["collectors"] / by_type[t]} for t, c in crew.items()}
    for t, cls in vehicle_classes().items():  # types not in the inventory use the class's crew
        crew_per_vehicle.setdefault(t, {"drivers": cls.get("crew", {}).get("drivers", 1), "collectors": cls.get("crew", {}).get("collectors", 1)})
    staff = {}
    for s in items(con, "staff", pilot, sector):
        if s["plannable"]:
            r = staff.setdefault(s["role"], {"count": 0, "hours": 0.0})
            r["count"] += 1
            r["hours"] += s["hours_per_day"]
    equipment = {}
    for e in items(con, "equipment", pilot, sector):
        if e["plannable"]:
            equipment[e["type"]] = equipment.get(e["type"], 0) + 1
    return {"inventory": inventory, "vehicles": by_type, "crew_per_vehicle": crew_per_vehicle, "staff": staff,
            "equipment": equipment, "vehicles_without_gps": sum(1 for v in vehicles if not v["gps"])}


def crew_needed(rows: list[dict], crew_per_vehicle: dict) -> dict:
    need = {"drivers": 0.0, "collectors": 0.0}
    for r in rows:
        c = crew_per_vehicle.get(r["type"], {"drivers": 1, "collectors": 1})
        need["drivers"] += int(r.get("count", 0)) * c["drivers"]
        need["collectors"] += int(r.get("count", 0)) * c["collectors"]
    return {k: round(v) for k, v in need.items()}


def shortfalls(av: dict, rows: list[dict]) -> list[str]:
    """What a fleet needs beyond what the sector has: vehicles by type, then drivers and collectors."""
    out = []
    if av["inventory"]["vehicle"]:
        want: dict[str, int] = {}
        for r in rows:
            want[r["type"]] = want.get(r["type"], 0) + int(r.get("count", 0))
        classes = vehicle_classes()
        out += [f"{n} × {classes[t]['label']} (available: {av['vehicles'].get(t, 0)})"
                for t, n in want.items() if n > av["vehicles"].get(t, 0)]
    if av["inventory"]["staff"]:
        need = crew_needed(rows, av["crew_per_vehicle"])
        for need_key, role, word in (("drivers", "driver", "drivers"), ("collectors", "waste_collector", "waste collectors")):
            have = av["staff"].get(role, {}).get("count", 0)
            if need[need_key] > have:
                out.append(f"{need[need_key]} {word} (available: {have})")
    return out


def check_plan(con, pilot: str, sector: str, rows: list[dict]) -> None:
    """Refuse a plan that needs more vehicles, drivers or collectors than the sector has."""
    short = shortfalls(available(con, pilot, sector), rows)
    if short:
        raise ResourceError(f"More than {sector} has: {'; '.join(short)}. Lower the numbers, or add them on the Resources page.")
