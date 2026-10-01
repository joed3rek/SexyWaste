"""Park composting and on-site (home) composting."""

import pytest

from backend.buildings import generators as g
from backend.config import CACHE_DIR
from backend.routing import parks as PK
from backend.routing import twotier as T
from backend.survey import store
from tests.test_buildings import state

HOUSE = dict(id="way/1", sector="Sector 1", house_number=None, street=None, name=None, address=None,
             category="residential_house", sub_use=None, basis="t", confidence="low", osm_building_tag="yes",
             landuse=None, places_inside=[], footprint_m2=100, osm_levels=None)
cached = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")


@pytest.mark.parametrize("answer,share", [("all", 1.0), ("most", 0.75), ("some", 0.4)])
def test_home_composting_share_reduces_wet_to_collect(answer, share):
    p = g.compute(HOUSE, state({"building_use": "independent_house", "home_compost": answer}, [("residential_dwelling", {"count": 4})]))
    assert p["wet_home_composted"] == pytest.approx(p["wet"] * share, abs=0.02)
    assert p["wet_to_collect"] == pytest.approx(p["wet"] - p["wet_home_composted"], abs=0.02)
    assert p["kg_day"] > p["wet_to_collect"]  # generation (for BWG tests) is unchanged


def test_home_composting_kg_is_capped_at_wet_waste():
    p = g.compute(HOUSE, state({"building_use": "independent_house", "home_compost": "some", "home_compost_kg": 999},
                               [("residential_dwelling", {"count": 1})]))
    assert p["wet_to_collect"] == 0 and p["home_compost_basis"] == "surveyed kg/day"


def test_store_clears_method_and_kg_when_not_composting(tmp_path):
    rec = store.save("hsr", "way/1", {"use": "residential", "home_compost": "no", "home_compost_kg": 3,
                                      "home_compost_method": "pit"}, db_path=tmp_path / "s.db")
    assert rec["home_compost_kg"] is None and rec["home_compost_method"] is None
    with pytest.raises(ValueError):
        store.save("hsr", "way/1", {"home_compost": "sometimes"}, db_path=tmp_path / "s.db")


def test_older_form_save_appends_a_visit_and_never_overwrites(tmp_path):
    from backend.survey import db as sdb
    from backend.survey import resolve
    from backend.survey import state as st
    path = tmp_path / "s.db"
    store.save("hsr", "way/1", {"use": "residential", "units": 2, "floors": 3, "surveyor": "Ravi"}, db_path=path)
    store.save("hsr", "way/1", {"use": "residential", "units": 5, "floors": 4}, actor={"name": "Asha", "role": "surveyor"}, db_path=path)
    s = st.building_state("hsr", "way/1", db_path=path)
    assert s["fields"]["building_use"]["value"] == "apartment_society" and s["fields"]["floors"]["value"] == 4
    assert [(m["use"], m["fields"]["count"]["value"]) for m in s["use_mix"]] == [("residential_dwelling", 5)]
    con = sdb.connect(path)
    assert [h["value"] for h in resolve.history(con, "building", "way/1") if h["field"] == "floors"] == [3, 4]
    assert con.execute("SELECT COUNT(*) FROM visit").fetchone()[0] == 2
    con.close()


def test_tier_shares_follow_park_size():
    assert [PK.tier_pct(a) for a in (500, 3000, 8000, 15000, 400000)] == [5, 4, 3, 2, 1]


def test_site_cap_comes_from_buffer_zone_rule():
    cap, rule = PK.site_cap_kg()
    assert cap == 5000 and rule == "SWM Rules 2026, r. 3(1)(h)"


@cached
def test_sites_exclude_private_parks_and_respect_cap():
    for sector in ("Sector 1", "Sector 3"):
        for s in PK.sites("hsr", sector):
            assert s["capacity_kg"] <= PK.site_cap_kg()[0]
            assert 1 <= s["share_pct"] <= 5
    ids = {s["id"] for sec in ("Sector 1", "Sector 2", "Sector 3", "Sector 4", "Sector 5", "Sector 6", "Sector 7")
           for s in PK.sites("hsr", sec)}
    import backend.buildings.layers as L
    private = set(L.parks("hsr").query("access == 'private'")["osm_id"])
    assert not ids & private


@cached
def test_plan_with_parks_diverts_wet_waste_within_capacity():
    r = T.plan(T.PlanInput(pilot="hsr", sector="Sector 1", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                           primary_fleet=[{"type": "e_loader_3w", "count": 10}, {"type": "mini_tipper", "count": 4}],
                           secondary_fleet=[{"type": "rear_loader_compactor", "count": 1}], time_limit_s=12,
                           park={"enabled": True}))
    c = r["summary"]["composting"]
    assert c["wet_to_parks_kg"] > 0
    assert c["compost_kg"] == pytest.approx(c["wet_to_parks_kg"] * 0.25, rel=0.02)
    for p in r["parks"]:
        assert p["received_kg"] <= p["capacity_kg"] + 1
    assert c["without_parks"]["kg_to_mrf"] > sum(s["kg"] for s in r["stations"])
    drops = [d for v in r["primary"]["vehicles"] for t in v["trips"] for d in t.get("park_drops", [])]
    assert drops, "vehicles should unload wet waste at park sites"
