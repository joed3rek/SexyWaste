"""One-time, idempotent migration of the old building_survey table into the Round 1 model.

For each old record: one quick visit (outcome completed, respondent observed_only, the original
timestamp) by the placeholder user "legacy"; the old use mapped to a building use; units turned
into use-mix rows; floors, water, on-site processing, segregation observed, weighed waste and
in-building composting kept as field values with a legacy note. The old table is then renamed
building_survey_legacy and made read-only.

Run: .venv\\Scripts\\python -m backend.survey.migrate
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from backend.survey import db

LEGACY_USER = {"name": "legacy", "role": None}
_NS = uuid.UUID("6f1c2d3e-5a4b-4c8d-9e0f-112233445566")
_OSM_TO_COMMERCIAL = {"food_service": "food_establishment", "commercial_office": "commercial_offices",
                      "hotel": "hotel_guesthouse", "market": "market", "healthcare": "hospital_clinic"}
_OSM_TO_INSTITUTIONAL = {"educational": "school_college", "healthcare": "hospital_clinic", "religious": "religious"}
# Use-mix row for the units of a non-residential building use.
_PRIMARY_USE = {"commercial_shops": "shop_retail", "food_establishment": "food_service", "commercial_offices": "office",
                "hotel_guesthouse": "hotel_guesthouse", "market": "market_stall", "hospital_clinic": "clinic_healthcare",
                "school_college": "educational", "religious": "religious", "government_public": "institutional"}
_SUB_USE = {"food_service": "food_service", "commercial_office": "office", "clinic": "clinic_healthcare",
            "commercial_retail": "shop_retail"}
_CARRIED = {  # old column -> (field, source)
    "water_lpd": ("water_lpd", "surveyed"), "water_source": ("water_source", "surveyed"),
    "onsite_processing": ("onsite_processing", "surveyed"), "segregation_observed": ("segregation_observed", "surveyed"),
    "weighed_kg_day": ("weighed_kg_day", "weighed"),
    "home_compost": ("home_compost", "surveyed"), "home_compost_method": ("home_compost_method", "surveyed"),
    "home_compost_kg": ("home_compost_kg", "surveyed"),
}


def map_building_use(old_use: str | None, units: int | None, osm_category: str | None,
                     apartment_if_units_over: int) -> str | None:
    """The closest Round 1 building use for an old survey use."""
    if old_use == "residential":
        apt = (units or 1) > apartment_if_units_over or osm_category == "residential_apartment"
        return "apartment_society" if apt else "independent_house"
    if old_use == "commercial":
        return _OSM_TO_COMMERCIAL.get(osm_category, "commercial_shops")
    if old_use == "mixed":
        return "mixed_use_shops_below"
    if old_use == "institutional":
        return _OSM_TO_INSTITUTIONAL.get(osm_category, "government_public")
    if old_use == "vacant":
        return "vacant_or_abandoned"
    if old_use == "construction":
        return "under_construction"
    return None


def use_mix_rows(building_use: str | None, units: int | None, commercial_units: int | None,
                 osm_sub_use: str | None) -> list[tuple[str, int]]:
    """(use, count) rows implied by the old unit counts."""
    if building_use is None or units is None:
        return []
    if building_use in ("independent_house", "apartment_society"):
        return [("residential_dwelling", units)] if units > 0 else []
    if building_use == "mixed_use_shops_below":
        com = min(commercial_units or 0, units)
        rows = [("residential_dwelling", units - com)] if units - com > 0 else []
        return rows + ([(_SUB_USE.get(osm_sub_use, "shop_retail"), com)] if com > 0 else [])
    use = _PRIMARY_USE.get(building_use)
    return [(use, units)] if use and units > 0 else []


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def migrate_legacy(db_path: Path | None = None, osm: dict[str, dict] | None = None) -> dict:
    """Migrate building_survey once. `osm` maps building id -> {"category", "sub_use"} to refine the
    mapping; without it, uses map from the old values alone. Returns a summary."""
    from backend.buildings.generators import NORMS

    con = db.connect(db_path)
    tables = _tables(con)
    if "building_survey" not in tables:
        return {"status": "nothing to migrate" if "building_survey_legacy" not in tables else "already migrated", "records": 0}
    if "building_survey_legacy" in tables:
        raise RuntimeError("Both building_survey and building_survey_legacy exist. Records were saved to the old table "
                           "after migration; merge them by hand before running again.")
    cols = {r[1] for r in con.execute("PRAGMA table_info(building_survey)")}
    rows = [dict(r) for r in con.execute("SELECT * FROM building_survey ORDER BY updated_at")]
    over = NORMS["surveyed_use"]["apartment_if_units_over"]
    osm = osm or {}
    counts = {"records": 0, "building_use": 0, "use_mix_rows": 0, "field_values": 0}
    with con:
        for r in rows:
            pilot, bid, at = r["pilot"], r["building_id"], r["updated_at"]
            vid = str(uuid.uuid5(_NS, f"visit:{pilot}:{bid}"))
            notes = "; ".join(x for x in (f"Migrated from the legacy survey. Surveyor: {r.get('surveyor') or 'not recorded'}.",
                                          r.get("notes")) if x)
            con.execute("INSERT INTO visit (id, pilot, building_id, round, purpose, user_name, user_role, started_at, ended_at,"
                        " outcome, respondent, notes) VALUES (?, ?, ?, 'quick', 'survey', ?, NULL, ?, ?, 'completed', 'observed_only', ?)",
                        (vid, pilot, bid, LEGACY_USER["name"], at, at, notes))
            o = osm.get(bid, {})
            bu = map_building_use(r.get("use"), r.get("units"), o.get("category"), over)
            if bu:
                detail = f"use '{r.get('use')}'" + (f", {r['units']} units" if r.get("units") is not None else "")
                db.record_value(con, "building", bid, "building_use", bu, "surveyed", vid, LEGACY_USER,
                                f"Mapped from the legacy survey ({detail}).", at)
                counts["building_use"] += 1
            if r.get("floors") is not None:
                db.record_value(con, "building", bid, "floors", r["floors"], "surveyed", vid, LEGACY_USER, "Legacy survey.", at)
                counts["field_values"] += 1
            for use, n in use_mix_rows(bu, r.get("units"), r.get("commercial_units"), o.get("sub_use")):
                mid = str(uuid.uuid5(_NS, f"use_mix:{pilot}:{bid}:{use}"))
                con.execute("INSERT INTO use_mix (id, pilot, building_id, use, created_visit_id) VALUES (?, ?, ?, ?, ?)",
                            (mid, pilot, bid, use, vid))
                db.record_value(con, "use_mix", mid, "count", n, "surveyed", vid, LEGACY_USER,
                                "From the legacy survey's unit counts.", at)
                counts["use_mix_rows"] += 1
            for col, (field, source) in _CARRIED.items():
                if col in cols and r.get(col) is not None:
                    db.record_value(con, "building", bid, field, r[col], source, vid, LEGACY_USER, "Legacy survey.", at)
                    counts["field_values"] += 1
            counts["records"] += 1
        con.execute("ALTER TABLE building_survey RENAME TO building_survey_legacy")
        for op in ("INSERT", "UPDATE", "DELETE"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS legacy_no_{op.lower()} BEFORE {op} ON building_survey_legacy "
                        "BEGIN SELECT RAISE(ABORT, 'building_survey_legacy is read-only'); END")
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_migration', ?)",
                    (json.dumps({**counts, "at": db.now()}),))
    con.close()
    return {"status": "migrated", **counts}


def _osm_lookup(pilot: str) -> dict[str, dict]:
    from backend.buildings import generators
    t = generators.base_table(pilot)
    return {r["id"]: {"category": r["category"], "sub_use": r["sub_use"]} for _, r in t[["id", "category", "sub_use"]].iterrows()}


if __name__ == "__main__":
    con = db.connect()
    pilots = []
    if "building_survey" in _tables(con):
        pilots = [r[0] for r in con.execute("SELECT DISTINCT pilot FROM building_survey")]
    con.close()
    osm = {}
    for p in pilots:
        osm.update(_osm_lookup(p))
    print(migrate_legacy(osm=osm))
