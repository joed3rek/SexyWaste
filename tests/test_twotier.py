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


def test_wet_waste_is_excluded_only_for_confirmed_bwgs(result):
    # The test database has no surveys, so no building is a confirmed BWG and all wet waste is routed.
    # test_points_v2 checks that a confirmed BWG's wet waste is excluded.
    assert result["summary"]["bwg_wet_excluded_kg"] == 0


def test_rejects_truck_as_primary_vehicle():
    with pytest.raises(ValueError):
        T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=DEPOT, mrf=MRF,
                           primary_fleet=[{"type": "rear_loader_compactor", "count": 1}], secondary_fleet=[]))


def test_suggested_stations_balanced_and_within_two_truck_loads():
    st = T.suggest_stations("hsr", "Sector 1", 500, secondary_fleet=[{"type": "rear_loader_compactor", "count": 1}])
    cap, _ = T.station_capacity_kg([{"type": "rear_loader_compactor", "count": 1}], list(T.STREAMS))
    loads = [s["kg"] for s in st]
    assert max(loads) <= cap
    # Stations placed to cover sparse areas can carry little; none should be overloaded.
    assert max(loads) <= 1.5 * sum(loads) / len(loads), "no station should carry far more than the average"


def test_vehicles_work_separate_territories(result):
    seen = {}
    for v in result["primary"]["vehicles"]:
        for t in v["trips"]:
            for pid in t["point_ids"]:
                seen.setdefault(pid, set()).add(v["id"])
    assert all(len(vs) == 1 for vs in seen.values()), "a collection point is visited by two vehicles"
    assert all(len({t["station"] for t in v["trips"]}) <= 3 for v in result["primary"]["vehicles"])


def test_each_truck_trip_carries_one_stream_within_capacity(result):
    cap = P.vehicle_class("rear_loader_compactor")["payload_kg"]["value"]
    trips = [t for tr in result["secondary"]["trucks"] for t in tr["trips"]]
    assert trips
    for t in trips:
        assert t["stream"] in T.STREAMS
        assert t["kg"] <= cap + 1 and t["fill_pct"] <= 101
        assert sum(t["by_station"].values()) == pytest.approx(t["kg"], abs=1)


def test_every_stream_at_every_station_reaches_the_mrf(result):
    moved = {}
    for tr in result["secondary"]["trucks"]:
        for t in tr["trips"]:
            for st, kg in t["by_station"].items():
                moved[(st, t["stream"])] = moved.get((st, t["stream"]), 0) + kg
    for st in result["stations"]:
        for stream, kg in st["kg_by_stream"].items():
            assert moved.get((st["id"], stream), 0) == pytest.approx(kg, abs=2), (st["id"], stream)
    assert result["summary"]["left_at_stations_kg"] == 0


def test_truck_km_comes_from_the_road_route(result):
    for tr in result["secondary"]["trucks"]:
        for t in tr["trips"]:
            assert t["km"] > 0 and len(t["geometry"]) >= 2
        assert tr["km"] == pytest.approx(sum(t["km"] for t in tr["trips"]), abs=0.05)


def test_trips_drive_every_collection_street(result):
    """A trip drives the street of every point it serves, not just a nearby junction."""
    import math
    from shapely.geometry import LineString, Point
    k = math.cos(math.radians(12.9))
    pts = {p["id"]: p for p in P.collection_points("hsr", {}, "Sector 4")}
    off = []
    for v in result["primary"]["vehicles"]:
        for t in v["trips"]:
            line = LineString([(x * 111320 * k, y * 110540) for x, y in t["geometry"]])
            for pid in {pid.split("#")[0] for pid in t["point_ids"]}:
                p = pts.get(pid)
                if p and len(p["path_nodes"]) > 1 and not p.get("merged_from"):
                    off.append(line.distance(Point(p["lon"] * 111320 * k, p["lat"] * 110540)))
    assert off and max(off) < 25


def test_resize_keeps_the_vehicle_mix():
    rows = [{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}]
    assert T._resize(rows, 9) == [{"type": "e_loader_3w", "count": 6}, {"type": "mini_tipper", "count": 3}]
    assert sum(r["count"] for r in T._resize(rows, 7)) == 7


def test_suggest_fleet_reaches_the_target():
    out = T.suggest_fleet(T.PlanInput(pilot="hsr", sector="Sector 4", depot=DEPOT, mrf=MRF,
                                      primary_fleet=[{"type": "e_loader_3w", "count": 2}],
                                      secondary_fleet=[{"type": "rear_loader_compactor", "count": 1}], time_limit_s=6), 6)
    assert out["fits"] and out["result"]["summary"]["time_to_complete_min"] <= 6 * 60
    assert {r["type"] for r in out["primary_fleet"]} == {"e_loader_3w"}
    assert any(a["code"] == "time_split" for a in out["advice"])
