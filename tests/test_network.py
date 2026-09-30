"""Road network helpers, on a small hand-built grid so no download is needed."""

import networkx as nx
import pytest

from backend.routing import network
from backend.routing.network import add_travel_times
from backend.routing.twotier import _Times


def test_travel_time_respects_one_way():
    G = nx.MultiDiGraph()
    G.add_node(1, x=0, y=0)
    G.add_node(2, x=0, y=0)
    G.add_node(3, x=0, y=0)
    G.add_edge(1, 2, length=100.0, highway="residential")  # one-way 1 -> 2
    G.add_edge(2, 3, length=100.0, highway="residential")
    G.add_edge(3, 1, length=100.0, highway="residential")
    add_travel_times(G)
    t = _Times(G)
    assert t.t(1, 2) == pytest.approx(36.0)  # 100 m at 10 km/h
    assert t.t(2, 1) == pytest.approx(72.0)  # must go round via node 3


def test_road_class_parses_list_strings():
    assert network._road_class("['residential', 'tertiary']") == "residential"
    assert network._road_class("primary_link") == "primary"
    assert network._road_class(None) == ""
