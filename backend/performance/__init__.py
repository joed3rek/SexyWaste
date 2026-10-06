"""Performance (read model over data/ops.db and data/survey.db): service required against service delivered.

Not activity counts: for each sector and kind of work over a period, what the service demands asked
for (required), what operation records say was done (delivered), the gap, and why.

  required     demands of the period, cancelled ones left out: collection by kg, cleaning by metres
               (GVP clearing by count)
  delivered    demands recorded done; partly done ones count their recorded actual where given
  missed       recorded missed or partly done, grouped by the reason recorded
  unrecorded   past demands with no outcome recorded: shown apart, never counted as delivered or missed
  not planned  demands no adopted plan took up: a planning cause, shown with the plans' exceptions

Plus route accuracy (planned vs actual km, minutes, kg per route), GVP response times (verification to
clearing, against the response targets in config) and recurrence.

Planning alerts turn repeated failure into planning signals instead of closing the same problem again:
a GVP that keeps coming back, a place missed again and again, or a sector short of capacity day after
day. Each alert carries the facts and the causes to investigate; a planner acknowledges or resolves it.
Alerts are kept (planning_alert) with an append-only log; scanning again does not duplicate open alerts.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path

from backend import demand as D
from backend import operations as O
from backend.config import GVP_RESPONSE_HOURS

ALERT_KINDS = ("recurring_gvp", "repeated_miss", "capacity_short")
ALERT_STATUSES = ("open", "acknowledged", "resolved")
PLANNERS = ("planner", "admin")
GVP_CAUSES = ["Collection not frequent enough nearby", "Too few public bins", "Poor access for vehicles or carts",
              "Commercial waste (shops, eateries)", "Market activity", "Street design", "Illegal dumping",
              "Cleaning not frequent enough", "Other"]
MISS_CAUSES = ["Window too short for the route", "Vehicle or crew shortage", "Access blocked or road closed",
               "Waste not put out in time", "Stop sequenced late in the route", "Other"]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS planning_alert (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN {ALERT_KINDS}),
    ref TEXT NOT NULL,                        -- GVP id, demand source, or sector
    sector TEXT,
    severity INTEGER NOT NULL DEFAULT 1,
    message TEXT NOT NULL,
    facts TEXT NOT NULL DEFAULT '{{}}',       -- JSON: the evidence
    causes TEXT NOT NULL DEFAULT '[]',        -- JSON: what to investigate
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN {ALERT_STATUSES}),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planning_alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL, at TEXT NOT NULL, user_name TEXT, user_role TEXT, change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS planning_alert_log_no_update BEFORE UPDATE ON planning_alert_log
BEGIN SELECT RAISE(ABORT, 'planning_alert_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_alert_log_no_delete BEFORE DELETE ON planning_alert_log
BEGIN SELECT RAISE(ABORT, 'planning_alert_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS planning_alert_no_delete BEFORE DELETE ON planning_alert
BEGIN SELECT RAISE(ABORT, 'alerts are resolved, never deleted'); END;
"""


class PerformanceError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = O.connect(db_path)
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _days(start: str, end: str) -> list[str]:
    a, b = Date.fromisoformat(start), Date.fromisoformat(end)
    if b < a:
        raise PerformanceError("The period ends before it starts.")
    if (b - a).days > 366:
        raise PerformanceError("Choose a period of at most a year.")
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


# ---------- Required vs delivered ----------

def _measure(d: dict) -> tuple[str, float]:
    """The unit a demand is measured in, and its required amount."""
    if d["kind"] == "collection":
        return "kg", d["quantity_kg"] or 0.0
    if d["source_type"] == "street":
        return "m", d["length_m"] or 0.0
    return "count", 1.0


def service(con, pilot: str, start: str, end: str, sector: str | None = None) -> dict:
    """Required vs delivered for the period, by sector and kind, with the reasons for the gap."""
    days = _days(start, end)
    today = D.today()
    q = ("SELECT * FROM service_demand WHERE pilot = ? AND service_date BETWEEN ? AND ? AND status != 'cancelled'")
    args: list = [pilot, start, end]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    demands = [dict(r) for r in con.execute(q, args)]
    ops = {r["ref"]: r for r in O.records(con, pilot, subject="demand") if start <= r["service_date"] <= end}
    out: dict = {}
    for d in demands:
        key = (d["sector"], d["kind"] if d["source_type"] != "gvp" or d["kind"] == "collection" else "gvp_clearing")
        unit, req = _measure(d)
        b = out.setdefault(key, {"sector": key[0], "work": key[1], "unit": unit, "demands": 0, "required": 0.0, "delivered": 0.0,
                                 "missed": 0.0, "unrecorded": 0.0, "pending": 0.0, "not_planned": 0, "reasons": Counter()})
        b["demands"] += 1
        b["required"] += req
        r = ops.get(d["id"])
        if r:
            actual = r["actual_kg"] if unit == "kg" else r["actual_m"] if unit == "m" else None
            if r["outcome"] == "done":
                b["delivered"] += actual if actual is not None else req
            else:
                got = min(actual or 0.0, req) if r["outcome"] == "partial" else 0.0
                b["delivered"] += got
                b["missed"] += req - got
                b["reasons"][r["reason"] or "other"] += 1
        elif d["service_date"] < today:
            b["unrecorded"] += req
        else:
            b["pending"] += req
        if d["status"] == "open" and d["service_date"] <= today:
            b["not_planned"] += 1
    rows = []
    for b in sorted(out.values(), key=lambda x: (x["sector"] or "", x["work"])):
        recorded = b["delivered"] + b["missed"]
        b["service_level_pct"] = round(100 * b["delivered"] / recorded, 1) if recorded else None
        b["recorded_pct"] = round(100 * recorded / b["required"], 1) if b["required"] else None
        b["gap"] = round(b["missed"], 1)
        b["reasons"] = dict(b["reasons"].most_common())
        for k in ("required", "delivered", "missed", "unrecorded", "pending"):
            b[k] = round(b[k], 1)
        rows.append(b)
    return {"from": start, "to": end, "days": len(days), "rows": rows, "plans": _plan_causes(con, pilot, start, end, sector),
            "note": "Service level is delivered over recorded work (done + missed). Work with no outcome recorded is shown as unrecorded, "
                    "not counted either way. Quantities are estimates unless weighed."}


def _plan_causes(con, pilot: str, start: str, end: str, sector: str | None) -> list[dict]:
    """Exceptions of the adopted route and work plans in the period: the planning side of a gap."""
    out = []
    for table, kind in (("route_plan", "routes"), ("work_plan", "cleaning")):
        try:
            q = f"SELECT sector, service_date, feasibility, exceptions FROM {table} WHERE pilot = ? AND status = 'adopted' AND service_date BETWEEN ? AND ?"
            args = [pilot, start, end]
            if sector:
                q += " AND sector = ?"
                args.append(sector)
            for r in con.execute(q, args):
                for e in json.loads(r["exceptions"]):
                    out.append({"plan": kind, "sector": r["sector"], "date": r["service_date"], "code": e["code"], "reason": e["reason"]})
        except sqlite3.OperationalError:  # the work plan tables are created when first used
            continue
    return out


def route_accuracy(con, pilot: str, start: str, end: str, sector: str | None = None) -> dict:
    """Planned vs actual per recorded route, and the mean error by vehicle type: how good the estimates are."""
    recs = [r for r in O.records(con, pilot, subject="route") if start <= r["service_date"] <= end and (not sector or r["sector"] == sector)]
    by_type: dict = defaultdict(lambda: {"routes": 0, "km": [], "min": [], "kg": []})
    rows = []
    for r in recs:
        err = {}
        for k, a, p in (("km", r["actual_km"], r["planned_km"]), ("min", r["actual_min"], r["planned_min"]), ("kg", r["actual_kg"], r["planned_kg"])):
            if a is not None and p:
                err[k] = round(100 * (a - p) / p, 1)
                by_type[r["vehicle_type"]][k].append(err[k])
        by_type[r["vehicle_type"]]["routes"] += 1
        rows.append({"date": r["service_date"], "sector": r["sector"], "route": r["place"], "vehicle_type": r["vehicle_type"],
                     "outcome": r["outcome"], "reason": r["reason"], "planned": {"km": r["planned_km"], "min": r["planned_min"], "kg": r["planned_kg"]},
                     "actual": {"km": r["actual_km"], "min": r["actual_min"], "kg": r["actual_kg"]}, "error_pct": err})
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {"routes": rows, "by_vehicle_type": {t: {"routes": v["routes"], "km_error_pct": mean(v["km"]), "min_error_pct": mean(v["min"]),
                                                     "kg_error_pct": mean(v["kg"])} for t, v in by_type.items()}}


def gvp_response(pilot: str, start: str, end: str, sector: str | None = None, survey_db: Path | None = None) -> dict:
    """Hours from verification to clearing for GVPs cleared in the period, against the response targets."""
    from backend.survey import db as sdb
    from backend.survey import gvp
    con = sdb.connect(survey_db)
    try:
        rows = []
        for g in gvp.list_gvps(pilot, sector, survey_db):
            ev = con.execute("SELECT at, value FROM gvp_event WHERE gvp_id = ? AND kind = 'status' ORDER BY at, rowid", (g["id"],)).fetchall()
            verified = None
            for e in ev:
                if e["value"] == "verified":
                    verified = e["at"]
                elif e["value"] == "cleared" and verified and start <= e["at"][:10] <= end:
                    h = (datetime.fromisoformat(e["at"]) - datetime.fromisoformat(verified)).total_seconds() / 3600
                    rows.append({"gvp_id": g["id"], "sector": g["sector"], "severity": g["severity"], "hours": round(h, 1),
                                 "target_h": GVP_RESPONSE_HOURS[g["severity"]], "on_time": h <= GVP_RESPONSE_HOURS[g["severity"]]})
                    verified = None
        recurring = [{"gvp_id": g["id"], "sector": g["sector"], "recurrences": g["recurrences"], "status": g["status"]}
                     for g in gvp.list_gvps(pilot, sector, survey_db) if g["recurrences"]]
    finally:
        con.close()
    return {"cleared": len(rows), "on_time": sum(r["on_time"] for r in rows),
            "on_time_pct": round(100 * sum(r["on_time"] for r in rows) / len(rows), 1) if rows else None,
            "mean_hours": round(sum(r["hours"] for r in rows) / len(rows), 1) if rows else None,
            "rows": rows, "recurring": sorted(recurring, key=lambda r: -r["recurrences"])}


# ---------- Planning alerts ----------

def _alert(con, pilot: str, kind: str, ref: str, sector, severity: int, message: str, facts: dict, causes: list) -> bool:
    """Open an alert, or refresh the open one for the same thing. Returns True when new."""
    row = con.execute("SELECT id FROM planning_alert WHERE pilot = ? AND kind = ? AND ref = ? AND status != 'resolved'",
                      (pilot, kind, ref)).fetchone()
    if row:
        con.execute("UPDATE planning_alert SET severity = ?, message = ?, facts = ?, updated_at = ? WHERE id = ?",
                    (severity, message, json.dumps(facts), _now(), row["id"]))
        return False
    aid = uuid.uuid4().hex
    con.execute("INSERT INTO planning_alert (id, pilot, kind, ref, sector, severity, message, facts, causes, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, pilot, kind, ref, sector, severity, message, json.dumps(facts), json.dumps(causes), _now(), _now()))
    con.execute("INSERT INTO planning_alert_log (alert_id, at, user_name, user_role, change) VALUES (?, ?, 'CityLoom', 'system', ?)",
                (aid, _now(), json.dumps({"opened": message})))
    return True


def scan(con, pilot: str, days: int = 14, survey_db: Path | None = None) -> dict:
    """Look back over the last `days` for repeated failure and open planning alerts."""
    from backend.survey import gvp
    end = D.today()
    start = (Date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    new = {"recurring_gvp": 0, "repeated_miss": 0, "capacity_short": 0}
    with con:
        for g in gvp.list_gvps(pilot, None, survey_db):
            if g["recurrences"] >= 2:
                place = g["landmark"] or g["road_name"] or "unnamed street"
                new["recurring_gvp"] += _alert(
                    con, pilot, "recurring_gvp", g["id"], g["sector"], min(3, g["recurrences"]),
                    f"GVP at {place} has come back {g['recurrences']} times after clearing.",
                    {"recurrences": g["recurrences"], "severity": g["severity"], "reports": g["reports"], "report_sources": g["report_sources"],
                     "sources_of_waste": g.get("sources"), "kg_per_day": g["kg_per_day"], "status": g["status"]}, GVP_CAUSES)
        missed = Counter()
        label = {}
        for r in O.records(con, pilot, subject="demand"):
            if start <= r["service_date"] <= end and r["outcome"] != "done":
                missed[(r["sector"], r["place"])] += 1
        for (sector, place), n in missed.items():
            if n >= 2:
                src, _, sid = (place or "").partition(":")
                d = con.execute("SELECT detail FROM service_demand WHERE source_type = ? AND source_id = ? ORDER BY service_date DESC LIMIT 1",
                                (src, sid)).fetchone()
                label[place] = (json.loads(d["detail"]).get("label") if d else None) or place
                new["repeated_miss"] += _alert(con, pilot, "repeated_miss", place, sector, min(3, n),
                                               f"{label[place]} was missed {n} times in the last {days} days.",
                                               {"misses": n, "days": days}, MISS_CAUSES)
        short = defaultdict(list)
        for table in ("route_plan", "work_plan"):
            try:
                for r in con.execute(f"SELECT sector, service_date, exceptions FROM {table} WHERE pilot = ? AND status = 'adopted' AND service_date BETWEEN ? AND ?",
                                     (pilot, start, end)):
                    codes = {e["code"] for e in json.loads(r["exceptions"])}
                    if codes & {"unserved_demand", "over_shift", "left_at_stations", "workforce_short"}:
                        short[r["sector"]].append({"date": r["service_date"], "plan": table, "codes": sorted(codes)})
            except sqlite3.OperationalError:
                continue
        for sector, days_short in short.items():
            if len({x["date"] for x in days_short}) >= 3:
                new["capacity_short"] += _alert(con, pilot, "capacity_short", sector, sector, 2,
                                                f"{sector} was planned short of capacity on {len({x['date'] for x in days_short})} days in the last {days} days.",
                                                {"days": days_short}, ["Add vehicles or staff", "Move resources from a sector with spare capacity",
                                                                       "Change the collection cycle or sweeping frequency", "Lengthen the window"])
    return {"from": start, "to": end, "new": new}


def alerts(con, pilot: str, status: str | None = "open", sector: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM planning_alert WHERE pilot = ?", [pilot]
    if status:
        q += " AND status = ?"
        args.append(status)
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    out = []
    for r in con.execute(q + " ORDER BY severity DESC, updated_at DESC", args):
        d = dict(r)
        d["facts"], d["causes"] = json.loads(d["facts"]), json.loads(d["causes"])
        out.append(d)
    return out


def set_alert(con, pilot: str, alert_id: str, status: str, actor: dict, note: str | None = None) -> dict:
    if actor.get("role") not in PLANNERS:
        raise PerformanceError("Only a planner or an admin can acknowledge or resolve alerts.", 403)
    if status not in ALERT_STATUSES:
        raise PerformanceError(f"status must be one of {ALERT_STATUSES}")
    r = con.execute("SELECT status FROM planning_alert WHERE pilot = ? AND id = ?", (pilot, alert_id)).fetchone()
    if r is None:
        raise PerformanceError("Unknown alert.", 404)
    with con:
        con.execute("UPDATE planning_alert SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), alert_id))
        con.execute("INSERT INTO planning_alert_log (alert_id, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?)",
                    (alert_id, _now(), actor.get("name"), actor.get("role"), json.dumps({"status": [r["status"], status], "note": note})))
    return next(a for a in alerts(con, pilot, None) if a["id"] == alert_id)
