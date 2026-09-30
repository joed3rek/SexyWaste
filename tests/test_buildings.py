"""Building classification, estimation, bulk waste generator checks and the survey store. No network needed."""

import pytest

from backend.buildings import generators as g
from backend.survey import store


def base(**kw):
    rec = {"id": "way/1", "sector": "Sector 1", "house_number": None, "street": None, "name": None, "address": None,
           "category": "residential_house", "sub_use": None, "basis": "test", "confidence": "low",
           "osm_building_tag": "yes", "landuse": None, "places_inside": [], "footprint_m2": 100, "osm_levels": None}
    rec.update(kw)
    return rec


# ---------- Classification ----------

def test_commercial_complex_with_one_clinic_is_not_a_hospital():
    cat, _, _, _ = g._classify("retail", "commercial", ["clinic", "commercial_retail", "food_service", "public_institutional"], 5000)
    assert cat == "commercial_retail"


def test_hospital_anchor_wins_in_untagged_building():
    cat, _, _, _ = g._classify("yes", None, ["healthcare", "commercial_retail"], 3000)
    assert cat == "healthcare"


def test_house_with_shop_inside_is_mixed_use():
    cat, sub, _, _ = g._classify("house", "residential", ["food_service"], 120)
    assert (cat, sub) == ("mixed_use", "food_service")


def test_untagged_building_without_landuse_is_low_confidence_household():
    cat, _, _, conf = g._classify("yes", None, [], 120)
    assert (cat, conf) == ("unclassified", "low")


def test_poi_category_handles_missing_tags():
    assert g.poi_category({"amenity": "restaurant"}) == "food_service"
    assert g.poi_category({"amenity": "clinic"}) == "clinic"
    assert g.poi_category({"amenity": "bench"}) is None


# ---------- Estimation and BWG ----------

def test_streams_sum_to_total_and_fractions_sum_to_dry():
    p = g.compute(base())
    assert sum(p[s] for s in g.STREAMS) == pytest.approx(p["kg_day"], abs=0.05)
    assert sum(p[f"dry_{f}"] for f in g.FRACTIONS) == pytest.approx(p["dry"], abs=0.05)
    assert p["quantity_source"].startswith("estimate")


def test_independent_house_is_never_a_bwg():
    p = g.compute(base(footprint_m2=9000, osm_levels=4))
    assert p["bwg_status"] == "not_applicable"


def test_apartment_crosses_100_kg_from_surveyed_units():
    # 60 units x 3.8 persons x 0.5 kg = 114 kg/day
    p = g.compute(base(category="residential_apartment", footprint_m2=500), {"use": "residential", "units": 60})
    assert p["kg_day"] == pytest.approx(114, abs=0.5)
    assert p["bwg_status"] == "bwg"
    assert p["bwg_compliance"] == "not_verified"


def test_floor_area_criterion_uses_footprint_times_floors():
    p = g.compute(base(category="commercial_office", footprint_m2=2100), {"use": "commercial", "units": 1, "floors": 10})
    assert p["floor_area_m2"] == 21000
    assert p["bwg_status"] == "bwg"
    assert any("floor area" in c for c in p["bwg_criteria"])


def test_water_is_only_checked_when_recorded():
    small = base(category="commercial_office", footprint_m2=200)
    assert not any("water" in c for c in g.compute(small, {"use": "commercial", "units": 1})["bwg_criteria"])
    p = g.compute(small, {"use": "commercial", "units": 1, "water_lpd": 45000, "water_source": "bwssb"})
    assert p["bwg_status"] == "bwg" and any("BWSSB" in c for c in p["bwg_criteria"])


@pytest.mark.parametrize("onsite,expected", [("compost", "processing_at_source"), ("biogas", "processing_at_source"),
                                             ("certificate", "ebwgr_certificate"), ("none", "non_compliant")])
def test_bwg_compliance_from_onsite_processing(onsite, expected):
    p = g.compute(base(category="residential_apartment"), {"use": "residential", "units": 80, "onsite_processing": onsite})
    assert p["bwg_compliance"] == expected


def test_weighed_data_replaces_estimate():
    p = g.compute(base(), {"use": "residential", "units": 2, "weighed_kg_day": 10})
    assert p["kg_day"] == 10 and p["quantity_source"] == "weighed"
    assert sum(p[s] for s in g.STREAMS) == pytest.approx(10, abs=0.05)


def test_mixed_use_splits_residential_and_commercial_units():
    p = g.compute(base(sub_use="food_service"), {"use": "mixed", "units": 5, "commercial_units": 1})
    assert p["category"] == "mixed_use" and p["dwellings"] == 4


def test_vacant_building_generates_nothing():
    p = g.compute(base(), {"use": "vacant"})
    assert p["kg_day"] == 0 and p["collection"]["mode"] == "none"


# ---------- Survey store ----------

def test_survey_store_roundtrip_and_validation(tmp_path):
    db = tmp_path / "s.db"
    rec = store.save("hsr", "way/1", {"use": "residential", "units": "12", "onsite_processing": "compost"}, db_path=db)
    assert rec["units"] == 12
    assert store.get("hsr", "way/1", db_path=db)["use"] == "residential"
    with pytest.raises(ValueError):
        store.save("hsr", "way/1", {"use": "spaceship"}, db_path=db)
    with pytest.raises(ValueError):
        store.save("hsr", "way/1", {"use": "mixed", "units": 2, "commercial_units": 3}, db_path=db)
    store.delete("hsr", "way/1", db_path=db)
    assert store.get("hsr", "way/1", db_path=db) is None
