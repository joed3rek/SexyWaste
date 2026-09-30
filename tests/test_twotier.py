"""Two-tier planner: transfer station suggestion, capacities, fleet balance. Needs cached HSR data."""

import pytest

from backend.config import CACHE_DIR
from backend.routing import points as P
from backend.routing import twotier as T

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

DEPOT, MRF = (77.6445, 12.9170), (77.6300, 12.9050)


@pytest.fixture(scope="module")
def result():
    return T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=DEPOT, mrf=MRF,
                              primary_fleet=[{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}],
                              secondary_fleet=[{"type": "rear_loader_compactor", "count": 1}], time_limit_s=15))


def test_suggested_stations_are_on_main_roads():
    st = T.suggest_stations("hsr", "Sector 4", 500)
    assert st and all(s["on_main_road"] for s in st)


def test_all_waste_collected_and_balanced(result):
    s = result["summary"]
    assert s["points_served"] == s["points"] and s["uncollected_kg"] == 0
    times = [v["total_min"] for v in result["primary"]["vehicles"]]
    assert max(times) <= 2 * min(times), "fleet work should be balanced"


def test_trips_respect_vehicle_capacity(result):
    for v in result["primary"]["vehicles"]:
        cap = P.vehicle_class(v["type"])["payload_kg"]["value"]
        for t in v["trips"]:
            assert t["kg"] <= cap + 1 and t["fill_pct"] <= 101


def test_truck_trips_move_all_station_waste(result):
    moved = sum(t["kg"] for tr in result["secondary"]["trucks"] for t in tr["trips"])
    assert moved == pytest.approx(result["summary"]["kg_collected"], rel=0.01)


def test_bwg_wet_waste_is_not_routed(result):
    assert result["summary"]["bwg_wet_excluded_kg"] > 0


def test_rejects_truck_as_primary_vehicle():
    with pytest.raises(ValueError):
        T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=DEPOT, mrf=MRF,
                           primary_fleet=[{"type": "rear_loader_compactor", "count": 1}], secondary_fleet=[]))


def test_suggested_stations_balanced_and_within_two_truck_loads():
    st = T.suggest_stations("hsr", "Sector 1", 500, secondary_fleet=[{"type": "rear_loader_compactor", "count": 1}])
    cap, _ = T.station_capacity_kg([{"type": "rear_loader_compactor", "count": 1}], list(T.STREAMS))
    loads = [s["kg"] for s in st]
    assert max(loads) <= cap
    assert max(loads) <= 2.2 * min(loads), "station loads should be similar"


def test_vehicles_work_separate_territories(result):
    seen = {}
    for v in result["primary"]["vehicles"]:
        for t in v["trips"]:
            for pid in t["point_ids"]:
                seen.setdefault(pid, set()).add(v["id"])
    assert all(len(vs) == 1 for vs in seen.values()), "a collection point is visited by two vehicles"
    assert all(len({t["station"] for t in v["trips"]}) <= 3 for v in result["primary"]["vehicles"])
