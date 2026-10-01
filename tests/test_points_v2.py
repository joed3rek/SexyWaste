"""Route Builder v2 collection points. Uses the cached HSR data; skipped where it has not been downloaded."""

import json

import pytest

from backend.config import CACHE_DIR
from backend.routing import points as P

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "pilots" / "hsr" / "buildings.geojson").exists()
                                or not (CACHE_DIR / "hsr.graphml").exists(),
                                reason="HSR pilot data not cached")


@pytest.fixture(scope="module")
def runs_points():
    return P.collection_points("hsr", {}, params={"max_run_m": 150, "max_buildings": 40, "min_buildings": 3})


def _use_of(pilot_table, ids):
    return {pilot_table[i] for i in ids}


def test_every_building_once_and_uses_never_mixed(runs_points):
    pts = runs_points
    t = P.generator_table("hsr", {}).set_index("id")["use"].to_dict()
    seen = [b for p in pts for b in p["building_ids"]]
    assert len(seen) == len(set(seen)), "a building appears in two points"
    assert set(seen) == set(t), "some generating buildings have no point"
    for p in pts:
        assert _use_of(t, p["building_ids"]) == {p["use"]}


def _confirmed_bwg_state():
    """Survey state that confirms the largest apartment-type building as a BWG on floor area."""
    import math
    from backend.buildings import generators
    from tests.test_buildings import state
    t = generators.base_table("hsr")
    row = t[t["category"] == "residential_apartment"].sort_values("footprint_m2").iloc[-1]
    floors = math.ceil(20000 / row["footprint_m2"]) + 1
    return row["id"], {row["id"]: state({"building_use": "apartment_society", "floors": floors})}


def test_without_surveys_no_bwg_points_are_split_out(runs_points):
    assert not [p for p in runs_points if p.get("is_bwg")], "only confirmed BWGs become separate points"


def test_confirmed_bwg_is_a_separate_point_without_wet_waste():
    bid, states = _confirmed_bwg_state()
    pts = P.collection_points("hsr", states)
    bwg = [p for p in pts if p.get("is_bwg")]
    assert [p["building_ids"] for p in bwg] == [[bid]]
    p = bwg[0]
    assert p["kg"]["wet"] == 0 and p["wet_excluded"] and p["wet_kg_excluded"] > 0
    assert p["onsite_processing"] == "not_surveyed"


def test_street_run_ids_are_stable(runs_points):
    P.street_runs.cache_clear()
    again = P.collection_points("hsr", {}, params={"max_run_m": 150, "max_buildings": 40, "min_buildings": 3})
    assert {p["id"] for p in again} == {p["id"] for p in runs_points}


def test_street_runs_respect_length_cap():
    runs, _ = P.street_runs("hsr", 150.0)
    multi = runs[runs["seg_ids"].apply(len) > 1]
    assert (multi["length_m"] <= 150 + 1e-6).all()


def test_points_respect_building_cap(runs_points):
    assert all(p["buildings"] <= 40 for p in runs_points if not p.get("is_bwg") and not p.get("merged_from"))


def test_service_time_per_building_is_clamped_1_to_5(runs_points):
    cls = P.vehicle_class("e_loader_3w")["service"]
    p = next(x for x in runs_points if not x.get("is_bwg") and x["buildings"] >= 10)
    no_travel = dict(p, span_m=0)
    minutes = P.service_minutes(no_travel, "e_loader_3w", ["wet", "dry"])
    served = sum(1 for row in p["building_kg"] if row[0] + row[1] > 0)
    assert cls["min_minutes"] * served <= minutes <= cls["max_minutes"] * served


def test_reference_overrides_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OVERRIDES_DIR", tmp_path)
    (tmp_path / "vehicles.json").write_text(json.dumps({"classes": [{"key": "cng_3w", "payload_kg": {"value": 999}}]}), encoding="utf-8")
    assert P.vehicle_class("cng_3w")["payload_kg"]["value"] == 999
    assert P.vehicle_class("cng_3w")["payload_kg"]["source"] == "bajaj_maxima_c"
    assert P.vehicle_class("mini_tipper")["payload_kg"]["value"] == 750


def test_vehicle_reference_every_figure_has_source_or_flag():
    for c in P.reference("vehicles")["classes"]:
        for field in ("body_volume_m3", "payload_kg", "compaction_ratio", "compartments", "min_road_width_m"):
            f = c[field]
            assert "value" in f and "sourced" in f
            assert (f["sourced"] and f.get("source")) or not f["sourced"]


def test_cached_points_match_a_fresh_build(runs_points):
    fresh = P.points_street_runs("hsr", {}, 150, 40, 3, 120)
    assert sorted(runs_points, key=lambda p: p["id"]) == sorted(fresh, key=lambda p: p["id"])


def test_cache_rebuilds_when_a_survey_changes():
    before = P.collection_points("hsr", {}, "Sector 4")
    target = next(p for p in before if not p.get("is_bwg") and p["use"] == "residential")
    bid = target["building_ids"][0]
    from tests.test_buildings import state
    after = P.collection_points("hsr", {bid: state({"building_use": "vacant_or_abandoned"})}, "Sector 4")
    assert bid not in {b for p in after for b in p["building_ids"]}, "a vacant building should drop out"


def test_callers_cannot_change_cached_points():
    first = P.collection_points("hsr", {}, "Sector 4")
    first[0]["kg"]["dry"] = -1
    first[0]["service_min"] = 99
    again = P.collection_points("hsr", {}, "Sector 4")
    assert again[0]["kg"]["dry"] >= 0 and "service_min" not in again[0]


def test_missing_street_names_are_ignored_under_pandas_2_and_3():
    # pandas 3 delivers missing names as float NaN, which is truthy and cannot be sorted with strings.
    import pandas as pd
    seg = pd.DataFrame({"u": [1, 1, 1, 2], "v": [2, 3, 4, 5], "name": ["B Road", float("nan"), None, "A Street"]})
    at = P._names_at_nodes(seg)
    assert at[1] == {"B Road"}
    assert P._cross_streets(at, 1, None) == ["B Road"]
    assert P._cross_streets({1: {"B Road", float("nan"), "A Street"}}, 1, "A Street") == ["B Road"]
    assert P._street_name(pd.NA) is None and P._street_name("  ") is None and P._street_name(" 7th Cross ") == "7th Cross"
