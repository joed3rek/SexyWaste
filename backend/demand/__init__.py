"""Service planning (data/ops.db): what service the city needs, and the work that needs to happen.

Service requirement: what should happen, where, when and how often. Requirements are not stored
here: they are read from where they are already kept, so nothing is entered twice.
  collection   the collection cycle (backend.cycle): a stream from a generator type on some days
  sweeping     the street's cleaning class and frequency (backend.cleancity)
  bin service  each public bin's service rule (backend.cleancity)
  GVP          event-driven: a verified GVP needs clearing, a cleared one needs its waste collected

Service demand: an executable piece of work for a day, stored in service_demand with an append-only
history in service_demand_event. generate() makes a day's demands from the requirements and can be
run again at any time: a demand that already exists is kept (open ones take the latest estimate),
and an open demand no longer required is closed with the reason.
  collection   a stream's waste at a street-run collection point (households, commercial and
               institutional buildings, bulk waste generators), at a public bin, or cleared from a GVP
  cleaning     a street's sweeping for the day, or a verified GVP to clear

Consumers read demands instead of working them out: the route builder takes the day's collection
demands (rule: the route optimiser does not invent demand), Clean City the cleaning demands.
Quantities are estimates (backend/buildings/norms.json, reference/clean_city.json) until weighed.

Statuses: open -> planned -> done | missed | cancelled. GVP demands follow the GVP: they are synced
whenever a GVP moves (backend.survey.gvp.act) and on every generate().
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path

from backend import cleancity, cycle, resources
from backend.regulations import stream_keys
from backend.routing import points as P

log = logging.getLogger(__name__)

STREAMS = stream_keys()
KINDS = ("collection", "cleaning")
SOURCES = ("collection_point", "bin", "gvp", "street")
STATUSES = ("open", "planned", "done", "missed", "cancelled")
ACTIVE = ("open", "planned")
PLANNERS = ("planner", "admin")
# Street cleaning priority by class: busy and market streets first. A planning choice, not a rule.
STREET_PRIORITY = {"market": 3, "primary": 3, "commercial": 2, "residential": 1, "low_intensity": 1}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS service_demand (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN {KINDS}),
    source_type TEXT NOT NULL CHECK (source_type IN {SOURCES}),
    source_id TEXT NOT NULL,
    sector TEXT,
    service_date TEXT NOT NULL,               -- YYYY-MM-DD: the day it is due (GVPs: the day it arose)
    stream TEXT NOT NULL DEFAULT '',          -- collection: one stream per demand; '' otherwise
    generator TEXT NOT NULL DEFAULT '',       -- collection from buildings: households, commercial, ...
    quantity_kg REAL,                         -- expected waste (estimate)
    length_m REAL,                            -- street cleaning: metres to sweep that day
    window_start TEXT, window_end TEXT,       -- HH:MM, from the collection cycle
    priority INTEGER NOT NULL DEFAULT 1,      -- higher first
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN {STATUSES}),
    detail TEXT NOT NULL DEFAULT '{{}}',      -- JSON: label, place, and the map geometry of bins and GVPs
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (pilot, kind, source_type, source_id, service_date, stream)
);
CREATE INDEX IF NOT EXISTS service_demand_day ON service_demand (pilot, sector, service_date, kind);
CREATE TABLE IF NOT EXISTS service_demand_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demand_id TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    kind TEXT NOT NULL,                       -- created, status, quantity
    value TEXT,
    note TEXT
);
CREATE TRIGGER IF NOT EXISTS service_demand_event_no_update BEFORE UPDATE ON service_demand_event
BEGIN SELECT RAISE(ABORT, 'service_demand_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS service_demand_event_no_delete BEFORE DELETE ON service_demand_event
BEGIN SELECT RAISE(ABORT, 'service_demand_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS service_demand_no_delete BEFORE DELETE ON service_demand
BEGIN SELECT RAISE(ABORT, 'demands are closed with a status, never deleted'); END;
"""

SYSTEM = {"name": "CityLoom", "role": "system"}


class DemandError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """ops.db with every table demands are made from (resources, cycle, Clean City) and the demand tables."""
    con = cleancity.connect(db_path)
    con.executescript(cycle._SCHEMA + _SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return Date.today().isoformat()


def parse_date(value: str | None) -> str:
    if not value:
        return today()
    try:
        return Date.fromisoformat(value).isoformat()
    except ValueError as err:
        raise DemandError("date must be YYYY-MM-DD.") from err


def weekday(day: str) -> str:
    return cycle.DAYS[Date.fromisoformat(day).weekday()]


def next_date_for(weekday_key: str, start: str | None = None) -> str:
    """The first date on or after `start` (default today) that falls on a weekday (mon..sun)."""
    if weekday_key not in cycle.DAYS:
        raise DemandError(f"day must be one of {cycle.DAYS}")
    d = Date.fromisoformat(start or today())
    return (d + timedelta(days=(cycle.DAYS.index(weekday_key) - d.weekday()) % 7)).isoformat()


def cleanings_on(per_week: float, day_index: int) -> int:
    """How many times a street with this weekly frequency is cleaned on a weekday (0 = Monday).
    Cleanings are spread evenly over the week: 7 a week is daily, 14 twice a day, 3 on Wed, Fri, Sun."""
    return int((day_index + 1) * per_week / 7 + 1e-9) - int(day_index * per_week / 7 + 1e-9)


# ---------- Requirements: one shape over the existing sources ----------

def requirements(con, pilot: str, sectors: list[str], kind: str | None = None) -> list[dict]:
    """What service each sector needs, read from the collection cycle, the street plans and the bins."""
    out = []
    if kind in (None, "collection"):
        for sector in sectors:
            for (stream, gen), e in sorted(cycle.effective(con, pilot, sector).items()):
                out.append({"kind": "collection", "source_type": "schedule", "source_id": e["id"], "sector": sector,
                            "what": f"{stream} waste from {gen.replace('_', ' ')}", "stream": stream, "generator": gen,
                            "frequency": {"days": e["days"]}, "window": {"start": e["start"], "end": e["end"]},
                            "method": e["method"], "basis": "collection cycle" + ("" if e["sector"] else " (every sector)")})
        for sector in sectors:
            for b in cleancity.bins(con, pilot, sector):
                if b["status"] == "missing":
                    continue
                out.append({"kind": "collection", "source_type": "bin", "source_id": b["id"], "sector": sector,
                            "what": f"Empty public bin {b['bin_code']}", "stream": b["stream"], "generator": "",
                            "frequency": {"per_day": b["services_per_day_in_force"], "or_when": ["full", "overflowing"]},
                            "window": None, "basis": "bin service rule (Clean City)"})
    if kind in (None, "cleaning"):
        for sector in sectors:
            for s in cleancity.streets(con, pilot, sector):
                out.append({"kind": "cleaning", "source_type": "street", "source_id": s["seg_id"], "sector": sector,
                            "what": f"Sweep {s['name'] or 'unnamed street'} ({s['length_m']:.0f} m)", "stream": "", "generator": "",
                            "frequency": {"per_week": s["cleanings_per_week"]}, "window": None,
                            "basis": f"{s['class'].replace('_', ' ')} street" + (" (set by planner)" if s["frequency_overridden"] or s["class_overridden"] else "")})
    gvp_rules = (("cleaning", "Clear each verified garbage vulnerable point", "verified"),
                 ("collection", "Collect the waste cleared from a garbage vulnerable point", "cleared"))
    for k, what, event in gvp_rules:
        if kind in (None, k):
            out.append({"kind": k, "source_type": "gvp", "source_id": "*", "sector": None, "what": what, "stream": "",
                        "generator": "", "frequency": {"on_event": event}, "window": None, "basis": "GVP lifecycle"})
    return out


# ---------- Building a day's demands (not stored) ----------

def _demand(kind, source_type, source_id, sector, day, *, stream="", generator="", quantity_kg=None, length_m=None,
            window=None, priority=1, detail=None) -> dict:
    return {"kind": kind, "source_type": source_type, "source_id": str(source_id), "sector": sector, "service_date": day,
            "stream": stream, "generator": generator or "", "quantity_kg": quantity_kg, "length_m": length_m,
            "window_start": window and window["start"], "window_end": window and window["end"],
            "priority": priority, "detail": detail or {}}


def _gvp_demands(pilot: str, sector: str, survey_db: Path | None = None) -> list[dict]:
    """GVP demands as they stand now: cleaning for GVPs to clear, collection for waste cleared and waiting."""
    from backend.survey import gvp
    out = []
    for t in gvp.cleaning_tasks(pilot, sector, survey_db):
        day = (t["verified_at"] or _now())[:10]
        out.append(_demand("cleaning", "gvp", t["id"], sector, day, quantity_kg=t["quantity_kg"], priority=t["priority"],
                           detail={"label": f"Clear GVP {t['id'][:8]}" + (f", {t['landmark'] or t['road_name']}" if t["landmark"] or t["road_name"] else ""),
                                   "severity": t["severity"], "status": t["status"], "assigned_to": t["assigned_to"],
                                   "respond_by": t["respond_by"], "streams": t["streams"], "lon": t["lon"], "lat": t["lat"]}))
    for p in gvp.collection_points(pilot, sector, survey_db):
        g = gvp.detail(pilot, p["id"].split(":", 1)[1], survey_db)
        day = g["pickup"]["since"][:10]
        geometry = {k: v for k, v in p.items() if k not in ("kg", "total_kg", "building_kg")}
        for s in STREAMS:
            if p["kg"][s] > 0:
                out.append(_demand("collection", "gvp", g["id"], sector, day, stream=s, quantity_kg=p["kg"][s],
                                   priority=g["priority"], detail=geometry))
    return out


def build(con, pilot: str, sector: str, day: str | None, *, survey_db: Path | None = None,
          params: dict | None = None) -> list[dict]:
    """The demands a sector has on a day, worked out from the requirements. day=None is the what-if
    used before a day is chosen: every stream's daily waste, bins and GVPs as they are now, no sweeping."""
    from backend.survey import state
    stamp = day or today()
    out = []
    # Collection from buildings: the street-run collection points, with the cycle's days and windows.
    plan = cycle.day_plan(con, pilot, sector, weekday(day)) if day else None
    scheduled = bool(plan and plan["scheduled"])
    eff = cycle.effective(con, pilot, sector) if scheduled else {}
    for p in P.collection_points(pilot, state.building_states(pilot), sector, params):
        gen = cycle.generator_of(p)
        for s in STREAMS:
            kg = 0.0 if s == "wet" and p.get("wet_excluded") else p["kg"][s]
            if scheduled:
                kg *= plan["factors"][gen][s]
            if kg <= 0:
                continue
            e = eff.get((s, gen))
            out.append(_demand("collection", "collection_point", p["id"], sector, stamp, stream=s, generator=gen,
                               quantity_kg=kg, window=e and {"start": e["start"], "end": e["end"]},
                               priority=2 if p.get("is_bwg") else 1, detail={"label": p.get("label"), "lon": p["lon"], "lat": p["lat"]}))
    # Public bins that need emptying now (Clean City decides which; the route builder collects them).
    for p in cleancity.bin_demands(con, pilot, sector):
        geometry = {k: v for k, v in p.items() if k not in ("kg", "total_kg", "building_kg")}
        for s in STREAMS:
            if p["kg"][s] > 0:
                out.append(_demand("collection", "bin", p["id"].split(":", 1)[1], sector, stamp, stream=s,
                                   quantity_kg=p["kg"][s], priority=2, detail=geometry))
    # Street sweeping due on the day.
    if day:
        i = cycle.DAYS.index(weekday(day))
        for s in cleancity.streets(con, pilot, sector):
            n = cleanings_on(s["cleanings_per_week"], i)
            if n:
                out.append(_demand("cleaning", "street", s["seg_id"], sector, day, length_m=round(s["length_m"] * n, 1),
                                   priority=STREET_PRIORITY[s["class"]],
                                   detail={"label": f"Sweep {s['name'] or 'unnamed street'}" + (f" ({n} times)" if n > 1 else ""),
                                           "class": s["class"], "times": n, "team": s["team"], "equipment": s["equipment"],
                                           "lon": s["coords"][len(s["coords"]) // 2][0], "lat": s["coords"][len(s["coords"]) // 2][1]}))
    out += _gvp_demands(pilot, sector, survey_db)
    return out


# ---------- Storing ----------

def _key(d: dict) -> tuple:
    return (d["kind"], d["source_type"], d["source_id"], d["service_date"], d["stream"])


def _row(r) -> dict:
    d = dict(r)
    d["detail"] = json.loads(d["detail"])
    return d


def _event(con, demand_id: str, actor: dict, kind: str, value=None, note=None) -> None:
    con.execute("INSERT INTO service_demand_event (demand_id, at, user_name, user_role, kind, value, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (demand_id, _now(), actor.get("name"), actor.get("role"), kind, None if value is None else str(value), note))


def set_status(con, demand_id: str, status: str, actor: dict, note: str | None = None) -> None:
    if status not in STATUSES:
        raise DemandError(f"status must be one of {STATUSES}")
    con.execute("UPDATE service_demand SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), demand_id))
    _event(con, demand_id, actor, "status", status, note)


def _store(con, pilot: str, built: list[dict], actor: dict) -> dict:
    """Insert new demands; open ones that exist take the latest estimate. Returns the counts."""
    n = {"created": 0, "updated": 0, "kept": 0}
    for d in built:
        row = con.execute("SELECT * FROM service_demand WHERE pilot = ? AND kind = ? AND source_type = ? AND source_id = ?"
                          " AND service_date = ? AND stream = ?", (pilot, *_key(d))).fetchone()
        if row is None:
            did = uuid.uuid4().hex
            con.execute("INSERT INTO service_demand (id, pilot, kind, source_type, source_id, sector, service_date, stream, generator,"
                        " quantity_kg, length_m, window_start, window_end, priority, detail, created_at, created_by, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (did, pilot, d["kind"], d["source_type"], d["source_id"], d["sector"], d["service_date"], d["stream"],
                         d["generator"], d["quantity_kg"], d["length_m"], d["window_start"], d["window_end"], d["priority"],
                         json.dumps(d["detail"]), _now(), actor.get("name"), _now()))
            _event(con, did, actor, "created")
            n["created"] += 1
        elif row["status"] == "open" and (row["quantity_kg"], row["length_m"], row["window_start"], row["window_end"], row["priority"],
                                          json.loads(row["detail"])) != (d["quantity_kg"], d["length_m"], d["window_start"],
                                                                         d["window_end"], d["priority"], d["detail"]):
            con.execute("UPDATE service_demand SET quantity_kg = ?, length_m = ?, window_start = ?, window_end = ?, priority = ?,"
                        " detail = ?, updated_at = ? WHERE id = ?",
                        (d["quantity_kg"], d["length_m"], d["window_start"], d["window_end"], d["priority"],
                         json.dumps(d["detail"]), _now(), row["id"]))
            if row["quantity_kg"] != d["quantity_kg"] or row["length_m"] != d["length_m"]:
                _event(con, row["id"], actor, "quantity", d["quantity_kg"] if d["quantity_kg"] is not None else d["length_m"])
            n["updated"] += 1
        else:
            n["kept"] += 1
    return n


def _close_unneeded(con, pilot: str, sector: str, day: str, built: list[dict], actor: dict, survey_db: Path | None) -> int:
    """Close open demands no longer required: dated ones for this day, and GVP ones whatever their day."""
    from backend.survey import gvp
    keep = {_key(d) for d in built}
    rows = con.execute("SELECT * FROM service_demand WHERE pilot = ? AND sector = ? AND status = 'open'"
                       " AND ((service_date = ? AND source_type != 'gvp') OR source_type = 'gvp')", (pilot, sector, day)).fetchall()
    closed = 0
    for r in rows:
        if (r["kind"], r["source_type"], r["source_id"], r["service_date"], r["stream"]) in keep:
            continue
        status, note = "cancelled", "No longer required."
        if r["source_type"] == "gvp":
            try:
                g = gvp.detail(pilot, r["source_id"], survey_db)
            except Exception:  # the GVP cannot be read: leave the demand open rather than guess
                continue
            if r["kind"] == "cleaning" and g["status"] in ("cleared", "monitoring"):
                status, note = "done", "GVP cleared."
            elif r["kind"] == "collection" and not g["pickup"]:
                status, note = "done", "Cleared waste collected."
            elif g["status"] == "rejected":
                note = "GVP report rejected."
        elif r["source_type"] == "bin":
            b = con.execute("SELECT last_serviced FROM public_bin WHERE id = ?", (r["source_id"],)).fetchone()
            if b and b["last_serviced"] and b["last_serviced"] >= r["created_at"]:
                status, note = "done", "Bin serviced."
        set_status(con, r["id"], status, actor, note)
        closed += 1
    return closed


def generate(con, pilot: str, day: str, sectors: list[str], actor: dict | None = None, *,
             survey_db: Path | None = None) -> dict:
    """Make or refresh the demands of each sector for a day. Safe to run any number of times."""
    actor = actor or SYSTEM
    total = {"date": day, "created": 0, "updated": 0, "kept": 0, "closed": 0}
    with con:
        for sector in sectors:
            built = build(con, pilot, sector, day, survey_db=survey_db)
            for k, v in _store(con, pilot, built, actor).items():
                total[k] += v
            total["closed"] += _close_unneeded(con, pilot, sector, day, built, actor, survey_db)
    return total


def sync_gvps(pilot: str, sector: str | None, survey_db: Path | None = None, actor: dict | None = None) -> None:
    """Bring a sector's GVP demands in line with its GVPs (called whenever a GVP moves)."""
    if not sector:
        return
    con = connect()
    try:
        with con:
            built = _gvp_demands(pilot, sector, survey_db)
            _store(con, pilot, built, actor or SYSTEM)
            _close_unneeded(con, pilot, sector, today(), built, actor or SYSTEM, survey_db)
    finally:
        con.close()


# ---------- Reading ----------

def demands(con, pilot: str, day: str, sector: str | None = None, kind: str | None = None,
            statuses: tuple = ACTIVE) -> list[dict]:
    """A day's demands, plus GVP demands still waiting from earlier days. Highest priority first."""
    q = ("SELECT * FROM service_demand WHERE pilot = ? AND (service_date = ? OR (source_type = 'gvp' AND service_date <= ?))"
         f" AND status IN ({','.join('?' * len(statuses))})")
    args: list = [pilot, day, day, *statuses]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    return [_row(r) for r in con.execute(q + " ORDER BY priority DESC, sector, source_type, source_id, stream", args)]


def summary(rows: list[dict]) -> dict:
    """Counts and amounts by kind and source."""
    out: dict = {}
    for r in rows:
        k = out.setdefault(r["kind"], {"demands": 0, "kg": 0.0, "m": 0.0, "by_source": {}})
        k["demands"] += 1
        k["kg"] += r["quantity_kg"] or 0
        k["m"] += r["length_m"] or 0
        s = k["by_source"].setdefault(r["source_type"], {"demands": 0, "kg": 0.0, "m": 0.0})
        s["demands"] += 1
        s["kg"] += r["quantity_kg"] or 0
        s["m"] += r["length_m"] or 0
    for k in out.values():
        k["kg"], k["m"] = round(k["kg"], 1), round(k["m"], 1)
        for s in k["by_source"].values():
            s["kg"], s["m"] = round(s["kg"], 1), round(s["m"], 1)
    return out


def get(con, pilot: str, demand_id: str) -> dict:
    r = con.execute("SELECT * FROM service_demand WHERE pilot = ? AND id = ?", (pilot, demand_id)).fetchone()
    if r is None:
        raise DemandError("Unknown service demand.", 404)
    d = _row(r)
    d["history"] = [dict(e) for e in con.execute("SELECT at, user_name, user_role, kind, value, note FROM service_demand_event"
                                                 " WHERE demand_id = ? ORDER BY id", (demand_id,))]
    return d


# ---------- For the route builder ----------

def plan_points(con, pilot: str, sector: str, day: str | None, streams: list[str], *, keep_empty: bool = False,
                survey_db: Path | None = None, params: dict | None = None) -> list[dict]:
    """The route builder's collection points: the collection demands of a day (generated first, stored),
    or the what-if demands when no day is chosen, joined to each point's place on the street network.
    Each point gets `load` (kg by stream, only the streams planned) and `load_kg`."""
    from backend.survey import state
    if day:
        generate(con, pilot, day, [sector], survey_db=survey_db)
        rows = demands(con, pilot, day, sector, "collection")
    else:
        rows = [d for d in build(con, pilot, sector, None, survey_db=survey_db, params=params) if d["kind"] == "collection"]
    loads: dict[tuple, dict] = {}
    places: dict[tuple, dict] = {}
    # Street runs first, then GVPs, then bins: the order the optimiser has always been given.
    for p in P.collection_points(pilot, state.building_states(pilot), sector, params):
        places[("collection_point", p["id"])] = p
    for r in sorted(rows, key=lambda r: r["source_type"] != "gvp"):
        key = (r["source_type"], r["source_id"])
        loads.setdefault(key, {})[r["stream"]] = loads.get(key, {}).get(r["stream"], 0.0) + (r["quantity_kg"] or 0.0)
        if r["source_type"] != "collection_point":
            places.setdefault(key, r["detail"])
    out = []
    for key, place in places.items():
        load = {s: loads.get(key, {}).get(s, 0.0) for s in streams}
        if not keep_empty and sum(load.values()) <= 0:
            continue
        point = dict(place)
        if key[0] != "collection_point":  # bins and GVPs: the amounts shown are those of the demand
            kg = {s: loads[key].get(s, 0.0) for s in STREAMS}
            point.update(kg={s: round(v, 1) for s, v in kg.items()}, total_kg=round(sum(kg.values()), 1),
                         building_kg=[[round(kg[s], 2) for s in STREAMS]])
        out.append({**point, "load": load, "load_kg": sum(load.values())})
    return out
