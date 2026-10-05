"""Collection cycle (data/ops.db): when each waste stream is collected from each type of generator.

A schedule entry says: this stream, from this generator type, in this sector (or in every sector),
is collected on these days within this time window, by this method. A sector's own entry overrides
the entry for every sector. The planner keeps these; the route builder reads them, so a day's
routes carry only the streams due that day, each with the waste built up since its last collection.

SWM Rules 2026: collection at regular intervals (r. 8(h)(iii)); market waste collected day to day
(r. 39(19)). Every stream and generator type without a schedule is reported. The example starting
schedule in reference/collection_cycle.json is a planning choice, not a rule.

Changes are written to cycle_log, which is append-only. Entries are switched off, never deleted.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import resources
from backend.config import ROOT
from backend.regulations import stream_keys, swm

STREAMS = stream_keys()
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GENERATORS = ("households", "commercial", "institutions", "bulk_generators")
METHODS = ("door_to_door", "collection_point", "bin")
EDITORS = ("planner", "admin")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS collection_schedule (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    sector TEXT,                              -- NULL: every sector
    stream TEXT NOT NULL CHECK (stream IN {STREAMS}),
    generator TEXT NOT NULL CHECK (generator IN {GENERATORS}),
    days TEXT NOT NULL,                       -- JSON list of mon..sun
    start TEXT NOT NULL, "end" TEXT NOT NULL, -- HH:MM
    method TEXT NOT NULL CHECK (method IN {METHODS}),
    vehicle_types TEXT NOT NULL DEFAULT '[]', -- JSON list; empty: any
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT
);
CREATE TABLE IF NOT EXISTS cycle_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cycle_log_no_update BEFORE UPDATE ON cycle_log
BEGIN SELECT RAISE(ABORT, 'cycle_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cycle_log_no_delete BEFORE DELETE ON cycle_log
BEGIN SELECT RAISE(ABORT, 'cycle_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS collection_schedule_no_delete BEFORE DELETE ON collection_schedule
BEGIN SELECT RAISE(ABORT, 'schedule entries are switched off, not deleted'); END;
"""


class CycleError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = resources.connect(db_path)  # the same operations database, with the resource tables in place
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rules() -> list[dict]:
    ct = swm()["collection_and_transport"]
    return [{"rule": ct[k]["rule"], "text": ct[k]["text"]} for k in ("regular_intervals", "markets_daily")]


def template() -> list[dict]:
    return json.load(io.open(ROOT / "reference" / "collection_cycle.json", encoding="utf-8"))["template"]


def generator_of(point: dict) -> str | None:
    """The generator type of a route collection point (None for demands outside the cycle: cleared GVPs, public bins)."""
    if point.get("is_gvp") or point.get("is_bin"):
        return None
    if point.get("is_bwg"):
        return "bulk_generators"
    return {"residential": "households", "commercial": "commercial", "mixed": "commercial",
            "institutional": "institutions"}.get(point.get("use"), "households")


# ---------- Entries ----------

def _row(r) -> dict:
    d = dict(r)
    d["days"], d["vehicle_types"] = json.loads(d["days"]), json.loads(d["vehicle_types"])
    d["active"] = bool(d["active"])
    d["hours"] = round(_hours(d["start"], d["end"]), 2)
    return d


def _hours(start: str, end: str) -> float:
    (h1, m1), (h2, m2) = (map(int, start.split(":")), map(int, end.split(":")))
    return (h2 * 60 + m2 - h1 * 60 - m1) / 60


def _clean(data: dict, sectors: list[str], partial: bool) -> dict:
    out = {}
    need = lambda f: f in data or not partial  # noqa: E731
    if "sector" in data:
        if data["sector"] not in (None, "", *sectors):
            raise CycleError(f"Unknown sector '{data['sector']}'")
        out["sector"] = data["sector"] or None
    if need("stream"):
        if data.get("stream") not in STREAMS:
            raise CycleError(f"stream must be one of {STREAMS}")
        out["stream"] = data["stream"]
    if need("generator"):
        if data.get("generator") not in GENERATORS:
            raise CycleError(f"generator must be one of {GENERATORS}")
        out["generator"] = data["generator"]
    if need("days"):
        days = [d for d in DAYS if d in (data.get("days") or [])]
        if not days or len(days) != len(set(data.get("days") or [])):
            raise CycleError(f"Choose at least one day from {DAYS}.")
        out["days"] = days
    for f in ("start", "end"):
        if need(f):
            v = str(data.get(f) or "")
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                raise CycleError(f"{f} must be a time like 06:00")
            out[f] = v
    if need("method"):
        if (data.get("method") or "door_to_door") not in METHODS:
            raise CycleError(f"method must be one of {METHODS}")
        out["method"] = data.get("method") or "door_to_door"
    if "vehicle_types" in data:
        classes = resources.vehicle_classes()
        if not set(data["vehicle_types"] or []) <= set(classes):
            raise CycleError("Unknown vehicle type")
        out["vehicle_types"] = list(data["vehicle_types"] or [])
    if "active" in data:
        out["active"] = 1 if data["active"] else 0
    return out


def _check_editor(actor: dict) -> None:
    if actor.get("role") not in EDITORS:
        raise CycleError("Only a planner or an admin can change the collection cycle.", 403)


def _log(con, sid: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO cycle_log (schedule_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                (sid, _now(), actor.get("name"), actor.get("role"), json.dumps(change)))


def _clash(con, pilot, sector, stream, generator, not_id=None):
    q = ("SELECT id FROM collection_schedule WHERE pilot = ? AND active = 1 AND stream = ? AND generator = ? AND "
         + ("sector IS NULL" if sector is None else "sector = ?") + (" AND id != ?" if not_id else ""))
    args = [pilot, stream, generator] + ([] if sector is None else [sector]) + ([not_id] if not_id else [])
    return con.execute(q, args).fetchone()


def add(con, pilot: str, sectors: list[str], data: dict, actor: dict) -> dict:
    _check_editor(actor)
    v = _clean(data, sectors, partial=False)
    v.setdefault("sector", None)
    v.setdefault("vehicle_types", [])
    if _hours(v["start"], v["end"]) <= 0:
        raise CycleError("The window must end after it starts.")
    if _clash(con, pilot, v["sector"], v["stream"], v["generator"]):
        raise CycleError(f"There is already a schedule for {v['stream']} waste from {v['generator'].replace('_', ' ')} "
                         f"in {v['sector'] or 'every sector'}. Edit it instead.", 409)
    sid, now = uuid.uuid4().hex, _now()
    row = {"id": sid, "pilot": pilot, **{k: (json.dumps(x) if k in ("days", "vehicle_types") else x) for k, x in v.items()},
           "created_at": now, "created_by": actor.get("name"), "updated_at": now, "updated_by": actor.get("name")}
    cols = ", ".join(f'"{k}"' for k in row)
    con.execute(f"INSERT INTO collection_schedule ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    _log(con, sid, {"created": [None, v]}, actor)
    con.commit()
    return get(con, pilot, sid)


def get(con, pilot: str, sid: str) -> dict:
    r = con.execute("SELECT * FROM collection_schedule WHERE id = ? AND pilot = ?", (sid, pilot)).fetchone()
    if r is None:
        raise CycleError("Unknown schedule entry", 404)
    return _row(r)


def update(con, pilot: str, sectors: list[str], sid: str, data: dict, actor: dict) -> dict:
    _check_editor(actor)
    old = get(con, pilot, sid)
    v = _clean(data, sectors, partial=True)
    new = {**old, **v}
    if _hours(new["start"], new["end"]) <= 0:
        raise CycleError("The window must end after it starts.")
    if new["active"] and _clash(con, pilot, new["sector"], new["stream"], new["generator"], not_id=sid):
        raise CycleError("Another active schedule covers the same stream, generator type and sector.", 409)
    change = {k: [old[k], x] for k, x in v.items() if old[k] != (bool(x) if k == "active" else x)}
    if not change:
        return old
    sets = {k: (json.dumps(x) if k in ("days", "vehicle_types") else x) for k, x in v.items()}
    sets.update(updated_at=_now(), updated_by=actor.get("name"))
    con.execute(f"UPDATE collection_schedule SET {', '.join(f'{chr(34)}{k}{chr(34)} = ?' for k in sets)} WHERE id = ?", [*sets.values(), sid])
    _log(con, sid, change, actor)
    con.commit()
    return get(con, pilot, sid)


def entries(con, pilot: str, active_only: bool = False) -> list[dict]:
    q = "SELECT * FROM collection_schedule WHERE pilot = ?" + (" AND active = 1" if active_only else "")
    return [_row(r) for r in con.execute(q + " ORDER BY sector IS NOT NULL, sector, stream, generator", (pilot,))]


def load_template(con, pilot: str, sectors: list[str], actor: dict) -> list[dict]:
    """Start from the example schedule (every sector). Only when no schedule exists yet."""
    _check_editor(actor)
    if entries(con, pilot, active_only=True):
        raise CycleError("A schedule already exists; edit it instead.", 409)
    return [add(con, pilot, sectors, e, actor) for e in template()]


# ---------- Reading for a sector and day ----------

def effective(con, pilot: str, sector: str) -> dict:
    """{(stream, generator): entry} in force for a sector: its own entry, else the every-sector one."""
    out = {}
    for e in entries(con, pilot, active_only=True):
        if e["sector"] in (None, sector):
            key = (e["stream"], e["generator"])
            if key not in out or e["sector"] == sector:
                out[key] = e
    return out


def built_up_days(days: list[str], day: str) -> int:
    """Days of waste waiting on `day`: from the day after the previous collection up to and including this one."""
    if day not in days:
        return 0
    idx = sorted(DAYS.index(d) for d in days)
    i = DAYS.index(day)
    prev = max((j for j in idx if j < i), default=idx[-1] - 7)
    return i - prev


def day_plan(con, pilot: str, sector: str, day: str) -> dict:
    """What is collected in a sector on a weekday: for each generator type and stream, the days of waste
    built up (0: not collected that day), and the collection window covering the day's entries."""
    if day not in DAYS:
        raise CycleError(f"day must be one of {DAYS}")
    eff = effective(con, pilot, sector)
    factors = {g: {s: 0 for s in STREAMS} for g in GENERATORS}
    todays = []
    for (stream, gen), e in eff.items():
        n = built_up_days(e["days"], day)
        factors[gen][stream] = n
        if n:
            todays.append(e)
    window = None
    if todays:
        start, end = min(e["start"] for e in todays), max(e["end"] for e in todays)
        window = {"start": start, "end": end, "hours": round(_hours(start, end), 2)}
    return {"day": day, "sector": sector, "scheduled": bool(eff), "factors": factors, "window": window,
            "entries": todays, "vehicle_types": sorted({t for e in todays for t in e["vehicle_types"]})}


def checks(con, pilot: str, sectors: list[str]) -> list[dict]:
    """Gaps against the rules: a stream from a generator type with no schedule in a sector (r. 8(h)(iii))."""
    out = []
    if not entries(con, pilot, active_only=True):
        return out
    rule = swm()["collection_and_transport"]["regular_intervals"]["rule"]
    for sector in sectors:
        eff = effective(con, pilot, sector)
        missing = [(s, g) for s in STREAMS for g in GENERATORS if (s, g) not in eff]
        if missing:
            out.append({"code": "not_collected", "sector": sector, "rule": rule,
                        "missing": [{"stream": s, "generator": g} for s, g in missing]})
    return out


def week(con, pilot: str, sector: str) -> list[dict]:
    """The week at a glance for a sector: per day, the streams collected and the window."""
    out = []
    for d in DAYS:
        p = day_plan(con, pilot, sector, d)
        streams = sorted({e["stream"] for e in p["entries"]}, key=STREAMS.index)
        out.append({"day": d, "streams": streams, "window": p["window"]})
    return out


def history(con, sid: str) -> list[dict]:
    return [{**dict(r), "change": json.loads(r["change"])}
            for r in con.execute("SELECT * FROM cycle_log WHERE schedule_id = ? ORDER BY id", (sid,))]
