"""Current survey state of each building, read from the Round 1 tables through the resolver.

    state = {
      "fields":   {field: {"value", "source", "respondent", "recorded_at", "recorded_by", "visit_id", "note"}},
      "use_mix":  [{"id", "use", "fields": {field: {...same...}}}],   # active rows only
      "visit":    {"outcome", "respondent", "ended_at", "user_name"} or None,  # latest survey visit
    }

Buildings with no visit and no values have no state. The legacy survey table is migrated the
first time states are read.
"""

from __future__ import annotations

from pathlib import Path

from backend.survey import db, resolve

_migrated: set[str] = set()


def _ensure_migrated(con, db_path: Path | None) -> None:
    key = str(db_path or db.DB_PATH)
    if key in _migrated:
        return
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "building_survey" in tables:
        from backend.survey import migrate
        pilots = [r[0] for r in con.execute("SELECT DISTINCT pilot FROM building_survey")]
        osm = {}
        for p in pilots:
            osm.update(migrate._osm_lookup(p))
        migrate.migrate_legacy(db_path, osm=osm)
    _migrated.add(key)


def building_states(pilot: str, building_ids: list[str] | None = None, db_path: Path | None = None) -> dict[str, dict]:
    con = db.connect(db_path)
    try:
        _ensure_migrated(con, db_path)
        where, args = "pilot = ?", [pilot]
        if building_ids is not None:
            if not building_ids:
                return {}
            where += f" AND building_id IN ({','.join('?' * len(building_ids))})"
            args += list(building_ids)
        mix_rows = [dict(r) for r in con.execute(f"SELECT id, building_id, use FROM use_mix WHERE {where} AND status = 'active'", args)]
        visits = {}
        for r in con.execute(f"SELECT building_id, outcome, respondent, ended_at, started_at, user_name FROM visit"
                             f" WHERE {where} AND purpose = 'survey' AND outcome IS NOT NULL ORDER BY started_at", args):
            visits[r["building_id"]] = {k: r[k] for k in ("outcome", "respondent", "ended_at", "user_name")}
        ids = building_ids
        if ids is None:
            ids = {r[0] for r in con.execute("SELECT DISTINCT entity_id FROM field_value WHERE entity_type = 'building'")}
            ids |= set(visits) | {m["building_id"] for m in mix_rows}
            ids = sorted(i for i in ids if i)
        fields = resolve.resolve(con, "building", list(ids))
        mix_fields = resolve.resolve(con, "use_mix", [m["id"] for m in mix_rows])
    finally:
        con.close()
    out: dict[str, dict] = {}
    for bid in ids:
        state = {"fields": fields.get(bid, {}), "use_mix": [], "visit": visits.get(bid)}
        out[bid] = state
    for m in mix_rows:
        if m["building_id"] in out:
            out[m["building_id"]]["use_mix"].append({"id": m["id"], "use": m["use"], "fields": mix_fields.get(m["id"], {})})
    return {bid: s for bid, s in out.items() if s["fields"] or s["use_mix"] or s["visit"]}


def building_state(pilot: str, building_id: str, db_path: Path | None = None) -> dict | None:
    return building_states(pilot, [building_id], db_path).get(building_id)


def fingerprint(states: dict) -> str:
    """Changes whenever any building's survey state changes (used to cache derived data)."""
    import hashlib
    import json
    return hashlib.sha1(json.dumps(states, sort_keys=True, default=str).encode("utf-8")).hexdigest()
