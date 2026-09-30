"""Routing tests on a small hand-built road grid, so no network download is needed."""

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from backend.routing import network
from backend.routing.network import add_travel_times
from backend.routing.planner import PlanRequest, plan_routes
from backend.routing.solver import solve_greedy_baseline, solve_optimised, travel_time_matrix


def grid_graph(size: int = 5, spacing_deg: float = 0.001) -> nx.MultiDiGraph:
    """Two-way residential street grid near Bengaluru."""
    G = nx.MultiDiGraph(crs="epsg:4326")
    node = lambda i, j: i * size + j  # noqa: E731
    for i in range(size):
        for j in range(size):
            G.add_node(node(i, j), x=77.6 + j * spacing_deg, y=12.98 + i * spacing_deg)
    for i in range(size):
        for j in range(size):
            for di, dj in ((0, 1), (1, 0)):
                if i + di < size and j + dj < size:
                    a, b = node(i, j), node(i + di, j + dj)
                    G.add_edge(a, b, length=110.0, highway="residential")
                    G.add_edge(b, a, length=110.0, highway="residential")
    return add_travel_times(G)


def test_travel_time_respects_one_way():
    G = nx.MultiDiGraph()
    G.add_node(1, x=0, y=0)
    G.add_node(2, x=0, y=0)
    G.add_node(3, x=0, y=0)
    G.add_edge(1, 2, length=100.0, highway="residential")  # one-way 1 -> 2
    G.add_edge(2, 3, length=100.0, highway="residential")
    G.add_edge(3, 1, length=100.0, highway="residential")
    add_travel_times(G)
    m = travel_time_matrix(G, [1, 2])
    assert m[0][1] == pytest.approx(36.0)  # 100 m at 10 km/h
    assert m[1][0] == pytest.approx(72.0)  # must go round via node 3


def _simple_problem():
    # depot, 4 points, facility; all 60 s apart.
    n = 6
    matrix = [[0 if i == j else 60 for j in range(n)] for i in range(n)]
    demands = [0, 400, 400, 400, 400, 0]
    service = [0, 30, 30, 30, 30, 0]
    return matrix, demands, service


@pytest.mark.parametrize("solver", ["optimised", "baseline"])
def test_capacity_is_respected_and_all_points_served(solver):
    matrix, demands, service = _simple_problem()
    args = (matrix, demands, service, 2, 800, 3600)
    plan = solve_optimised(*args, time_limit_s=1) if solver == "optimised" else solve_greedy_baseline(*args)
    assert plan.dropped == []
    assert sorted(p for r in plan.routes for p in r) == [1, 2, 3, 4]
    for route in plan.routes:
        assert sum(demands[p] for p in route) <= 800


@pytest.mark.parametrize("solver", ["optimised", "baseline"])
def test_points_dropped_when_fleet_too_small(solver):
    matrix, demands, service = _simple_problem()
    args = (matrix, demands, service, 1, 800, 3600)
    plan = solve_optimised(*args, time_limit_s=1) if solver == "optimised" else solve_greedy_baseline(*args)
    assert len(plan.dropped) == 2
    assert sum(len(r) for r in plan.routes) == 2


def test_shift_length_limits_route():
    matrix, demands, service = _simple_problem()
    # Depot -> p -> facility takes 60 + 30 + 60 = 150 s; a second stop pushes it to 240 s.
    plan = solve_optimised(matrix, demands, service, 1, 10_000, 200, time_limit_s=1)
    assert sum(len(r) for r in plan.routes) == 1


def test_plan_routes_on_grid_optimised_not_worse_than_baseline():
    G = grid_graph()
    points = [
        {"id": f"P{k}", "lon": G.nodes[n]["x"], "lat": G.nodes[n]["y"], "demand_kg": {"wet": 200}, "service_min": 2}
        for k, n in enumerate([4, 20, 24, 12, 7, 17, 9, 15], start=1)
    ]
    req = PlanRequest(
        depot=(G.nodes[0]["x"], G.nodes[0]["y"]),
        facility=(G.nodes[24]["x"], G.nodes[24]["y"]),
        points=points,
        stream="wet",
        num_vehicles=2,
        capacity_kg=1000,
        shift_min=120,
        time_limit_s=1,
    )
    result = plan_routes(G, req)
    opt, base = result["optimised"]["totals"], result["baseline"]["totals"]
    assert opt["points_served"] == 8
    assert opt["total_min"] <= base["total_min"] + 1e-6
    for v in result["optimised"]["vehicles"]:
        assert v["load_kg"] <= 1000
        assert len(v["geometry"]["coordinates"]) >= 2


def test_api_end_to_end(monkeypatch):
    from backend.api import main

    G = grid_graph()
    import pandas as pd

    monkeypatch.setattr(main, "load_graph", lambda key: G)
    monkeypatch.setattr(main, "pilot_sectors", lambda key: pd.DataFrame({"name": ["Sector 1"]}))
    monkeypatch.setattr(
        main, "default_depot_and_facility", lambda G, key: (0, 24)
    )
    client = TestClient(main.app)

    assert client.get("/api/areas").status_code == 200
    assert client.get("/api/areas/nowhere/network").status_code == 404
    assert len(client.get("/api/areas/shivajinagar/network").json()["features"]) > 0

    scenario = client.get("/api/areas/shivajinagar/scenario?n_points=10&seed=3").json()
    assert len(scenario["points"]) == 10

    res = client.post(
        "/api/areas/shivajinagar/solve",
        json={**{k: scenario[k] for k in ("depot", "facility", "points")}, "stream": "dry", "num_vehicles": 2, "time_limit_s": 1},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["optimised"]["totals"]["points_served"] + body["optimised"]["totals"]["points_dropped"] == 10

    bad = client.post("/api/areas/shivajinagar/solve", json={**{k: scenario[k] for k in ("depot", "facility", "points")}, "stream": "plastic"})
    assert bad.status_code == 422


def test_road_class_parses_list_strings():
    assert network._road_class("['residential', 'tertiary']") == "residential"
    assert network._road_class("primary_link") == "primary"
    assert network._road_class(None) == ""
