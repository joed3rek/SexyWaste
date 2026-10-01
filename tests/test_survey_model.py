"""Round 1 survey data model: append-only values, the resolver, building-use config and the legacy migration."""

import json
import sqlite3

import pytest

from backend import regulations as regs
from backend.buildings.generators import NORMS
from backend.survey import db, migrate, resolve, uses

ACTOR = {"name": "Asha", "role": "surveyor"}


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "survey.db")
    yield c
    c.close()


def _visit(con, building="way/1", purpose="survey", user="Asha"):
    vid = db.new_id()
    con.execute("INSERT INTO visit (id, pilot, building_id, round, purpose, user_name, user_role, started_at)"
                " VALUES (?, 'hsr', ?, 'quick', ?, ?, 'surveyor', ?)", (vid, building, purpose, user, db.now()))
    return vid


# ---------- field_value is append-only ----------

def test_field_value_cannot_be_updated_or_deleted(con):
    v = _visit(con)
    fid = db.record_value(con, "building", "way/1", "floors", 3, "surveyed", v, ACTOR)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE field_value SET value = '4' WHERE id = ?", (fid,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM field_value WHERE id = ?", (fid,))


def test_use_mix_rows_are_never_deleted_only_removed(con):
    v = _visit(con)
    con.execute("INSERT INTO use_mix (id, pilot, building_id, use, created_visit_id) VALUES ('m1', 'hsr', 'way/1', 'shop_retail', ?)", (v,))
    con.execute("UPDATE use_mix SET status = 'removed', removed_visit_id = ? WHERE id = 'm1'", (v,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM use_mix WHERE id = 'm1'")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE use_mix SET use = 'office' WHERE id = 'm1'")


def test_no_code_updates_or_deletes_field_value():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "backend"
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8").upper()
        assert "UPDATE FIELD_VALUE" not in text and "DELETE FROM FIELD_VALUE" not in text, f


# ---------- the resolver ----------

def test_resolver_prefers_stronger_source_then_latest(con):
    v = _visit(con)
    db.record_value(con, "building", "way/1", "floors", 2, "assumed", None, None, recorded_at="2026-01-03T00:00:00+00:00")
    db.record_value(con, "building", "way/1", "floors", 3, "surveyed", v, ACTOR, recorded_at="2026-01-01T00:00:00+00:00")
    db.record_value(con, "building", "way/1", "floors", 4, "surveyed", v, ACTOR, recorded_at="2026-01-02T00:00:00+00:00")
    got = resolve.resolve(con, "building", ["way/1"])["way/1"]["floors"]
    assert got["value"] == 4 and got["source"] == "surveyed", "latest surveyed beats older surveyed and newer assumed"
    db.record_value(con, "building", "way/1", "floors", 5, "weighed", v, ACTOR, recorded_at="2025-01-01T00:00:00+00:00")
    assert resolve.resolve(con, "building", ["way/1"])["way/1"]["floors"]["value"] == 5, "weighed wins even if older"


def test_resolver_breaks_equal_timestamps_by_insertion_order(con):
    t = "2026-01-01T00:00:00+00:00"
    db.record_value(con, "building", "way/1", "society_name", "A", "surveyed", None, ACTOR, recorded_at=t)
    db.record_value(con, "building", "way/1", "society_name", "B", "surveyed", None, ACTOR, recorded_at=t)
    assert resolve.resolve(con, "building", ["way/1"])["way/1"]["society_name"]["value"] == "B"


def test_verified_value_keeps_the_surveyed_value_it_confirms(con):
    v = _visit(con)
    spot = _visit(con, purpose="spot_check", user="Meena")
    db.record_value(con, "building", "way/1", "building_use", "apartment_society", "surveyed", v, ACTOR)
    db.record_value(con, "building", "way/1", "building_use", "apartment_society", "verified", spot,
                    {"name": "Meena", "role": "survey_supervisor"}, "Spot-check matched.")
    current = resolve.resolve(con, "building", ["way/1"])["way/1"]["building_use"]
    assert current["source"] == "verified"
    assert [h["source"] for h in resolve.history(con, "building", "way/1")] == ["surveyed", "verified"]


def test_schema_accepts_a_round_2_unit_entity_without_changes(con, monkeypatch):
    monkeypatch.setattr(db, "ENTITY_TYPES", (*db.ENTITY_TYPES, "unit"))
    db.record_value(con, "unit", "unit-1", "occupants", 4, "surveyed", None, ACTOR)
    assert resolve.resolve(con, "unit", ["unit-1"])["unit-1"]["occupants"]["value"] == 4
    with pytest.raises(ValueError):
        db.record_value(con, "plot", "p1", "x", 1, "surveyed", None, ACTOR)


# ---------- building-use configuration ----------

def test_every_building_use_has_typology_collection_mode_and_valid_bwg_entity():
    cfg = uses.config()
    typology = NORMS["building_use_typology"]
    for key, bu in cfg["building_uses"].items():
        assert typology[key] in NORMS["default_levels"], key
        assert bu["collection_mode"] in cfg["collection_modes"]
        assert all(u in cfg["uses"] for u in bu["default_use_mix"])
        uses.bwg_entity(key)  # raises if the entity text is not in the regulations library
    assert set(cfg["uses"]) == set(db.USES)


def test_bwg_entities_quote_the_regulations_library():
    groups = regs.swm()["generator_types"]["bulk_waste_generator"]["entity_groups"]
    assert uses.bwg_entity("apartment_society") == {"group": "residential", "entity": "residential societies",
                                                    "rule": "SWM Rules 2026, r. 3(1)(i)"}
    assert uses.bwg_entity("independent_house") is None
    assert uses.bwg_entity("hotel_guesthouse")["entity"] in groups["commercial"]


def test_building_use_drives_collection_mode():
    assert uses.collection("market")["mode"] == "daily_bulk" and "39(19)" in uses.collection("market")["rule"]
    assert uses.collection("food_establishment")["mode"] == "daily_commercial"
    assert uses.collection("under_construction")["mode"] == "cnd_on_request"


def test_every_use_has_an_assumed_rate_and_stream_shares():
    for use in db.USES:
        n = NORMS["use_mix"][use]
        assert n["stream_shares"] in NORMS["stream_shares"]
        if n["basis"] == "per_bed":
            assert n["kg_per_bed"] > 0 and n["kg_per_unit"] > 0
        elif n["basis"] == "per_unit":
            assert n["kg_per_unit"] > 0
    assert "ASSUMPTIONS" in NORMS["use_mix"]["_status"] and "Round 3" in NORMS["use_mix"]["_calibration"]


# ---------- contradictions ----------

def test_mixed_building_with_flats_pg_shops_and_cafe_raises_no_contradiction():
    rows = [{"use": "residential_dwelling", "count": 4, "occupants_total": 12}, {"use": "pg_coliving", "count": 1, "beds_total": 30},
            {"use": "shop_retail", "count": 2}, {"use": "food_service", "count": 1}]
    assert uses.contradictions("mixed_use_shops_below", rows) == []


def test_independent_house_with_30_pg_beds_is_a_contradiction():
    warn = uses.contradictions("independent_house", [{"use": "residential_dwelling", "count": 1},
                                                     {"use": "pg_coliving", "count": 1, "beds_total": 30}])
    assert len(warn) == 1 and warn[0]["code"] == "warn_over" and warn[0]["total"] == 30
    assert "30 beds" in warn[0]["message"]


def test_offices_without_office_rows_and_vacant_with_homes_are_contradictions():
    assert uses.contradictions("commercial_offices", [{"use": "shop_retail", "count": 3}])
    assert uses.contradictions("vacant_or_abandoned", [{"use": "residential_dwelling", "count": 2}])
    assert uses.contradictions("commercial_offices", []) == [], "no rows yet: nothing to contradict"


# ---------- legacy migration ----------

OLD_SCHEMA = """CREATE TABLE building_survey (pilot TEXT NOT NULL, building_id TEXT NOT NULL, use TEXT, units INTEGER,
 commercial_units INTEGER, floors REAL, water_lpd REAL, water_source TEXT, onsite_processing TEXT, segregation_observed TEXT,
 weighed_kg_day REAL, surveyor TEXT, notes TEXT, updated_at TEXT NOT NULL, home_compost TEXT, home_compost_method TEXT,
 home_compost_kg REAL, PRIMARY KEY (pilot, building_id))"""


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "survey.db"
    c = sqlite3.connect(path)
    c.execute(OLD_SCHEMA)
    c.executemany("INSERT INTO building_survey (pilot, building_id, use, units, commercial_units, floors, water_lpd, water_source,"
                  " onsite_processing, segregation_observed, weighed_kg_day, surveyor, notes, updated_at, home_compost)"
                  " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                      ("hsr", "way/1", "mixed", 6, 2, 4, None, None, None, "partial", None, "Ravi", "corner plot", "2026-09-01T10:00:00+00:00", "some"),
                      ("hsr", "way/2", "residential", 40, None, 8, 30000, "bwssb", "compost", None, None, "Ravi", None, "2026-09-02T10:00:00+00:00", None),
                      ("hsr", "way/3", "commercial", 1, None, None, None, None, None, None, 12.5, None, None, "2026-09-03T10:00:00+00:00", None),
                  ])
    c.commit()
    c.close()
    return path


def test_legacy_migration_is_correct(legacy_db):
    out = migrate.migrate_legacy(legacy_db, osm={"way/3": {"category": "food_service", "sub_use": None},
                                                 "way/1": {"category": "mixed_use", "sub_use": "food_service"}})
    assert out["status"] == "migrated" and out["records"] == 3
    con = db.connect(legacy_db)
    visits = {r["building_id"]: dict(r) for r in con.execute("SELECT * FROM visit")}
    assert all(v["round"] == "quick" and v["outcome"] == "completed" and v["respondent"] == "observed_only"
               and v["user_name"] == "legacy" for v in visits.values())
    assert visits["way/1"]["started_at"] == "2026-09-01T10:00:00+00:00" and "Ravi" in visits["way/1"]["notes"]
    b = resolve.resolve(con, "building")
    assert b["way/1"]["building_use"]["value"] == "mixed_use_shops_below" and b["way/1"]["building_use"]["source"] == "surveyed"
    assert "legacy" in b["way/1"]["building_use"]["note"]
    assert b["way/2"]["building_use"]["value"] == "apartment_society"
    assert b["way/3"]["building_use"]["value"] == "food_establishment"
    assert b["way/2"]["water_lpd"]["value"] == 30000 and b["way/2"]["onsite_processing"]["value"] == "compost"
    assert b["way/3"]["weighed_kg_day"]["source"] == "weighed"
    assert b["way/1"]["home_compost"]["value"] == "some" and b["way/1"]["segregation_observed"]["value"] == "partial"
    mix = {(r["building_id"], r["use"]): r["id"] for r in con.execute("SELECT * FROM use_mix")}
    counts = resolve.resolve(con, "use_mix")
    assert counts[mix[("way/1", "residential_dwelling")]]["count"]["value"] == 4
    assert counts[mix[("way/1", "food_service")]]["count"]["value"] == 2
    assert counts[mix[("way/2", "residential_dwelling")]]["count"]["value"] == 40
    assert counts[mix[("way/3", "food_service")]]["count"]["value"] == 1
    with pytest.raises(sqlite3.IntegrityError, match="read-only"):
        con.execute("UPDATE building_survey_legacy SET floors = 9")
    assert con.execute("SELECT COUNT(*) FROM building_survey_legacy").fetchone()[0] == 3
    con.close()


def test_legacy_migration_is_idempotent(legacy_db):
    migrate.migrate_legacy(legacy_db)
    con = db.connect(legacy_db)
    before = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("visit", "use_mix", "field_value")]
    con.close()
    assert migrate.migrate_legacy(legacy_db)["status"] == "already migrated"
    con = db.connect(legacy_db)
    assert [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("visit", "use_mix", "field_value")] == before
    assert json.loads(con.execute("SELECT value FROM meta WHERE key = 'legacy_migration'").fetchone()[0])["records"] == 3
    con.close()


def test_migration_with_nothing_to_migrate(tmp_path):
    assert migrate.migrate_legacy(tmp_path / "empty.db")["status"] == "nothing to migrate"
