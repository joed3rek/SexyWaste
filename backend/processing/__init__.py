"""Processing (data/ops.db): what reaches each facility, what is recovered, and what is left for disposal.

  collection -> transfer -> MRF / composting / recycler -> recovered material + rejects -> disposal

An intake record (facility_intake, append-only) is one day's figures for one facility and stream:
received, recovered by material (dry waste fractions sorted at an MRF, as in norms.json, or compost),
rejects, and residual sent to disposal. Figures are weighed or estimated (`basis`).

The facilities are the same records the route builder uses (backend.facilities), so expected intake
comes from the adopted route plans: what their trucks deliver to the MRF that day. Recorded against
expected, and against the facility's capacity, this feeds planning: a facility over its capacity on
several days opens a planning alert (backend.performance), and observed recovery shows how far the
assumed dry waste fractions are from what an MRF actually sorts.
"""

from __future__ import annotations

import io
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path

from backend import facilities as F
from backend import performance as PERF
from backend.config import ROOT
from backend.regulations import stream_keys

STREAMS = stream_keys()
RECORDERS = ("planner", "admin", "facility_operator")
BASES = ("weighed", "estimated")


def _fractions() -> dict:
    f = json.load(io.open(ROOT / "backend" / "buildings" / "norms.json", encoding="utf-8"))["dry_waste_fractions"]
    return {k: v for k, v in f.items() if not k.startswith("_")}


MATERIALS = tuple(k for k in _fractions() if k != "other_and_rejects") + ("compost",)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS facility_intake (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    facility_id TEXT NOT NULL,
    service_date TEXT NOT NULL,
    stream TEXT NOT NULL CHECK (stream IN {STREAMS}),
    received_kg REAL NOT NULL,
    recovered TEXT NOT NULL DEFAULT '{{}}',   -- JSON {{material: kg}}
    rejects_kg REAL NOT NULL DEFAULT 0,
    disposal_kg REAL NOT NULL DEFAULT 0,      -- residual sent to landfill or other disposal
    basis TEXT NOT NULL CHECK (basis IN {BASES}),
    note TEXT,
    recorded_at TEXT NOT NULL, recorded_by TEXT, recorded_role TEXT
);
CREATE INDEX IF NOT EXISTS facility_intake_day ON facility_intake (pilot, facility_id, service_date);
CREATE TRIGGER IF NOT EXISTS facility_intake_no_update BEFORE UPDATE ON facility_intake
BEGIN SELECT RAISE(ABORT, 'intake records are append-only: record a correction'); END;
CREATE TRIGGER IF NOT EXISTS facility_intake_no_delete BEFORE DELETE ON facility_intake
BEGIN SELECT RAISE(ABORT, 'intake records are append-only'); END;
"""


class ProcessingError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = PERF.connect(db_path)
    con.executescript(F._SCHEMA + _SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kg(v, name: str) -> float:
    try:
        x = float(v or 0)
    except (TypeError, ValueError) as err:
        raise ProcessingError(f"{name} must be a number.") from err
    if not 0 <= x <= 2_000_000:
        raise ProcessingError(f"{name} must be between 0 and 2,000,000 kg.")
    return x


def record(con, pilot: str, facility_id: str, data: dict, actor: dict) -> dict:
    """Record a day's intake at a facility for one stream. A correction is a new record; the latest counts."""
    if actor.get("role") not in RECORDERS:
        raise ProcessingError("Only a facility operator, a planner or an admin can record intake.", 403)
    fac = F.get(con, pilot, facility_id)
    if fac["kind"] not in ("mrf", "compost_site", "recycler", "transfer_station", "disposal_site"):
        raise ProcessingError("Intake is recorded at processing facilities and transfer stations.")
    try:
        day = Date.fromisoformat(data.get("date") or "").isoformat()
    except ValueError as err:
        raise ProcessingError("date must be YYYY-MM-DD.") from err
    if data.get("stream") not in STREAMS:
        raise ProcessingError(f"stream must be one of {STREAMS}")
    if data.get("basis", "weighed") not in BASES:
        raise ProcessingError(f"basis must be one of {BASES}")
    received = _kg(data.get("received_kg"), "received_kg")
    rec = {m: _kg(v, m) for m, v in (data.get("recovered") or {}).items() if v not in (None, "", 0)}
    if not set(rec) <= set(MATERIALS):
        raise ProcessingError(f"recovered materials must be from {MATERIALS}")
    rejects, disposal = _kg(data.get("rejects_kg"), "rejects_kg"), _kg(data.get("disposal_kg"), "disposal_kg")
    if sum(rec.values()) + rejects > received + 0.5:
        raise ProcessingError("Recovered material and rejects cannot be more than what was received.")
    rid = uuid.uuid4().hex
    with con:
        con.execute("INSERT INTO facility_intake (id, pilot, facility_id, service_date, stream, received_kg, recovered, rejects_kg, disposal_kg,"
                    " basis, note, recorded_at, recorded_by, recorded_role) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, pilot, facility_id, day, data["stream"], received, json.dumps(rec), rejects, disposal,
                     data.get("basis", "weighed"), (data.get("note") or "").strip() or None, _now(), actor.get("name"), actor.get("role")))
    return {"id": rid}


def intakes(con, pilot: str, start: str, end: str, facility_id: str | None = None) -> list[dict]:
    """The latest intake record for each facility, day and stream in the period."""
    q, args = "SELECT * FROM facility_intake WHERE pilot = ? AND service_date BETWEEN ? AND ?", [pilot, start, end]
    if facility_id:
        q += " AND facility_id = ?"
        args.append(facility_id)
    last = {}
    for r in con.execute(q + " ORDER BY recorded_at, rowid", args):
        d = dict(r)
        d["recovered"] = json.loads(d["recovered"])
        last[(d["facility_id"], d["service_date"], d["stream"])] = d
    return sorted(last.values(), key=lambda d: (d["service_date"], d["facility_id"], d["stream"]))


def expected(con, pilot: str, start: str, end: str) -> dict:
    """kg the adopted route plans' trucks deliver to the MRF, by day: what the MRF should expect."""
    out = defaultdict(float)
    for r in con.execute("SELECT p.service_date, s.kg FROM route_stop s JOIN route r ON r.id = s.route_id JOIN route_plan p ON p.id = r.plan_id"
                         " WHERE p.pilot = ? AND p.status = 'adopted' AND p.service_date BETWEEN ? AND ? AND s.kind = 'deliver'",
                         (pilot, start, end)):
        out[r["service_date"]] += r["kg"] or 0
    return {k: round(v, 1) for k, v in out.items()}


def summary(con, pilot: str, start: str, end: str) -> dict:
    """Per facility over the period: received, utilisation against capacity, recovery, rejects, disposal."""
    days = (Date.fromisoformat(end) - Date.fromisoformat(start)).days + 1
    if days < 1 or days > 366:
        raise ProcessingError("Choose a period of 1 to 366 days.")
    recs = intakes(con, pilot, start, end)
    facs = {f["id"]: f for f in F.items(con, pilot, include_closed=True)}
    out = []
    for fid, f in facs.items():
        rs = [r for r in recs if r["facility_id"] == fid]
        if not rs and f["kind"] not in ("mrf", "compost_site"):
            continue
        by_day = defaultdict(float)
        for r in rs:
            by_day[r["service_date"]] += r["received_kg"]
        received = sum(by_day.values())
        recovered = defaultdict(float)
        for r in rs:
            for m, kg in r["recovered"].items():
                recovered[m] += kg
        rec_total = sum(recovered.values())
        dry_in = sum(r["received_kg"] for r in rs if r["stream"] == "dry")
        cap = f["capacity_t_day"] * 1000 if f["capacity_t_day"] else None
        over = sorted(d for d, kg in by_day.items() if cap and kg > cap)
        out.append({"facility_id": fid, "name": f["name"], "kind": f["kind"], "status": f["status"], "capacity_t_day": f["capacity_t_day"],
                    "days_recorded": len(by_day), "received_kg": round(received, 1),
                    "mean_t_day": round(received / len(by_day) / 1000, 2) if by_day else None,
                    "peak_t_day": round(max(by_day.values()) / 1000, 2) if by_day else None,
                    "utilisation_pct": round(100 * received / len(by_day) / cap, 1) if cap and by_day else None,
                    "days_over_capacity": over,
                    "recovered_kg": {m: round(v, 1) for m, v in sorted(recovered.items())}, "recovery_pct": round(100 * rec_total / received, 1) if received else None,
                    "rejects_kg": round(sum(r["rejects_kg"] for r in rs), 1), "disposal_kg": round(sum(r["disposal_kg"] for r in rs), 1),
                    "dry_fractions_observed": {m: round(recovered[m] / dry_in, 3) for m in recovered if m != "compost"} if dry_in else {},
                    "basis": sorted({r["basis"] for r in rs})})
    exp = expected(con, pilot, start, end)
    return {"from": start, "to": end, "facilities": out, "expected_to_mrf_kg": exp,
            "dry_fractions_assumed": _fractions(),
            "note": "Expected intake is what adopted route plans deliver to the MRF (estimates). Recorded figures are as weighed or estimated at the facility."}


def scan(con, pilot: str, days: int = 7) -> int:
    """Open a planning alert for each facility over its capacity on two or more of the last `days` days."""
    end = Date.today().isoformat()
    start = (Date.today() - timedelta(days=days - 1)).isoformat()
    new = 0
    with con:
        for f in summary(con, pilot, start, end)["facilities"]:
            if len(f["days_over_capacity"]) >= 2:
                new += PERF._alert(con, pilot, "facility_over_capacity", f["facility_id"], None, 3 if len(f["days_over_capacity"]) >= 4 else 2,
                                   f"{f['name']} took more than its {f['capacity_t_day']:g} t/day on {len(f['days_over_capacity'])} of the last {days} days.",
                                   {"days": f["days_over_capacity"], "peak_t_day": f["peak_t_day"], "capacity_t_day": f["capacity_t_day"]},
                                   ["Send part of the waste to another facility", "Add processing shifts or capacity",
                                    "Process wet waste closer to source (parks, buildings)", "Stagger truck deliveries"])
    return new
