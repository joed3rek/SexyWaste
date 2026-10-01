"""Routing service and matrices, on small hand-built networks so no download is needed."""

import networkx as nx
import numpy as np
import pytest

from backend.routing.matrix import UNREACHABLE_S, RoutingService, SpeedModel
from backend.routing.network import add_travel_times


def _ring():
    """1 -> 2 -> 3 -> 1, one-way, 100 m residential edges (36 s each at 10 km/h), plus an isolated node 9."""
    G = nx.MultiDiGraph()
    for n, (x, y) in {1: (77.0, 12.0), 2: (77.001, 12.0), 3: (77.0005, 12.001), 9: (78.0, 13.0)}.items():
        G.add_node(n, x=x, y=y)
    for u, v in ((1, 2), (2, 3), (3, 1)):
        G.add_edge(u, v, length=100.0, highway="residential")
    return add_travel_times(G)


def test_matrix_is_directed_and_matches_pairwise_times():
    rs = RoutingService(_ring())
    m = rs.matrix([1, 2, 3])
    assert m.time_s.tolist() == [[0, 36, 72], [72, 0, 36], [36, 72, 0]]
    for i, a in enumerate(m.nodes):
        for j, b in enumerate(m.nodes):
            assert m.time_s[i, j] == pytest.approx(rs.time_s(a, b))


def test_distance_matrix_in_metres():
    m = RoutingService(_ring()).matrix([1, 2, 3], distances=True)
    assert m.dist_m.tolist() == [[0, 100, 200], [200, 0, 100], [100, 200, 0]]


def test_unreachable_pairs_get_a_large_finite_time():
    rs = RoutingService(_ring())
    assert rs.time_s(1, 9) == UNREACHABLE_S
    m = rs.matrix([1, 9])
    assert np.isfinite(m.time_s).all() and m.time_s[0, 1] == UNREACHABLE_S


def test_nearest_nodes():
    rs = RoutingService(_ring())
    assert rs.nearest_nodes([[77.0009, 12.0001], [77.99, 12.99]]) == [2, 9]


def test_speed_model_changes_times_without_changing_callers():
    class Rush(SpeedModel):
        name = "rush_hour"

        def edge_seconds(self, data):
            return 2 * float(data["travel_time"])

    assert RoutingService(_ring(), Rush()).time_s(1, 2) == pytest.approx(72.0)
    assert RoutingService(_ring()).time_s(1, 2) == pytest.approx(36.0)


def test_rows_are_reused_and_bounded():
    rs = RoutingService(_ring(), max_rows=2)
    first = rs.times_from(1)
    assert rs.times_from(1) is first, "a cached row should be reused"
    rs.times_from(2)
    rs.times_from(3)
    assert len(rs._time_from.rows) == 2 and 1 not in rs._time_from.rows


def test_route_geometry_follows_one_way_streets():
    coords, length = RoutingService(_ring()).route([2, 1])
    assert length == pytest.approx(200.0), "2 -> 1 must go round through 3"
    assert len(coords) == 3
