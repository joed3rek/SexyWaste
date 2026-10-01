"""Survey operations: visits, building fields, use mix, map problems, photos, spot-checks, coverage.

Every write goes through record_value() or a single INSERT here and records the acting person
and role. Login is a dummy: the actor and their sectors are whatever the browser sent, so the
sector and own-visit rules below stop honest mistakes, not determined misuse.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point

from backend.config import GPS_WARN_DISTANCE_M, PHOTO_DIR, PHOTO_MAX_BYTES, PHOTO_RETENTION_DAYS, SPOT_CHECK_RATE, SPOT_CHECK_WINDOW_DAYS
from backend.survey import db, resolve, uses


class SurveyError(Exception):
    """A request the survey rules reject. `status` is the HTTP status to return."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---------- Buildings and sectors ----------

@lru_cache(maxsize=4)
def _buildings(pilot: str) -> dict:
    """{building id: (sector, footprint polygon in metres)}."""
    from backend.buildings import generators
    from backend.buildings.layers import METRIC_CRS
    t = generators.base_table(pilot).to_crs(METRIC_CRS)
    return {r.id: (r.sector, r.geometry) for r in t.itertuples()}


@lru_cache(maxsize=4)
def _sectors(pilot: str):
    from backend.buildings import layers
    from backend.buildings.layers import METRIC_CRS
    return layers.sectors(pilot).to_crs(METRIC_CRS)[["name", "geometry"]]


def _to_metric(lon: float, lat: float) -> Point:
    from pyproj import Transformer
    from backend.buildings.layers import METRIC_CRS
    return Point(Transformer.from_crs(4326, METRIC_CRS, always_xy=True).transform(lon, lat))


def building_sector(pilot: str, building_id: str) -> str:
    b = _buildings(pilot).get(building_id)
    if not b:
        raise SurveyError(f"Unknown building '{building_id}'", 404)
    return b[0]


def point_sector(pilot: str, lon: float, lat: float) -> str | None:
    p = _to_metric(lon, lat)
    for r in _sectors(pilot).itertuples():
        if r.geometry.contains(p):
            return r.name
    return None


def _check_sector(actor: dict, sector: str | None, what: str) -> None:
    if sector not in (actor.get("sectors") or []):
        raise SurveyError(f"{what} is in {sector or 'no pilot sector'}, which is not one of your sectors "
                          f"({', '.join(actor.get('sectors') or []) or 'none chosen'}).", 403)


# ---------- Visits ----------

def _visit(con, visit_id: str) -> dict:
    row = con.execute("SELECT * FROM visit WHERE id = ?", (visit_id,)).fetchone()
    if not row:
        raise SurveyError(f"Unknown visit '{visit_id}'", 404)
    return dict(row)


def _open_visit(con, visit_id: str, actor: dict) -> dict:
    v = _visit(con, visit_id)
    if v["ended_at"]:
        raise SurveyError("This visit is closed. Open a new visit to record more.", 409)
    if v["user_name"] != actor.get("name"):
        raise SurveyError("Only the person who opened this visit can record in it.", 403)
    return v


def open_visit(pilot: str, actor: dict, building_id: str | None = None, purpose: str = "survey",
               spot_check_of: str | None = None, lon: float | None = None, lat: float | None = None,
               accuracy_m: float | None = None, db_path: Path | None = None) -> dict:
    """Open a quick visit. A surveyor surveys buildings in their sectors; a survey supervisor
    spot-checks other people's completed visits in their sectors."""
    if purpose not in db.PURPOSES:
        raise SurveyError(f"purpose must be one of {db.PURPOSES}")
    con = db.connect(db_path)
    try:
        if purpose == "spot_check":
            if actor.get("role") != "survey_supervisor":
                raise SurveyError("Only a survey supervisor can spot-check.", 403)
            if not spot_check_of:
                raise SurveyError("A spot-check needs the visit it checks (spot_check_of).")
            orig = _visit(con, spot_check_of)
            if orig["purpose"] != "survey" or orig["outcome"] not in ("completed", "partial"):
                raise SurveyError("Only a completed survey visit can be spot-checked.")
            if orig["user_name"] == actor.get("name"):
                raise SurveyError("You cannot spot-check your own visit.", 403)
            building_id = orig["building_id"]
        elif actor.get("role") != "surveyor":
            raise SurveyError("Only a surveyor can open a survey visit.", 403)
        if building_id:
            sector = building_sector(pilot, building_id)
        elif lon is not None and lat is not None:
            sector = point_sector(pilot, lon, lat)
        else:
            raise SurveyError("A visit needs a building, or a position for a building missing from the map.")
        _check_sector(actor, sector, "This building" if building_id else "This position")
        vid = db.new_id()
        with con:
            con.execute("INSERT INTO visit (id, pilot, building_id, round, purpose, spot_check_of, user_name, user_role,"
                        " started_at, gps_lat, gps_lon, gps_accuracy_m) VALUES (?, ?, ?, 'quick', ?, ?, ?, ?, ?, ?, ?, ?)",
                        (vid, pilot, building_id, purpose, spot_check_of, actor.get("name"), actor.get("role"), db.now(),
                         lat, lon, accuracy_m))
            warnings = []
            if building_id and lon is not None and lat is not None:
                dist = _buildings(pilot)[building_id][1].distance(_to_metric(lon, lat))
                if dist > GPS_WARN_DISTANCE_M:
                    warnings.append(f"Your position is {dist:.0f} m from this building. Check you are at the right building.")
                    _review(con, pilot, building_id, vid, "gps_far", {"distance_m": round(dist), "accuracy_m": accuracy_m})
        return {"visit": _visit(con, vid), "warnings": warnings}
    finally:
        con.close()


def close_visit(visit_id: str, actor: dict, outcome: str, notes: str | None = None,
                db_path: Path | None = None) -> dict:
    if outcome not in db.OUTCOMES:
        raise SurveyError(f"outcome must be one of {db.OUTCOMES}")
    con = db.connect(db_path)
    try:
        v = _open_visit(con, visit_id, actor)
        with con:
            con.execute("UPDATE visit SET ended_at = ?, outcome = ?, notes = COALESCE(?, notes) WHERE id = ?",
                        (db.now(), outcome, notes, visit_id))
            warnings = contradictions_for(con, v["pilot"], v["building_id"]) if v["building_id"] else []
            if warnings:
                _review(con, v["pilot"], v["building_id"], visit_id, "use_mix_contradiction", {"warnings": warnings})
        return {"visit": _visit(con, visit_id), "warnings": warnings}
    finally:
        con.close()


# ---------- Values ----------

def _check_value(spec, value, name: str):
    if value is None:
        raise SurveyError(f"{name} needs a value.")
    if spec == "number":
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise SurveyError(f"{name} must be a number.") from None
        if v < 0 or math.isnan(v):
            raise SurveyError(f"{name} cannot be negative.")
        return int(v) if v.is_integer() else v
    if spec == "text":
        return str(value).strip()[:200]
    if value not in spec:
        raise SurveyError(f"{name} must be one of {spec}.")
    return value


def _current_value(con, entity_type: str, entity_id: str, field: str):
    f = resolve.resolve(con, entity_type, [entity_id], [field]).get(entity_id, {}).get(field)
    return f["value"] if f else None


def _write(con, v: dict, actor: dict, entity_type: str, entity_id: str, field: str, value) -> str | None:
    """Record one value in a visit. In a spot-check, a value that matches the checked visit is
    recorded as verified; a different value is recorded as surveyed and the mismatch is logged.
    Returns a mismatch message or None."""
    if v["purpose"] != "spot_check":
        db.record_value(con, entity_type, entity_id, field, value, "surveyed", v["id"], actor)
        return None
    before = _current_value(con, entity_type, entity_id, field)
    if before == value:
        db.record_value(con, entity_type, entity_id, field, value, "verified", v["id"], actor, "Spot-check matched.")
        return None
    db.record_value(con, entity_type, entity_id, field, value, "surveyed", v["id"], actor,
                    f"Spot-check differs from the survey ({before!r}).")
    kind = "spot_check_building_use_mismatch" if field == "building_use" else "spot_check_mismatch"
    orig = _visit(con, v["spot_check_of"])
    _review(con, v["pilot"], v["building_id"], v["id"], kind,
            {"field": field, "entity_type": entity_type, "entity_id": entity_id, "surveyed": before, "checked": value,
             "surveyor": orig["user_name"], "checked_by": actor.get("name")})
    return f"{field}: survey said {before!r}, spot-check found {value!r}."


def set_building_fields(visit_id: str, actor: dict, fields: dict, db_path: Path | None = None) -> dict:
    """Record Round 1 building fields in an open visit. Values are appended, never overwritten."""
    con = db.connect(db_path)
    try:
        v = _open_visit(con, visit_id, actor)
        if not v["building_id"]:
            raise SurveyError("This visit has no building; report the missing building as a map problem.")
        mismatches = []
        with con:
            for name, value in fields.items():
                if name not in db.BUILDING_FIELDS:
                    raise SurveyError(f"Unknown building field '{name}'. Round 1 records {sorted(db.BUILDING_FIELDS)}.")
                spec = tuple(uses.config()["building_uses"]) if name == "building_use" else db.BUILDING_FIELDS[name]
                m = _write(con, v, actor, "building", v["building_id"], name, _check_value(spec, value, name))
                if m:
                    mismatches.append(m)
        return {"warnings": contradictions_for(con, v["pilot"], v["building_id"]), "mismatches": mismatches}
    finally:
        con.close()


def _mix_fields(use: str, values: dict) -> dict:
    allowed = uses.config()["uses"][use]["fields"]
    out = {}
    for name, value in values.items():
        if value is None:
            continue
        if name not in allowed:
            raise SurveyError(f"{name} is not recorded for {use}; it records {allowed}.")
        out[name] = _check_value(db.USE_MIX_FIELDS[name], value, name)
    return out


def add_use_mix(visit_id: str, actor: dict, use: str, values: dict, db_path: Path | None = None) -> dict:
    """Add a use-mix row (or, in a spot-check, check the surveyed row for the same use)."""
    if use not in db.USES:
        raise SurveyError(f"use must be one of {db.USES}")
    con = db.connect(db_path)
    try:
        v = _open_visit(con, visit_id, actor)
        if not v["building_id"]:
            raise SurveyError("This visit has no building.")
        fields = _mix_fields(use, values)
        mismatches = []
        with con:
            existing = con.execute("SELECT id FROM use_mix WHERE pilot = ? AND building_id = ? AND use = ? AND status = 'active'",
                                   (v["pilot"], v["building_id"], use)).fetchone()
            if existing and v["purpose"] != "spot_check":
                raise SurveyError(f"{use} is already recorded for this building; change that row instead.", 409)
            if existing:
                mid = existing[0]
            else:
                mid = db.new_id()
                con.execute("INSERT INTO use_mix (id, pilot, building_id, use, created_visit_id) VALUES (?, ?, ?, ?, ?)",
                            (mid, v["pilot"], v["building_id"], use, visit_id))
                if v["purpose"] == "spot_check":
                    mismatches.append(f"{use}: not in the survey, found by the spot-check.")
                    _review(con, v["pilot"], v["building_id"], visit_id, "spot_check_mismatch",
                            {"field": "use_mix", "use": use, "surveyed": None, "checked": "present",
                             "surveyor": _visit(con, v["spot_check_of"])["user_name"], "checked_by": actor.get("name")})
            for name, value in fields.items():
                m = _write(con, v, actor, "use_mix", mid, name, value)
                if m:
                    mismatches.append(m)
        return {"id": mid, "warnings": contradictions_for(con, v["pilot"], v["building_id"]), "mismatches": mismatches}
    finally:
        con.close()


def update_use_mix(visit_id: str, actor: dict, row_id: str, values: dict, remove: bool = False,
                   db_path: Path | None = None) -> dict:
    con = db.connect(db_path)
    try:
        v = _open_visit(con, visit_id, actor)
        row = con.execute("SELECT * FROM use_mix WHERE id = ? AND building_id = ?", (row_id, v["building_id"])).fetchone()
        if not row or row["status"] != "active":
            raise SurveyError("Unknown or removed use-mix row.", 404)
        mismatches = []
        with con:
            if remove:
                con.execute("UPDATE use_mix SET status = 'removed', removed_visit_id = ? WHERE id = ?", (visit_id, row_id))
                if v["purpose"] == "spot_check":
                    mismatches.append(f"{row['use']}: in the survey, not found by the spot-check.")
                    _review(con, v["pilot"], v["building_id"], visit_id, "spot_check_mismatch",
                            {"field": "use_mix", "use": row["use"], "surveyed": "present", "checked": None,
                             "surveyor": _visit(con, v["spot_check_of"])["user_name"], "checked_by": actor.get("name")})
            else:
                for name, value in _mix_fields(row["use"], values).items():
                    m = _write(con, v, actor, "use_mix", row_id, name, value)
                    if m:
                        mismatches.append(m)
        return {"warnings": contradictions_for(con, v["pilot"], v["building_id"]), "mismatches": mismatches}
    finally:
        con.close()


def contradictions_for(con, pilot: str, building_id: str) -> list[str]:
    bu = resolve.resolve(con, "building", [building_id], ["building_use"]).get(building_id, {}).get("building_use")
    if not bu:
        return []
    rows = [dict(r) for r in con.execute("SELECT id, use FROM use_mix WHERE pilot = ? AND building_id = ? AND status = 'active'",
                                         (pilot, building_id))]
    vals = resolve.resolve(con, "use_mix", [r["id"] for r in rows])
    return uses.contradictions(bu["value"], [{"use": r["use"], **{k: f["value"] for k, f in vals.get(r["id"], {}).items()}}
                                              for r in rows])


def _review(con, pilot, building_id, visit_id, kind, detail) -> None:
    con.execute("INSERT INTO review_item (id, pilot, building_id, visit_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (db.new_id(), pilot, building_id, visit_id, kind, json.dumps(detail, ensure_ascii=False), db.now()))


# ---------- Map problems and photos ----------

def add_geometry_flag(pilot: str, actor: dict, kind: str, building_id: str | None = None, lon: float | None = None,
                      lat: float | None = None, related_building_id: str | None = None, visit_id: str | None = None,
                      note: str | None = None, db_path: Path | None = None) -> dict:
    if kind not in db.FLAG_KINDS:
        raise SurveyError(f"kind must be one of {db.FLAG_KINDS}")
    if actor.get("role") not in ("surveyor", "survey_supervisor"):
        raise SurveyError("Only surveyors and survey supervisors report map problems.", 403)
    if kind == "missing_from_map":
        if lon is None or lat is None:
            raise SurveyError("Place a pin where the missing building is.")
        sector = point_sector(pilot, lon, lat)
    elif building_id:
        sector = building_sector(pilot, building_id)
    else:
        raise SurveyError("Choose the building this problem is about.")
    _check_sector(actor, sector, "This place")
    con = db.connect(db_path)
    try:
        fid = db.new_id()
        with con:
            con.execute("INSERT INTO geometry_flag (id, pilot, building_id, kind, lat, lon, related_building_id, visit_id, note,"
                        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (fid, pilot, building_id, kind, lat, lon, related_building_id, visit_id, note, actor.get("name"), db.now()))
        return dict(con.execute("SELECT * FROM geometry_flag WHERE id = ?", (fid,)).fetchone())
    finally:
        con.close()


def resolve_geometry_flag(flag_id: str, actor: dict, db_path: Path | None = None) -> None:
    if actor.get("role") != "survey_supervisor":
        raise SurveyError("Only a survey supervisor resolves map problems.", 403)
    con = db.connect(db_path)
    try:
        with con:
            n = con.execute("UPDATE geometry_flag SET status = 'resolved', resolved_by = ?, resolved_at = ? WHERE id = ? AND status = 'open'",
                            (actor.get("name"), db.now(), flag_id)).rowcount
        if not n:
            raise SurveyError("Unknown or already resolved map problem.", 404)
    finally:
        con.close()


def add_photo(visit_id: str, actor: dict, data: bytes, lon: float | None = None, lat: float | None = None,
              photo_dir: Path | None = None, db_path: Path | None = None) -> dict:
    """Store the one optional frontage photo of a Round 1 visit (no people, no interiors)."""
    if not data:
        raise SurveyError("The photo is empty.")
    if len(data) > PHOTO_MAX_BYTES:
        raise SurveyError(f"The photo is larger than {PHOTO_MAX_BYTES // (1024 * 1024)} MB.", 413)
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[8:12] == b"WEBP"):
        raise SurveyError("The photo must be a JPEG, PNG or WebP image.", 415)
    con = db.connect(db_path)
    try:
        v = _open_visit(con, visit_id, actor)
        if con.execute("SELECT 1 FROM photo WHERE visit_id = ?", (visit_id,)).fetchone():
            raise SurveyError("A quick visit has at most one frontage photo.", 409)
        digest = hashlib.sha256(data).hexdigest()
        folder = Path(photo_dir or PHOTO_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{visit_id}-{digest[:12]}.img"
        path.write_bytes(data)
        pid = db.new_id()
        with con:
            con.execute("INSERT INTO photo (id, visit_id, file_path, sha256, taken_at, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (pid, visit_id, str(path), digest, db.now(), lat, lon))
        return {"id": pid, "sha256": digest, "visit_id": v["id"]}
    finally:
        con.close()


def purge_old_photos(days: int = PHOTO_RETENTION_DAYS, db_path: Path | None = None) -> int:
    """Delete photo files older than the retention period. Their rows (with the hash) stay."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    con = db.connect(db_path)
    try:
        removed = 0
        for r in con.execute("SELECT file_path FROM photo WHERE taken_at < ?", (cutoff,)):
            p = Path(r[0])
            if p.exists():
                p.unlink()
                removed += 1
        return removed
    finally:
        con.close()


# ---------- Reading ----------

def building_detail(pilot: str, building_id: str, db_path: Path | None = None) -> dict:
    """Fields with their resolved value and source, use mix, visits, open map problems, contradictions."""
    con = db.connect(db_path)
    try:
        fields = resolve.resolve(con, "building", [building_id]).get(building_id, {})
        rows = [dict(r) for r in con.execute("SELECT id, use, status, created_visit_id FROM use_mix WHERE pilot = ? AND building_id = ?"
                                             " ORDER BY rowid", (pilot, building_id))]
        vals = resolve.resolve(con, "use_mix", [r["id"] for r in rows])
        visits = [dict(r) for r in con.execute("SELECT id, round, purpose, spot_check_of, user_name, user_role, started_at, ended_at,"
                                               " outcome, notes FROM visit WHERE pilot = ? AND building_id = ? ORDER BY started_at",
                                               (pilot, building_id))]
        flags = [dict(r) for r in con.execute("SELECT * FROM geometry_flag WHERE pilot = ? AND building_id = ? AND status = 'open'",
                                              (pilot, building_id))]
        return {"fields": fields,
                "use_mix": [{**r, "fields": vals.get(r["id"], {})} for r in rows if r["status"] == "active"],
                "visits": visits, "geometry_flags": flags, "contradictions": contradictions_for(con, pilot, building_id)}
    finally:
        con.close()


def history(entity_type: str, entity_id: str, db_path: Path | None = None) -> list[dict]:
    con = db.connect(db_path)
    try:
        return resolve.history(con, entity_type, entity_id)
    finally:
        con.close()


def spot_check_sample(pilot: str, actor: dict, rate: float = SPOT_CHECK_RATE, days: int = SPOT_CHECK_WINDOW_DAYS,
                      today: datetime | None = None, db_path: Path | None = None) -> list[dict]:
    """A random sample of each surveyor's completed quick visits in the supervisor's sectors over the
    last `days` (at least one per surveyor with visits), excluding the supervisor's own visits and
    visits already spot-checked. The sample is the same all week for the same inputs."""
    if actor.get("role") != "survey_supervisor":
        raise SurveyError("Only a survey supervisor has a spot-check queue.", 403)
    today = today or datetime.now(timezone.utc)
    since = (today - timedelta(days=days)).isoformat(timespec="seconds")
    sectors = set(actor.get("sectors") or [])
    con = db.connect(db_path)
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT v.* FROM visit v WHERE v.pilot = ? AND v.purpose = 'survey' AND v.round = 'quick' AND v.outcome = 'completed'"
            " AND v.started_at >= ? AND v.user_name IS NOT ? AND v.building_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM visit s WHERE s.spot_check_of = v.id) ORDER BY v.started_at, v.id",
            (pilot, since, actor.get("name")))]
    finally:
        con.close()
    rows = [r for r in rows if building_sector(pilot, r["building_id"]) in sectors]
    by_surveyor: dict[str, list[dict]] = {}
    for r in rows:
        by_surveyor.setdefault(r["user_name"] or "unknown", []).append(r)
    year, week, _ = today.isocalendar()
    out = []
    for name, visits in sorted(by_surveyor.items()):
        k = max(1, math.ceil(rate * len(visits)))
        rng = random.Random(f"{pilot}:{year}-W{week}:{name}")
        out += [{"visit_id": v["id"], "building_id": v["building_id"], "surveyor": name,
                 "sector": building_sector(pilot, v["building_id"]), "visited_at": v["started_at"]}
                for v in rng.sample(visits, min(k, len(visits)))]
    return out


def survey_summary(pilot: str, building_sectors: dict[str, str], db_path: Path | None = None) -> dict:
    """Coverage by outcome and sector, surveyed building uses, spot-check mismatch rates, open reviews.
    `building_sectors` maps every building id to its sector, so not-visited buildings are counted."""
    con = db.connect(db_path)
    try:
        latest = {}
        for r in con.execute("SELECT building_id, outcome, user_name FROM visit WHERE pilot = ? AND purpose = 'survey'"
                             " AND outcome IS NOT NULL ORDER BY started_at", (pilot,)):
            latest[r["building_id"]] = r["outcome"]
        uses_now = resolve.resolve(con, "building", fields=["building_use"])
        checks = [dict(r) for r in con.execute(
            "SELECT s.id AS check_id, o.user_name AS surveyor FROM visit s JOIN visit o ON o.id = s.spot_check_of"
            " WHERE s.pilot = ? AND s.purpose = 'spot_check'", (pilot,))]
        compared = {r["check_id"]: con.execute("SELECT COUNT(*) FROM field_value WHERE visit_id = ?", (r["check_id"],)).fetchone()[0]
                    for r in checks}
        mismatch = [dict(r) for r in con.execute("SELECT visit_id, kind FROM review_item WHERE pilot = ? AND kind LIKE 'spot_check%'", (pilot,))]
        open_reviews = {r[0]: r[1] for r in con.execute(
            "SELECT kind, COUNT(*) FROM review_item WHERE pilot = ? AND status = 'open' GROUP BY kind", (pilot,))}
        open_flags = con.execute("SELECT COUNT(*) FROM geometry_flag WHERE pilot = ? AND status = 'open'", (pilot,)).fetchone()[0]
    finally:
        con.close()

    by_sector: dict[str, dict] = {}
    for bid, sector in building_sectors.items():
        row = by_sector.setdefault(sector or "unknown", {"sector": sector, "buildings": 0, "not_visited": 0,
                                                          **{o: 0 for o in db.OUTCOMES}})
        row["buildings"] += 1
        outcome = latest.get(bid)
        row[outcome or "not_visited"] += 1
    totals = {k: sum(r[k] for r in by_sector.values()) for k in ("buildings", "not_visited", *db.OUTCOMES)}
    building_use_counts: dict[str, int] = {}
    for bid, f in uses_now.items():
        if bid in building_sectors and f["building_use"]["source"] in ("surveyed", "verified", "weighed"):
            u = f["building_use"]["value"]
            building_use_counts[u] = building_use_counts.get(u, 0) + 1
    per_surveyor: dict[str, dict] = {}
    surveyor_of = {r["check_id"]: r["surveyor"] for r in checks}
    for r in checks:
        s = per_surveyor.setdefault(r["surveyor"], {"surveyor": r["surveyor"], "spot_checks": 0, "values_checked": 0,
                                                    "mismatches": 0, "building_use_mismatches": 0})
        s["spot_checks"] += 1
        s["values_checked"] += compared[r["check_id"]]
    for m in mismatch:
        s = per_surveyor.get(surveyor_of.get(m["visit_id"]))
        if s:
            s["building_use_mismatches" if m["kind"] == "spot_check_building_use_mismatch" else "mismatches"] += 1
    for s in per_surveyor.values():
        n = s["values_checked"]
        s["mismatch_rate"] = round((s["mismatches"] + s["building_use_mismatches"]) / n, 3) if n else None
        s["building_use_mismatch_rate"] = round(s["building_use_mismatches"] / s["spot_checks"], 3) if s["spot_checks"] else None
    visited = totals["buildings"] - totals["not_visited"]
    return {
        "coverage": {**totals, "visited": visited,
                     "visited_pct": round(100 * visited / totals["buildings"], 1) if totals["buildings"] else 0.0},
        "coverage_by_sector": sorted(by_sector.values(), key=lambda r: str(r["sector"])),
        "building_use_counts": dict(sorted(building_use_counts.items(), key=lambda kv: -kv[1])),
        "spot_checks_by_surveyor": sorted(per_surveyor.values(), key=lambda s: s["surveyor"] or ""),
        "open_reviews": open_reviews,
        "open_geometry_flags": open_flags,
    }
