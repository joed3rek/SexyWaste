"""Garbage mapping: garbage vulnerable points (GVPs), places where waste repeatedly accumulates
outside the intended waste-management system.

SWM Rules 2026, r. 15(1): every GVP is to be geo-mapped and assessed for accumulation.

Reporting. Surveyors, sanitation workers, supervisors, citizens and other authorised users report
GVPs into one database. Each report records who made it (its source), where the pin was put, the
waste streams seen, roughly how much waste there is, how often it builds up, likely dumpers, a
severity and up to a few photos. A GVP belongs to the public realm: it is placed on the nearest
street and linked to that street segment, never to a property. A pin with no street close by is
refused. A report close to a GVP already mapped is added to it instead of creating another one.

Lifecycle (supervisors move it on):
    reported -> verified -> assigned -> cleaning -> cleared -> monitoring
    and "recurred" when waste is reported again at a cleared or monitored GVP; "rejected" for a
    false or duplicate report.

Hand-offs:
- A verified GVP is a cleaning task for the cleaning team: location, severity, waste type,
  quantity, response target and priority (cleaning_tasks()).
- Clearing it creates a one-off collection demand for the waste the team piled up. The route
  builder collects open demands; once collected, the GVP is monitored. Cleaning the GVP and
  transporting its waste are separate steps.

Reports, photos and events are append-only.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from backend.config import (GVP_MAX_PHOTOS, GVP_MAX_ROAD_DISTANCE_M, GVP_MERGE_DISTANCE_M, GVP_RESPONSE_HOURS, PHOTO_DIR,
                            PHOTO_MAX_BYTES, ROOT)
from backend.regulations import stream_keys, swm
from backend.survey import db
from backend.survey.service import SurveyError, _check_sector, point_sector

log = logging.getLogger(__name__)

STREAMS = stream_keys()
MANAGERS = ("survey_supervisor", "operations_supervisor")  # verify, reject, assign, clear, record interventions
CLEANERS = MANAGERS + ("collector",)  # may start cleaning, mark cleared and mark the pickup collected
SECTOR_BOUND = ("surveyor", "survey_supervisor")  # work only in the sectors they chose at sign-in
SOURCE_OF_ROLE = {"surveyor": "surveyor", "collector": "worker", "survey_supervisor": "supervisor",
                  "operations_supervisor": "supervisor", "generator": "citizen"}
OPEN = ("reported", "verified", "assigned", "cleaning", "recurred")  # waste is on the street now
TASK = ("verified", "assigned", "cleaning", "recurred")  # cleaning tasks
# Allowed moves: action -> (from statuses, to status)
MOVES = {
    "verify": (("reported", "recurred"), "verified"),
    "reject": (("reported", "recurred"), "rejected"),
    "assign": (("verified", "recurred", "assigned"), "assigned"),
    "start": (("assigned",), "cleaning"),
    "clear": (("assigned", "cleaning"), "cleared"),
    "monitor": (("cleared",), "monitoring"),
}


def rule() -> dict:
    """The mapping duty as the regulations library states it."""
    r = swm()["collection_and_transport"]["gvp_mapping"]
    return {"rule": r["rule"], "deadline": r["deadline"], "text": r["text"]}


def _norms() -> dict:
    return json.load(io.open(ROOT / "backend" / "buildings" / "norms.json", encoding="utf-8"))["gvp"]


def build_ups_per_day() -> dict:
    return _norms()["build_ups_per_day"]


def size_kg() -> dict:
    return _norms()["size_kg"]


def source_of(role: str | None) -> str:
    return SOURCE_OF_ROLE.get(role or "", "other")


# ---------- Places ----------

def snap_to_road(pilot: str, lon: float, lat: float) -> dict:
    """The nearest point on a street, the street segment and how far the pin moved."""
    import osmnx as ox
    from shapely.geometry import Point

    from backend.routing import points as P
    Gu, seg = P.street_graph(pilot)
    x, y = P._TO_METRIC.transform(lon, lat)
    edges, dists = ox.distance.nearest_edges(Gu, [x], [y], return_dist=True)
    (u, v, k), dist = edges[0], float(dists[0])
    a, b = sorted((u, v))
    seg_id = f"{a}-{b}-{k}"
    row = seg.set_index("seg_id").loc[seg_id]
    on = row.geometry.interpolate(row.geometry.project(Point(x, y)))
    slon, slat = P._TO_WGS84.transform(on.x, on.y)
    return {"lon": float(slon), "lat": float(slat), "seg_id": seg_id, "road_u": int(u), "road_v": int(v),
            "road_name": P._street_name(row["name"]), "snap_m": round(dist, 1)}


def _metres(a: tuple, b: tuple) -> float:
    from backend.routing import points as P
    (x1, y1), (x2, y2) = P._TO_METRIC.transform(*a), P._TO_METRIC.transform(*b)
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


# ---------- Reports ----------

def _report_values(streams, quantity_kg, frequency, sources, severity, size: str | None = None) -> dict:
    streams = [s for s in STREAMS if s in (streams or [])]
    if not streams:
        raise SurveyError("Tick at least one waste stream seen.")
    if quantity_kg in (None, "") and size:
        if size not in size_kg():
            raise SurveyError(f"size must be one of {tuple(size_kg())}")
        quantity_kg = size_kg()[size]
    try:
        q = float(quantity_kg)
    except (TypeError, ValueError) as err:
        raise SurveyError("Enter roughly how many kg, or choose a size.") from err
    if not 0 < q <= 20000:
        raise SurveyError("Quantity must be between 0 and 20,000 kg.")
    if frequency not in db.GVP_FREQUENCIES:
        raise SurveyError(f"frequency must be one of {db.GVP_FREQUENCIES}")
    if severity not in db.GVP_SEVERITIES:
        raise SurveyError(f"severity must be one of {db.GVP_SEVERITIES}")
    sources = [s for s in db.GVP_SOURCES if s in (sources or [])] or ["unknown"]
    return {"streams": streams, "quantity_kg": q, "frequency": frequency, "sources": sources, "severity": severity}


def _check_reporter(actor: dict, sector: str | None) -> None:
    if not actor.get("role") or not actor.get("name"):
        raise SurveyError("Sign in first: choose your role and enter your name.", 401)
    if not sector:
        raise SurveyError("This place is outside the pilot area.")
    if actor["role"] in SECTOR_BOUND:
        _check_sector(actor, sector, "This place")


def _event(con, gvp_id: str, actor: dict, kind: str, value: str | None = None, note: str | None = None) -> None:
    con.execute("INSERT INTO gvp_event (id, gvp_id, at, user_name, user_role, kind, value, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (db.new_id(), gvp_id, db.now(), actor.get("name"), actor.get("role"), kind, value, (note or "").strip() or None))


def _insert_report(con, gvp_id: str, vals: dict, actor: dict, lon, lat, note) -> str:
    oid = db.new_id()
    con.execute("INSERT INTO gvp_observation (id, gvp_id, at, user_name, user_role, source, lon, lat, streams, quantity_kg,"
                " frequency, sources, severity, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, gvp_id, db.now(), actor.get("name"), actor.get("role"), source_of(actor.get("role")), lon, lat,
                 json.dumps(vals["streams"]), vals["quantity_kg"], vals["frequency"], json.dumps(vals["sources"]),
                 vals["severity"], (note or "").strip() or None))
    return oid


def _get(con, pilot: str, gvp_id: str):
    g = con.execute("SELECT * FROM gvp WHERE id = ? AND pilot = ?", (gvp_id, pilot)).fetchone()
    if g is None:
        raise SurveyError("Unknown garbage vulnerable point.", 404)
    return g


def report(pilot: str, actor: dict, lon: float, lat: float, streams, quantity_kg, frequency, sources=None,
           landmark: str | None = None, note: str | None = None, size: str | None = None, severity: str = "medium",
           db_path: Path | None = None) -> dict:
    """Report waste at a place. Maps a new GVP on the nearest street, or adds the report to a GVP
    already mapped close by (`merged`); waste reported at a cleared or monitored GVP marks it recurred.
    A supervisor's own new report is verified at once. Returns the GVP and the new report's id."""
    vals = _report_values(streams, quantity_kg, frequency, sources, severity, size)
    road = snap_to_road(pilot, lon, lat)
    if road["snap_m"] > GVP_MAX_ROAD_DISTANCE_M:
        raise SurveyError(f"A garbage vulnerable point is on a street or public space. The nearest street is {road['snap_m']:.0f} m "
                          f"away; place the pin on the street (within {GVP_MAX_ROAD_DISTANCE_M} m).")
    sector = point_sector(pilot, road["lon"], road["lat"])
    _check_reporter(actor, sector)
    con = db.connect(db_path)
    try:
        near = [g for g in con.execute("SELECT * FROM gvp WHERE pilot = ? AND status != 'rejected'", (pilot,))
                if _metres((g["lon"], g["lat"]), (road["lon"], road["lat"])) <= GVP_MERGE_DISTANCE_M]
        with con:
            if near:
                g = min(near, key=lambda g: _metres((g["lon"], g["lat"]), (road["lon"], road["lat"])))
                gid = g["id"]
                oid = _insert_report(con, gid, vals, actor, lon, lat, note)
                if g["status"] in ("cleared", "monitoring"):
                    con.execute("UPDATE gvp SET status = 'recurred', assigned_to = NULL WHERE id = ?", (gid,))
                    _event(con, gid, actor, "status", "recurred", "Waste reported again")
                merged = True
            else:
                gid, merged = db.new_id(), False
                con.execute("INSERT INTO gvp (id, pilot, sector, lon, lat, seg_id, road_u, road_v, road_name, snap_m, landmark, status,"
                            " severity, created_by, created_role, created_source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (gid, pilot, sector, road["lon"], road["lat"], road["seg_id"], road["road_u"], road["road_v"], road["road_name"],
                             road["snap_m"], (landmark or "").strip() or None, "reported", vals["severity"], actor.get("name"),
                             actor.get("role"), source_of(actor.get("role")), db.now()))
                oid = _insert_report(con, gid, vals, actor, lon, lat, note)
                if actor.get("role") in MANAGERS:
                    con.execute("UPDATE gvp SET status = 'verified', verified_at = ? WHERE id = ?", (db.now(), gid))
                    _event(con, gid, actor, "status", "verified", "Reported by a supervisor")
        return {**detail(pilot, gid, db_path), "merged": merged, "report_id": oid}
    finally:
        con.close()


def add_photo(pilot: str, report_id: str, actor: dict, data: bytes, lon: float | None = None, lat: float | None = None,
              photo_dir: Path | None = None, db_path: Path | None = None) -> dict:
    """Attach a photo to a report, by the person who made it, up to GVP_MAX_PHOTOS per report."""
    if not data:
        raise SurveyError("The photo is empty.")
    if len(data) > PHOTO_MAX_BYTES:
        raise SurveyError(f"The photo is larger than {PHOTO_MAX_BYTES // (1024 * 1024)} MB.", 413)
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP"):
        raise SurveyError("The photo must be a JPEG, PNG or WebP image.", 415)
    con = db.connect(db_path)
    try:
        o = con.execute("SELECT o.*, g.pilot FROM gvp_observation o JOIN gvp g ON g.id = o.gvp_id WHERE o.id = ?", (report_id,)).fetchone()
        if o is None or o["pilot"] != pilot:
            raise SurveyError("Unknown report.", 404)
        if o["user_name"] != actor.get("name"):
            raise SurveyError("Only the person who made the report can add its photos.", 403)
        if con.execute("SELECT COUNT(*) FROM gvp_photo WHERE observation_id = ?", (report_id,)).fetchone()[0] >= GVP_MAX_PHOTOS:
            raise SurveyError(f"A report has at most {GVP_MAX_PHOTOS} photos.", 409)
        digest = hashlib.sha256(data).hexdigest()
        folder = Path(photo_dir or PHOTO_DIR) / "gvp"
        folder.mkdir(parents=True, exist_ok=True)
        pid = db.new_id()
        path = folder / f"{pid}-{digest[:12]}.img"
        path.write_bytes(data)
        with con:
            con.execute("INSERT INTO gvp_photo (id, gvp_id, observation_id, file_path, sha256, taken_at, lon, lat, user_name)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (pid, o["gvp_id"], report_id, str(path), digest, db.now(),
                         lon if lon is not None else o["lon"], lat if lat is not None else o["lat"], actor.get("name")))
        return {"id": pid, "report_id": report_id, "sha256": digest}
    finally:
        con.close()


def photo_file(pilot: str, photo_id: str, db_path: Path | None = None) -> Path:
    con = db.connect(db_path)
    try:
        r = con.execute("SELECT p.file_path FROM gvp_photo p JOIN gvp g ON g.id = p.gvp_id WHERE p.id = ? AND g.pilot = ?",
                        (photo_id, pilot)).fetchone()
    finally:
        con.close()
    if r is None or not Path(r[0]).exists():
        raise SurveyError("Photo not found (it may have been removed after the retention period).", 404)
    return Path(r[0])


# ---------- Lifecycle ----------

def act(pilot: str, gvp_id: str, actor: dict, action: str, value: str | None = None, kg: float | None = None,
        note: str | None = None, db_path: Path | None = None) -> dict:
    """Move a GVP through its lifecycle, set its severity, record an intervention, or mark its
    cleared waste collected.

    verify (value: severity, optional), reject, assign (value: team or person), start, clear (kg left
    for pickup, default the latest reported quantity), monitor, collected, severity (value), and the
    interventions in db.GVP_INTERVENTIONS.
    """
    role = actor.get("role")
    allowed = CLEANERS if action in ("start", "clear", "collected") else MANAGERS
    if role not in allowed:
        raise SurveyError("You cannot do that for a garbage vulnerable point with your role.", 403)
    con = db.connect(db_path)
    try:
        g = _get(con, pilot, gvp_id)
        if role in SECTOR_BOUND:
            _check_sector(actor, g["sector"], "This garbage vulnerable point")
        with con:
            if action in MOVES:
                froms, to = MOVES[action]
                if g["status"] not in froms:
                    raise SurveyError(f"A GVP that is {g['status']} cannot be moved to {to}.", 409)
                sets = {"status": to}
                if action == "verify":
                    sets["verified_at"] = db.now()
                    if value:
                        if value not in db.GVP_SEVERITIES:
                            raise SurveyError(f"severity must be one of {db.GVP_SEVERITIES}")
                        sets["severity"] = value
                if action == "assign":
                    if not (value or "").strip():
                        raise SurveyError("Name the team or person to assign it to.")
                    sets["assigned_to"] = value.strip()
                if action == "clear":
                    last = con.execute("SELECT * FROM gvp_observation WHERE gvp_id = ? ORDER BY at DESC, rowid DESC LIMIT 1", (gvp_id,)).fetchone()
                    q = float(kg) if kg not in (None, "") else float(last["quantity_kg"])
                    if not 0 < q <= 20000:
                        raise SurveyError("Enter the kg left for pickup (more than 0).")
                    con.execute("INSERT INTO gvp_demand (id, gvp_id, created_at, created_by, kg, streams) VALUES (?, ?, ?, ?, ?, ?)",
                                (db.new_id(), gvp_id, db.now(), actor.get("name"), q, last["streams"]))
                    sets["assigned_to"] = None
                con.execute(f"UPDATE gvp SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), gvp_id])
                _event(con, gvp_id, actor, "status", to, note if action != "assign" else f"Assigned to {value.strip()}" + (f". {note}" if note else ""))
            elif action == "collected":
                d = con.execute("SELECT id FROM gvp_demand WHERE gvp_id = ? AND status = 'open'", (gvp_id,)).fetchone()
                if d is None:
                    raise SurveyError("No cleared waste is waiting for pickup here.", 409)
                con.execute("UPDATE gvp_demand SET status = 'collected', collected_at = ?, collected_by = ? WHERE id = ?",
                            (db.now(), actor.get("name"), d["id"]))
                _event(con, gvp_id, actor, "collected", None, note)
                if g["status"] == "cleared":
                    con.execute("UPDATE gvp SET status = 'monitoring' WHERE id = ?", (gvp_id,))
                    _event(con, gvp_id, actor, "status", "monitoring", "Waste collected")
            elif action == "severity":
                if value not in db.GVP_SEVERITIES:
                    raise SurveyError(f"severity must be one of {db.GVP_SEVERITIES}")
                con.execute("UPDATE gvp SET severity = ? WHERE id = ?", (value, gvp_id))
                _event(con, gvp_id, actor, "severity", value, note)
            elif action in db.GVP_INTERVENTIONS:
                _event(con, gvp_id, actor, action, None, note)
            else:
                raise SurveyError(f"Unknown action '{action}'.")
    finally:
        con.close()
    _sync_demands(pilot, g["sector"], db_path, actor)
    return detail(pilot, gvp_id, db_path)


def _sync_demands(pilot: str, sector: str | None, db_path: Path | None, actor: dict) -> None:
    """GVP -> service demands: a verified GVP is a cleaning demand, its cleared waste a collection demand.
    The GVP change is already saved; if the demands cannot be updated now, the next generate() catches up."""
    from backend import demand
    try:
        demand.sync_gvps(pilot, sector, db_path, actor)
    except Exception:  # noqa: BLE001 - never undo a saved GVP change over its demands
        log.exception("Could not update service demands for GVPs in %s", sector)


# ---------- Reading ----------

def _report_row(r, photos: list[dict]) -> dict:
    d = dict(r)
    d["streams"], d["sources"] = json.loads(d["streams"]), json.loads(d["sources"])
    d["photos"] = [p for p in photos if p["observation_id"] == d["id"]]
    return d


def _respond_by(g: dict) -> str | None:
    if not g.get("verified_at") or g["status"] not in TASK:
        return None
    return (datetime.fromisoformat(g["verified_at"]) + timedelta(hours=GVP_RESPONSE_HOURS[g["severity"]])).isoformat(timespec="seconds")


def _summary(con, g: dict) -> dict:
    gid = g["id"]
    last = con.execute("SELECT * FROM gvp_observation WHERE gvp_id = ? ORDER BY at DESC, rowid DESC LIMIT 1", (gid,)).fetchone()
    last = _report_row(last, []) if last else None
    counts = con.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM gvp_observation WHERE gvp_id = ?", (gid,)).fetchone()
    sources = [r[0] for r in con.execute("SELECT DISTINCT source FROM gvp_observation WHERE gvp_id = ?", (gid,))]
    n_int = con.execute(f"SELECT COUNT(*) FROM gvp_event WHERE gvp_id = ? AND kind IN {tuple(db.GVP_INTERVENTIONS)}", (gid,)).fetchone()[0]
    n_rec = con.execute("SELECT COUNT(*) FROM gvp_event WHERE gvp_id = ? AND kind = 'status' AND value = 'recurred'", (gid,)).fetchone()[0]
    n_photos = con.execute("SELECT COUNT(*) FROM gvp_photo WHERE gvp_id = ?", (gid,)).fetchone()[0]
    demand = con.execute("SELECT * FROM gvp_demand WHERE gvp_id = ? AND status = 'open'", (gid,)).fetchone()
    per_day = build_ups_per_day()
    return {
        **g, "reports": counts[0], "report_sources": sources, "photos": n_photos, "interventions": n_int, "recurrences": n_rec,
        "last_reported_at": last["at"] if last else None, "streams": last["streams"] if last else [],
        "quantity_kg": last["quantity_kg"] if last else None, "frequency": last["frequency"] if last else None,
        "kg_per_day": round(last["quantity_kg"] * per_day[last["frequency"]], 1) if last else 0.0,
        "priority": db.GVP_SEVERITIES.index(g["severity"]) + 1, "respond_by": _respond_by(g),
        "pickup": {"kg": demand["kg"], "since": demand["created_at"]} if demand else None,
    }


def list_gvps(pilot: str, sector: str | None = None, db_path: Path | None = None) -> list[dict]:
    con = db.connect(db_path)
    try:
        q, args = "SELECT * FROM gvp WHERE pilot = ?", [pilot]
        if sector:
            q += " AND sector = ?"
            args.append(sector)
        return [_summary(con, dict(g)) for g in con.execute(q + " ORDER BY created_at", args)]
    finally:
        con.close()


def detail(pilot: str, gvp_id: str, db_path: Path | None = None) -> dict:
    con = db.connect(db_path)
    try:
        g = _summary(con, dict(_get(con, pilot, gvp_id)))
        photos = [{k: r[k] for k in ("id", "observation_id", "taken_at", "lon", "lat", "user_name")}
                  for r in con.execute("SELECT * FROM gvp_photo WHERE gvp_id = ? ORDER BY taken_at", (gvp_id,))]
        reports = [_report_row(r, photos) for r in con.execute("SELECT * FROM gvp_observation WHERE gvp_id = ? ORDER BY at, rowid", (gvp_id,))]
        events = [dict(r) for r in con.execute("SELECT * FROM gvp_event WHERE gvp_id = ? ORDER BY at, rowid", (gvp_id,))]
        return {**g, "report_log": reports, "events": events}
    finally:
        con.close()


def cleaning_tasks(pilot: str, sector: str | None = None, db_path: Path | None = None) -> list[dict]:
    """GVP -> Clean City: verified GVPs to be cleared, most severe and oldest first."""
    keep = ("id", "sector", "lon", "lat", "seg_id", "road_name", "landmark", "status", "severity", "priority", "streams",
            "quantity_kg", "assigned_to", "verified_at", "respond_by", "recurrences")
    tasks = [{k: g[k] for k in keep} for g in list_gvps(pilot, sector, db_path) if g["status"] in TASK]
    return sorted(tasks, key=lambda t: (-t["priority"], t["verified_at"] or ""))


def geojson(pilot: str, db_path: Path | None = None) -> dict:
    """GVPs for publishing (r. 15(1)): location, street, status, severity and latest assessment. Rejected reports are left out."""
    keep = ("id", "sector", "seg_id", "road_name", "landmark", "status", "severity", "created_at", "last_reported_at", "streams",
            "quantity_kg", "frequency", "kg_per_day", "reports", "recurrences", "interventions")
    return {"type": "FeatureCollection", "rule": rule(),
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [g["lon"], g["lat"]]},
                          "properties": {k: g[k] for k in keep}}
                         for g in list_gvps(pilot, db_path=db_path) if g["status"] != "rejected"]}


def collection_points(pilot: str, sector: str, db_path: Path | None = None) -> list[dict]:
    """GVP -> route builder: waste cleared from GVPs and waiting for pickup, as one-off collection
    demands (same shape as street-run points, kept apart by is_gvp). Split evenly over the streams seen."""
    out = []
    for g in list_gvps(pilot, sector, db_path):
        if not g["pickup"]:
            continue
        seen = [s for s in g["streams"] if s in STREAMS] or ["dry"]
        kg = {s: (g["pickup"]["kg"] / len(seen) if s in seen else 0.0) for s in STREAMS}
        place = g["landmark"] or g["road_name"]
        out.append({
            "id": f"GVP:{g['id']}", "use": "gvp", "is_gvp": True, "demand": "gvp_pickup", "sector": g["sector"],
            "label": "Cleared GVP waste" + (f": {place}" if place else ""), "severity": g["severity"],
            "buildings": 0, "building_ids": [], "building_kg": [[round(kg[s], 2) for s in STREAMS]],
            "kg": {s: round(v, 1) for s, v in kg.items()}, "total_kg": round(sum(kg.values()), 1),
            "home_composted_kg": 0.0, "span_m": 0.0, "min_width_m": None, "lon": g["lon"], "lat": g["lat"],
            "path_nodes": [g["road_u"], g["road_v"]] if g.get("road_u") is not None else [],  # drive the street it is on
            "reported_by_public": g["created_source"] == "citizen",
        })
    return out
