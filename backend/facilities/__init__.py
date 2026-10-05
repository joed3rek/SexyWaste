"""Facilities (data/ops.db): the fixed places waste moves through.

  depot             where collection vehicles start and end (a sector's start point)
  truck_yard        where the trucks that haul to the MRF are kept
  transfer_station  where small vehicles unload for the trucks (a sector's set)
  mrf               material recovery facility
  compost_site, recycler, disposal_site   recorded for processing (later steps)

One record per real place, used by every view: the route builder loads them instead of asking for
them on every plan, and processing will attach intake and capacity to them. A facility is closed,
never deleted; every change is written to facility_log (append-only).

Capacity is the planner's figure where known (t/day); the route builder still sizes a transfer
station from the trucks in use when none is recorded. Places come from the planner; nothing here is
a regulation.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import resources
from backend.regulations import stream_keys

STREAMS = stream_keys()
KINDS = ("depot", "truck_yard", "transfer_station", "mrf", "compost_site", "recycler", "disposal_site")
STATUSES = ("active", "planned", "closed")
EDITORS = ("planner", "admin")
# Kinds a sector's route plan places, and whether a sector has its own or shares the city's.
PLAN_PLACES = {"depot": "sector", "truck_yard": "city", "mrf": "city", "transfer_station": "sector"}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS facility (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN {KINDS}),
    name TEXT NOT NULL,
    lon REAL NOT NULL, lat REAL NOT NULL,
    sector TEXT,                              -- NULL: serves the whole city
    capacity_t_day REAL,                      -- NULL: not recorded
    streams TEXT NOT NULL DEFAULT '[]',       -- JSON: streams it takes; empty: any
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN {STATUSES}),
    notes TEXT,
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS facility_kind ON facility (pilot, kind, sector, status);
CREATE TABLE IF NOT EXISTS facility_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility_id TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS facility_log_no_update BEFORE UPDATE ON facility_log
BEGIN SELECT RAISE(ABORT, 'facility_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS facility_log_no_delete BEFORE DELETE ON facility_log
BEGIN SELECT RAISE(ABORT, 'facility_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS facility_no_delete BEFORE DELETE ON facility
BEGIN SELECT RAISE(ABORT, 'facilities are closed, never deleted'); END;
"""

LABEL = {"depot": "Depot", "truck_yard": "Truck yard", "transfer_station": "Transfer station", "mrf": "MRF",
         "compost_site": "Compost site", "recycler": "Recycler", "disposal_site": "Disposal site"}


class FacilityError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = resources.connect(db_path)
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r) -> dict:
    d = dict(r)
    d["streams"] = json.loads(d["streams"])
    return d


def _check_editor(actor: dict) -> None:
    if actor.get("role") not in EDITORS:
        raise FacilityError("Only a planner or an admin can change facilities.", 403)


def _log(con, fid: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO facility_log (facility_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                (fid, _now(), actor.get("name"), actor.get("role"), json.dumps(change)))


def _clean(data: dict, partial: bool) -> dict:
    out = {}
    if "kind" in data or not partial:
        if data.get("kind") not in KINDS:
            raise FacilityError(f"kind must be one of {KINDS}")
        out["kind"] = data["kind"]
    for f in ("lon", "lat"):
        if f in data or not partial:
            try:
                out[f] = round(float(data[f]), 6)
            except (KeyError, TypeError, ValueError) as err:
                raise FacilityError("A facility needs a place on the map (lon, lat).") from err
    if "lon" in out and not (-180 <= out["lon"] <= 180 and -90 <= out["lat"] <= 90):
        raise FacilityError("lon or lat is out of range.")
    if "name" in data:
        out["name"] = (data["name"] or "").strip() or None
    if "sector" in data:
        out["sector"] = data["sector"] or None
    if "capacity_t_day" in data:
        v = data["capacity_t_day"]
        if v not in (None, ""):
            v = float(v)
            if not 0 < v <= 10000:
                raise FacilityError("capacity_t_day must be more than 0 and at most 10,000.")
        out["capacity_t_day"] = v if v not in (None, "") else None
    if "streams" in data:
        s = list(data["streams"] or [])
        if not set(s) <= set(STREAMS):
            raise FacilityError(f"streams must be from {STREAMS}")
        out["streams"] = json.dumps(s)
    if "status" in data:
        if data["status"] not in STATUSES:
            raise FacilityError(f"status must be one of {STATUSES}")
        out["status"] = data["status"]
    if "notes" in data:
        out["notes"] = (data["notes"] or "").strip() or None
    return out


def add(con, pilot: str, data: dict, actor: dict) -> dict:
    _check_editor(actor)
    d = _clean(data, partial=False)
    fid = uuid.uuid4().hex
    n = con.execute("SELECT COUNT(*) FROM facility WHERE pilot = ? AND kind = ?", (pilot, d["kind"])).fetchone()[0]
    name = d.get("name") or f"{LABEL[d['kind']]} {n + 1}" + (f", {d['sector']}" if d.get("sector") else "")
    with con:
        con.execute("INSERT INTO facility (id, pilot, kind, name, lon, lat, sector, capacity_t_day, streams, status, notes,"
                    " created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (fid, pilot, d["kind"], name, d["lon"], d["lat"], d.get("sector"), d.get("capacity_t_day"),
                     d.get("streams", "[]"), d.get("status", "active"), d.get("notes"), _now(), actor.get("name"), _now(),
                     actor.get("name")))
        _log(con, fid, {"created": {k: v for k, v in d.items()} | {"name": name}}, actor)
    return get(con, pilot, fid)


def get(con, pilot: str, fid: str) -> dict:
    r = con.execute("SELECT * FROM facility WHERE pilot = ? AND id = ?", (pilot, fid)).fetchone()
    if r is None:
        raise FacilityError("Unknown facility.", 404)
    return _row(r)


def update(con, pilot: str, fid: str, data: dict, actor: dict) -> dict:
    _check_editor(actor)
    old = get(con, pilot, fid)
    d = _clean({k: v for k, v in data.items() if k != "kind"}, partial=True)
    if "name" in d and not d["name"]:
        raise FacilityError("A facility needs a name.")
    change = {}
    for k, v in d.items():
        before = json.dumps(old[k]) if k == "streams" else old[k]
        if before != v:
            change[k] = [old[k], json.loads(v) if k == "streams" else v]
    if change:
        with con:
            con.execute(f"UPDATE facility SET {', '.join(f'{k} = ?' for k in d)}, updated_at = ?, updated_by = ? WHERE id = ?",
                        [*d.values(), _now(), actor.get("name"), fid])
            _log(con, fid, change, actor)
    return get(con, pilot, fid)


def items(con, pilot: str, kind: str | None = None, sector: str | None = None, include_closed: bool = False) -> list[dict]:
    """Facilities, optionally of one kind; with a sector, those in it plus the city-wide ones."""
    q, args = "SELECT * FROM facility WHERE pilot = ?", [pilot]
    if kind:
        if kind not in KINDS:
            raise FacilityError(f"kind must be one of {KINDS}")
        q += " AND kind = ?"
        args.append(kind)
    if sector:
        q += " AND (sector = ? OR sector IS NULL)"
        args.append(sector)
    if not include_closed:
        q += " AND status != 'closed'"
    return [_row(r) for r in con.execute(q + " ORDER BY kind, sector, created_at", args)]


def history(con, fid: str) -> list[dict]:
    return [{**dict(r), "change": json.loads(r["change"])} for r in
            con.execute("SELECT at, user_name, user_role, change FROM facility_log WHERE facility_id = ? ORDER BY id", (fid,))]


# ---------- A sector's route-plan places ----------

def plan_places(con, pilot: str, sector: str) -> dict:
    """The places a sector's route plan uses: its depot and transfer stations, and the city's MRF and
    truck yard (a sector's own, where it has one). Active facilities only; None where not set."""
    def first(kind):
        rows = [f for f in items(con, pilot, kind, sector) if f["status"] == "active"]
        own = [f for f in rows if f["sector"] == sector]
        return (own or rows or [None])[0]
    return {"depot": first("depot"), "truck_yard": first("truck_yard"), "mrf": first("mrf"),
            "transfer_stations": [f for f in items(con, pilot, "transfer_station", sector)
                                  if f["status"] == "active" and f["sector"] == sector]}


def save_plan_places(con, pilot: str, sector: str, places: dict, actor: dict) -> dict:
    """Save the places set in the route builder. Single places (depot, truck yard, MRF) update the one
    in use or create it; transfer stations are the sector's whole set: ones left out are closed.
    `places`: {"depot": {"lon", "lat"} | None, "truck_yard": ..., "mrf": ..., "transfer_stations": [{"id"?, "lon", "lat"}]}"""
    _check_editor(actor)
    current = plan_places(con, pilot, sector)
    for kind in ("depot", "truck_yard", "mrf"):
        if kind not in places:
            continue
        p, f = places[kind], current[kind]
        if p is None:
            continue  # clearing a place in one plan does not close a facility others may use
        if f is None:
            add(con, pilot, {"kind": kind, "lon": p["lon"], "lat": p["lat"],
                             "sector": sector if PLAN_PLACES[kind] == "sector" else None}, actor)
        elif (round(float(p["lon"]), 6), round(float(p["lat"]), 6)) != (f["lon"], f["lat"]):
            update(con, pilot, f["id"], {"lon": p["lon"], "lat": p["lat"]}, actor)
    if "transfer_stations" in places:
        have = {f["id"]: f for f in current["transfer_stations"]}
        kept = set()
        for p in places["transfer_stations"] or []:
            fid = p.get("id")
            if fid in have:
                kept.add(fid)
                if (round(float(p["lon"]), 6), round(float(p["lat"]), 6)) != (have[fid]["lon"], have[fid]["lat"]):
                    update(con, pilot, fid, {"lon": p["lon"], "lat": p["lat"]}, actor)
            else:
                add(con, pilot, {"kind": "transfer_station", "lon": p["lon"], "lat": p["lat"], "sector": sector}, actor)
        for fid in set(have) - kept:
            update(con, pilot, fid, {"status": "closed"}, actor)
    return plan_places(con, pilot, sector)
