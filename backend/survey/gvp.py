"""Garbage vulnerable points (GVPs): places where waste is dumped outside the collection system.

SWM Rules 2026, r. 15(1): every GVP is to be geo-mapped and assessed for accumulation. Surveyors,
survey supervisors and the public (the generator role) report a GVP with a first observation:
streams seen, roughly how much waste builds up each time (kg, or a size for the public), how
often, the likely generators and a photo. A GVP is on a road: the pin is moved to the nearest
road, and refused when no road is close. A report close to an open GVP is added to it as a new
observation instead of creating another point. Observations are never written over.
Supervisors record interventions and the status.

An active GVP marked for collection becomes a stop on the door-to-door routes, with its waste
estimated as quantity × build-ups per day (an estimate from backend/buildings/norms.json).
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from backend.config import GVP_MAX_ROAD_DISTANCE_M, GVP_MERGE_DISTANCE_M, PHOTO_DIR, PHOTO_MAX_BYTES, ROOT
from backend.regulations import stream_keys, swm
from backend.survey import db
from backend.survey.service import SurveyError, _check_sector, point_sector

REPORTERS = ("surveyor", "survey_supervisor", "generator")
PUBLIC = ("generator",)  # may report anywhere in the pilot, not only in their own sector
MANAGERS = ("survey_supervisor",)
STREAMS = stream_keys()


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


def snap_to_road(pilot: str, lon: float, lat: float) -> dict:
    """The nearest point on a road, the road's end nodes and name, and how far the pin moved."""
    import osmnx as ox
    from shapely.geometry import Point

    from backend.routing import points as P
    Gu, seg = P.street_graph(pilot)
    x, y = P._TO_METRIC.transform(lon, lat)
    edges, dists = ox.distance.nearest_edges(Gu, [x], [y], return_dist=True)
    (u, v, k), dist = edges[0], float(dists[0])
    a, b = sorted((u, v))
    row = seg.set_index("seg_id").loc[f"{a}-{b}-{k}"]
    on = row.geometry.interpolate(row.geometry.project(Point(x, y)))
    slon, slat = P._TO_WGS84.transform(on.x, on.y)
    name = P._street_name(row["name"])
    return {"lon": float(slon), "lat": float(slat), "road_u": int(u), "road_v": int(v), "road_name": name, "snap_m": round(dist, 1)}


def _metres(a: tuple, b: tuple) -> float:
    from backend.routing import points as P
    (x1, y1), (x2, y2) = P._TO_METRIC.transform(*a), P._TO_METRIC.transform(*b)
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def _check_photo(data: bytes) -> None:
    if len(data) > PHOTO_MAX_BYTES:
        raise SurveyError(f"The photo is larger than {PHOTO_MAX_BYTES // (1024 * 1024)} MB.", 413)
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP"):
        raise SurveyError("The photo must be a JPEG, PNG or WebP image.", 415)


def _observation(streams, quantity_kg, frequency, sources, size: str | None = None) -> dict:
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
        raise SurveyError("Enter roughly how many kg build up each time, or choose a size.") from err
    if not 0 < q <= 20000:
        raise SurveyError("Quantity must be between 0 and 20,000 kg.")
    if frequency not in db.GVP_FREQUENCIES:
        raise SurveyError(f"frequency must be one of {db.GVP_FREQUENCIES}")
    sources = [s for s in db.GVP_SOURCES if s in (sources or [])] or ["unknown"]
    return {"streams": streams, "quantity_kg": q, "frequency": frequency, "sources": sources}


def _get(con, pilot: str, gvp_id: str):
    g = con.execute("SELECT * FROM gvp WHERE id = ? AND pilot = ?", (gvp_id, pilot)).fetchone()
    if g is None:
        raise SurveyError("Unknown garbage vulnerable point.", 404)
    return g


def _check_reporter(actor: dict, sector: str | None) -> None:
    if actor.get("role") not in REPORTERS:
        raise SurveyError("Only surveyors, survey supervisors and the public report garbage vulnerable points.", 403)
    if actor.get("role") in PUBLIC:
        if not sector:
            raise SurveyError("This place is outside the pilot area.", 422)
    else:
        _check_sector(actor, sector, "This place")


def report(pilot: str, actor: dict, lon: float, lat: float, streams, quantity_kg, frequency, sources=None,
           landmark: str | None = None, note: str | None = None, size: str | None = None, db_path: Path | None = None) -> dict:
    """Map a GVP on the nearest road with its first observation, or add the observation to an open GVP
    already mapped close by. The result says which (`merged`)."""
    obs = _observation(streams, quantity_kg, frequency, sources, size)
    road = snap_to_road(pilot, lon, lat)
    if road["snap_m"] > GVP_MAX_ROAD_DISTANCE_M:
        raise SurveyError(f"A garbage vulnerable point is on a road. The nearest road is {road['snap_m']:.0f} m away; "
                          f"place the pin on the road (within {GVP_MAX_ROAD_DISTANCE_M} m).", 422)
    sector = point_sector(pilot, road["lon"], road["lat"])
    _check_reporter(actor, sector)
    con = db.connect(db_path)
    try:
        near = [g for g in con.execute("SELECT * FROM gvp WHERE pilot = ? AND status != 'closed'", (pilot,))
                if _metres((g["lon"], g["lat"]), (road["lon"], road["lat"])) <= GVP_MERGE_DISTANCE_M]
        if near:
            g = min(near, key=lambda g: _metres((g["lon"], g["lat"]), (road["lon"], road["lat"])))
            with con:
                _insert_observation(con, g["id"], obs, actor, note)
                if g["status"] != "active":  # waste is back: the GVP is active again
                    con.execute("UPDATE gvp SET status = 'active' WHERE id = ?", (g["id"],))
                    con.execute("INSERT INTO gvp_event (id, gvp_id, at, user_name, user_role, kind, value, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (db.new_id(), g["id"], db.now(), actor.get("name"), actor.get("role"), "status", "active", "Reported again"))
            return {**detail(pilot, g["id"], db_path), "merged": True}
        gid, at = db.new_id(), db.now()
        with con:
            con.execute("INSERT INTO gvp (id, pilot, sector, lon, lat, landmark, road_u, road_v, road_name, snap_m,"
                        " created_by, created_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (gid, pilot, sector, road["lon"], road["lat"], (landmark or "").strip() or None, road["road_u"], road["road_v"],
                         road["road_name"], road["snap_m"], actor.get("name"), actor.get("role"), at))
            _insert_observation(con, gid, obs, actor, note)
        return {**detail(pilot, gid, db_path), "merged": False}
    finally:
        con.close()


def _insert_observation(con, gvp_id: str, obs: dict, actor: dict, note: str | None) -> str:
    oid = db.new_id()
    con.execute("INSERT INTO gvp_observation (id, gvp_id, at, user_name, user_role, streams, quantity_kg, frequency, sources, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (oid, gvp_id, db.now(), actor.get("name"), actor.get("role"), json.dumps(obs["streams"]), obs["quantity_kg"],
                 obs["frequency"], json.dumps(obs["sources"]), (note or "").strip() or None))
    return oid


def observe(pilot: str, gvp_id: str, actor: dict, streams, quantity_kg, frequency, sources=None, note: str | None = None,
            size: str | None = None, db_path: Path | None = None) -> dict:
    """Add an observation (assessment of accumulation) beside the earlier ones."""
    obs = _observation(streams, quantity_kg, frequency, sources, size)
    con = db.connect(db_path)
    try:
        g = _get(con, pilot, gvp_id)
        _check_reporter(actor, g["sector"])
        with con:
            _insert_observation(con, gvp_id, obs, actor, note)
        return detail(pilot, gvp_id, db_path)
    finally:
        con.close()


def add_photo(pilot: str, observation_id: str, actor: dict, data: bytes, photo_dir: Path | None = None,
              db_path: Path | None = None) -> dict:
    """Attach the photo of an observation, once, by the person who made it."""
    if not data:
        raise SurveyError("The photo is empty.")
    _check_photo(data)
    con = db.connect(db_path)
    try:
        o = con.execute("SELECT o.*, g.pilot FROM gvp_observation o JOIN gvp g ON g.id = o.gvp_id WHERE o.id = ?",
                        (observation_id,)).fetchone()
        if o is None or o["pilot"] != pilot:
            raise SurveyError("Unknown observation.", 404)
        if o["user_name"] != actor.get("name"):
            raise SurveyError("Only the person who made the observation can add its photo.", 403)
        if o["photo_path"]:
            raise SurveyError("This observation already has a photo.", 409)
        digest = hashlib.sha256(data).hexdigest()
        folder = Path(photo_dir or PHOTO_DIR) / "gvp"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{observation_id}-{digest[:12]}.img"
        path.write_bytes(data)
        with con:
            con.execute("UPDATE gvp_observation SET photo_path = ?, photo_sha256 = ? WHERE id = ?", (str(path), digest, observation_id))
        return {"observation_id": observation_id, "sha256": digest}
    finally:
        con.close()


def record_event(pilot: str, gvp_id: str, actor: dict, kind: str, value: str | None = None, note: str | None = None,
                 db_path: Path | None = None) -> dict:
    """A supervisor records an intervention, changes the status, or takes the GVP on or off the routes."""
    if actor.get("role") not in MANAGERS:
        raise SurveyError("Only a survey supervisor records interventions and status.", 403)
    con = db.connect(db_path)
    try:
        g = _get(con, pilot, gvp_id)
        _check_sector(actor, g["sector"], "This garbage vulnerable point")
        with con:
            if kind == "status":
                if value not in db.GVP_STATUSES:
                    raise SurveyError(f"status must be one of {db.GVP_STATUSES}")
                con.execute("UPDATE gvp SET status = ? WHERE id = ?", (value, gvp_id))
            elif kind == "collect":
                value = "1" if str(value).lower() in ("1", "true", "yes") else "0"
                con.execute("UPDATE gvp SET collect = ? WHERE id = ?", (int(value), gvp_id))
            elif kind not in db.GVP_INTERVENTIONS:
                raise SurveyError(f"kind must be 'status', 'collect' or one of {db.GVP_INTERVENTIONS}")
            con.execute("INSERT INTO gvp_event (id, gvp_id, at, user_name, user_role, kind, value, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (db.new_id(), gvp_id, db.now(), actor.get("name"), actor.get("role"), kind, value, (note or "").strip() or None))
        return detail(pilot, gvp_id, db_path)
    finally:
        con.close()


def _summary(g: dict, last: dict | None, n_obs: int, n_int: int) -> dict:
    per_day = build_ups_per_day()
    kg_day = round(last["quantity_kg"] * per_day[last["frequency"]], 1) if last else 0.0
    return {**g, "collect": bool(g["collect"]), "observations": n_obs, "interventions": n_int,
            "reported_by_public": g["created_role"] in PUBLIC,
            "last_observed_at": last["at"] if last else None, "streams": last["streams"] if last else [],
            "quantity_kg": last["quantity_kg"] if last else None, "frequency": last["frequency"] if last else None,
            "kg_per_day": kg_day, "on_routes": g["status"] == "active" and bool(g["collect"])}


def _obs_row(r) -> dict:
    d = dict(r)
    d["streams"], d["sources"] = json.loads(d["streams"]), json.loads(d["sources"])
    d["has_photo"] = bool(d.pop("photo_path"))
    return d


def list_gvps(pilot: str, sector: str | None = None, db_path: Path | None = None) -> list[dict]:
    con = db.connect(db_path)
    try:
        q, args = "SELECT * FROM gvp WHERE pilot = ?", [pilot]
        if sector:
            q += " AND sector = ?"
            args.append(sector)
        out = []
        for g in con.execute(q + " ORDER BY created_at", args):
            last = con.execute("SELECT * FROM gvp_observation WHERE gvp_id = ? ORDER BY at DESC, rowid DESC LIMIT 1", (g["id"],)).fetchone()
            n_obs = con.execute("SELECT COUNT(*) FROM gvp_observation WHERE gvp_id = ?", (g["id"],)).fetchone()[0]
            n_int = con.execute("SELECT COUNT(*) FROM gvp_event WHERE gvp_id = ? AND kind NOT IN ('status', 'collect')", (g["id"],)).fetchone()[0]
            out.append(_summary(dict(g), _obs_row(last) if last else None, n_obs, n_int))
        return out
    finally:
        con.close()


def detail(pilot: str, gvp_id: str, db_path: Path | None = None) -> dict:
    con = db.connect(db_path)
    try:
        g = dict(_get(con, pilot, gvp_id))
        obs = [_obs_row(r) for r in con.execute("SELECT * FROM gvp_observation WHERE gvp_id = ? ORDER BY at, rowid", (gvp_id,))]
        events = [dict(r) for r in con.execute("SELECT * FROM gvp_event WHERE gvp_id = ? ORDER BY at, rowid", (gvp_id,))]
        n_int = sum(1 for e in events if e["kind"] not in ("status", "collect"))
        return {**_summary(g, obs[-1] if obs else None, len(obs), n_int), "observation_log": obs, "events": events}
    finally:
        con.close()


def geojson(pilot: str, db_path: Path | None = None) -> dict:
    """All GVPs of the pilot for publishing (r. 15(1)): location, status and latest assessment."""
    keep = ("id", "sector", "landmark", "status", "created_at", "last_observed_at", "streams", "quantity_kg", "frequency",
            "kg_per_day", "observations", "interventions")
    return {"type": "FeatureCollection", "rule": rule(),
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [g["lon"], g["lat"]]},
                          "properties": {k: g[k] for k in keep}} for g in list_gvps(pilot, db_path=db_path)]}


def collection_points(pilot: str, sector: str, db_path: Path | None = None) -> list[dict]:
    """Active GVPs marked for collection, as route collection points (same shape as street-run points).
    Their waste is split evenly over the streams seen; it is an estimate until pickups are weighed."""
    out = []
    for g in list_gvps(pilot, sector, db_path):
        if not g["on_routes"] or g["kg_per_day"] <= 0:
            continue
        seen = [s for s in g["streams"] if s in STREAMS] or ["dry"]
        kg = {s: (g["kg_per_day"] / len(seen) if s in seen else 0.0) for s in STREAMS}
        out.append({
            "id": f"GVP:{g['id']}", "use": "gvp", "is_gvp": True, "sector": g["sector"],
            "label": "Garbage vulnerable point" + (f": {g['landmark'] or g['road_name']}" if g["landmark"] or g.get("road_name") else ""),
            "buildings": 0, "building_ids": [], "building_kg": [[round(kg[s], 2) for s in STREAMS]],
            "kg": {s: round(v, 1) for s, v in kg.items()}, "total_kg": round(sum(kg.values()), 1),
            "home_composted_kg": 0.0, "span_m": 0.0, "min_width_m": None, "lon": g["lon"], "lat": g["lat"],
            # The vehicle drives the road the GVP is on.
            "path_nodes": [g["road_u"], g["road_v"]] if g.get("road_u") is not None else [],
            "reported_by_public": g["reported_by_public"],
        })
    return out
