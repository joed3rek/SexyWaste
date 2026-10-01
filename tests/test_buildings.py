"""Building classification, estimation and bulk waste generator status from survey state. No network needed."""

import pytest

from backend.buildings import generators as g
from backend.survey import uses


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


# ---------- Survey state helpers ----------

def state(fields=None, mix=None, respondent="owner", outcome="completed"):
    """A survey state as backend/survey/state.py builds it. fields: {name: value or (value, source)};
    mix: [(use, {field: value or (value, source)})]."""
    def f(v):
        value, source = v if isinstance(v, tuple) else (v, "surveyed")
        return {"value": value, "source": source, "respondent": respondent, "recorded_at": "2026-10-01T00:00:00+00:00",
                "recorded_by": "Asha", "visit_id": "v1", "note": None}
    return {"fields": {k: f(v) for k, v in (fields or {}).items()},
            "use_mix": [{"id": f"m{i}", "use": u, "fields": {k: f(v) for k, v in fl.items()}} for i, (u, fl) in enumerate(mix or [])],
            "visit": {"outcome": outcome, "respondent": respondent, "ended_at": None, "user_name": "Asha"}}


# ---------- Estimation ----------

def test_streams_sum_to_total_and_fractions_sum_to_dry():
    p = g.compute(base())
    assert sum(p[s] for s in g.STREAMS) == pytest.approx(p["kg_day"], abs=0.05)
    assert sum(p[f"dry_{f}"] for f in g.FRACTIONS) == pytest.approx(p["dry"], abs=0.05)
    assert p["quantity_source"].startswith("estimate") and p["quantity_input_source"] == "assumed"


def test_mixed_building_sums_its_use_mix_rows():
    # 12 occupants x 0.5 + 30 PG beds x 0.4 + 2 shops x 2.0 + 1 cafe x 15.0 = 6 + 12 + 4 + 15 = 37 kg/day
    mix = [("residential_dwelling", {"count": 4, "occupants_total": 12}), ("pg_coliving", {"count": 1, "beds_total": 30}),
           ("shop_retail", {"count": 2}), ("food_service", {"count": 1})]
    p = g.compute(base(footprint_m2=300), state({"building_use": "mixed_use_shops_below"}, mix))
    assert p["kg_day"] == pytest.approx(37.0, abs=0.01)
    assert p["quantity_basis"] == "use_mix" and p["quantity_is_estimate"]
    assert p["quantity_input_source"] == "surveyed" and p["quantity_respondents"] == ["owner"]
    assert uses.contradictions("mixed_use_shops_below", [{"use": u, **fl} for u, fl in mix]) == []


def test_occupants_replace_persons_per_dwelling():
    with_occ = g.compute(base(), state({"building_use": "independent_house"}, [("residential_dwelling", {"count": 1, "occupants_total": 6})]))
    without = g.compute(base(), state({"building_use": "independent_house"}, [("residential_dwelling", {"count": 1})]))
    assert with_occ["kg_day"] == pytest.approx(3.0) and without["kg_day"] == pytest.approx(1.9)


def test_beds_use_the_per_bed_rate_and_fall_back_to_per_unit():
    assert g.compute(base(), state({}, [("hostel", {"count": 1, "beds_total": 100})]))["kg_day"] == pytest.approx(45.0)
    assert g.compute(base(), state({}, [("hostel", {"count": 1})]))["kg_day"] == pytest.approx(13.5)


def test_building_use_without_use_mix_uses_its_typology_and_geometry():
    p = g.compute(base(category="residential_house", footprint_m2=500), state({"building_use": "apartment_society", "floors": 4}))
    assert p["category"] == "residential_apartment" and p["quantity_basis"] == "building_use"
    assert p["dwellings"] == 20  # 500 m2 x 4 floors / 100 m2 per apartment


def test_weakest_source_is_reported():
    p = g.compute(base(), state({"building_use": "commercial_shops"}, [("shop_retail", {"count": (3, "verified")}),
                                                                       ("office", {"count": (2, "surveyed")})]))
    assert p["quantity_input_source"] == "surveyed"


def test_weighed_value_replaces_the_estimate():
    p = g.compute(base(), state({"building_use": "independent_house", "weighed_kg_day": (10, "weighed")},
                                [("residential_dwelling", {"count": 2})]))
    assert p["kg_day"] == 10 and p["quantity_source"] == "weighed" and not p["quantity_is_estimate"]
    assert sum(p[s] for s in g.STREAMS) == pytest.approx(10, abs=0.05)


def test_weighed_value_with_zero_estimate_is_split_by_stream_shares():
    p = g.compute(base(), state({"building_use": "vacant_or_abandoned", "weighed_kg_day": (8, "weighed")}))
    assert p["kg_day"] == 8
    assert p["wet"] == pytest.approx(8 * g.NORMS["stream_shares"]["household"]["wet"], abs=0.01)


def test_vacant_building_generates_nothing():
    p = g.compute(base(), state({"building_use": "vacant_or_abandoned"}))
    assert p["kg_day"] == 0 and p["collection"]["mode"] == "none"


# ---------- Building use drives collection and BWG entity ----------

def test_building_use_drives_collection_mode_and_bwg_entity():
    market = g.compute(base(), state({"building_use": "market"}))
    assert market["collection"]["mode"] == "daily_bulk" and "39(19)" in market["collection"]["rule"]
    assert market["bwg_entity"]["group"] == "commercial"
    assert g.compute(base(), state({"building_use": "independent_house"}))["bwg_status"] == "not_applicable"


def test_without_survey_the_osm_category_sets_collection_and_entity():
    p = g.compute(base(category="food_service"))
    assert p["collection"]["mode"] == "daily_commercial" and p["bwg_entity"]["group"] == "commercial"
    assert p["building_use"] is None and p["building_use_hint"] == "food_establishment"


# ---------- BWG status ----------

def test_independent_house_is_never_a_bwg():
    assert g.compute(base(footprint_m2=9000, osm_levels=4))["bwg_status"] == "not_applicable"


def test_surveyed_use_mix_over_100_kg_is_only_likely():
    # 60 homes x 3.8 persons x 0.5 kg = 114 kg/day: an estimate, however good the counts.
    p = g.compute(base(category="residential_apartment", footprint_m2=500),
                  state({"building_use": "apartment_society", "onsite_processing": "none"}, [("residential_dwelling", {"count": 60})]))
    assert p["kg_day"] == pytest.approx(114, abs=0.5)
    assert p["bwg_status"] == "bwg_likely" and p["round2_candidate"]
    assert p["bwg_compliance"] is None, "never a compliance label on an estimate"
    assert p["collection"]["mode"] == "door_to_door"


def test_surveyed_floors_and_building_use_confirm_on_floor_area():
    p = g.compute(base(category="commercial_office", footprint_m2=2100), state({"building_use": "commercial_offices", "floors": 10}))
    assert p["floor_area_m2"] == 21000
    assert p["bwg_status"] == "bwg_confirmed" and not p["round2_candidate"]
    assert any("floor area" in c for c in p["bwg_criteria"])
    assert p["bwg_compliance"] == "not_verified" and p["collection"]["mode"] == "bwg"


def test_floor_area_with_assumed_floors_is_only_likely():
    p = g.compute(base(category="commercial_office", footprint_m2=4100, osm_levels=None), state({"building_use": "commercial_offices"}))
    assert p["levels_source"] == "assumed" and p["floor_area_m2"] >= 20000
    assert p["bwg_status"] == "bwg_likely"


def test_osm_only_building_can_be_likely_but_not_confirmed():
    p = g.compute(base(category="commercial_office", footprint_m2=2100, osm_levels=10))
    assert p["bwg_status"] == "bwg_likely"


def test_weighed_waste_with_surveyed_entity_confirms():
    p = g.compute(base(category="residential_apartment"), state({"building_use": "apartment_society", "weighed_kg_day": (130, "weighed")}))
    assert p["bwg_status"] == "bwg_confirmed"


def test_water_is_not_evaluated_for_now():
    p = g.compute(base(category="commercial_office", footprint_m2=200),
                  state({"building_use": "commercial_offices", "water_lpd": 45000, "water_source": "bwssb"}))
    assert p["bwg_status"] in ("no", "watch") and not any("water" in c for c in p["bwg_criteria"])


@pytest.mark.parametrize("onsite,expected", [("compost", "processing_at_source"), ("biogas", "processing_at_source"),
                                             ("certificate", "ebwgr_certificate"), ("none", "non_compliant"), (None, "not_verified")])
def test_compliance_labels_only_for_confirmed_bwgs(onsite, expected):
    fields = {"building_use": "apartment_society", "floors": 10}
    if onsite:
        fields["onsite_processing"] = onsite
    p = g.compute(base(category="residential_apartment", footprint_m2=2500), state(fields))
    assert p["bwg_status"] == "bwg_confirmed" and p["bwg_compliance"] == expected


def test_watch_when_near_a_threshold():
    # 15,000 m2 is within 70% of the 20,000 m2 threshold; 2 offices make only 3 kg/day.
    p = g.compute(base(category="commercial_office", footprint_m2=1500),
                  state({"building_use": "commercial_offices", "floors": 10}, [("office", {"count": 2})]))
    assert p["floor_area_m2"] == 15000 and p["kg_day"] == pytest.approx(3.0) and p["bwg_status"] == "watch"


# ---------- Survey state counts ----------

def test_refused_visit_is_not_surveyed():
    p = g.compute(base(), state({}, outcome="refused"))
    assert p["visit_outcome"] == "refused" and not p["surveyed"]
    assert g.compute(base(), state({"building_use": "independent_house"}))["surveyed"]
