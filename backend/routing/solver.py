"""Vehicle routing on a precomputed travel-time matrix.

Index convention for every function here:
    0      = depot (vehicles start here)
    1..n   = collection points
    n + 1  = unloading facility (MRF / transfer station; vehicles end here)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

UNREACHABLE_S = 10**7


@dataclass
class RoutePlan:
    routes: list[list[int]]  # per vehicle, the visited point indices in order (no depot/facility)
    dropped: list[int] = field(default_factory=list)  # point indices that could not be served


def travel_time_matrix(G: nx.MultiDiGraph, nodes: list[int]) -> list[list[float]]:
    """All-pairs shortest travel time in seconds between the given graph nodes."""
    matrix = []
    for src in nodes:
        lengths = nx.single_source_dijkstra_path_length(G, src, weight="travel_time")
        matrix.append([lengths.get(dst, UNREACHABLE_S) for dst in nodes])
    return matrix


def solve_optimised(
    time_matrix: list[list[float]],
    demands: list[float],
    service_s: list[float],
    num_vehicles: int,
    capacity: float,
    max_route_s: float,
    time_limit_s: int = 3,
) -> RoutePlan | None:
    """Capacitated VRP with a shift-length limit, solved with OR-Tools.

    Points that cannot fit within fleet capacity or shift time are dropped at a
    high penalty rather than making the problem infeasible.
    """
    n_nodes = len(time_matrix)
    depot, facility = 0, n_nodes - 1
    manager = pywrapcp.RoutingIndexManager(n_nodes, num_vehicles, [depot] * num_vehicles, [facility] * num_vehicles)
    routing = pywrapcp.RoutingModel(manager)

    def transit(from_index: int, to_index: int) -> int:
        a = manager.IndexToNode(from_index)
        b = manager.IndexToNode(to_index)
        return int(round(time_matrix[a][b] + service_s[a]))

    transit_cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb)
    routing.AddDimension(transit_cb, 0, int(max_route_s), True, "Time")

    demand_cb = routing.RegisterUnaryTransitCallback(lambda i: int(round(demands[manager.IndexToNode(i)])))
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, [int(capacity)] * num_vehicles, True, "Load")

    # Serving a point always costs less than max_route_s, so this penalty only drops points when forced.
    penalty = int(max_route_s) * 10 + 1
    for node in range(1, n_nodes - 1):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = int(time_limit_s)

    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None

    routes = []
    for v in range(num_vehicles):
        index = solution.Value(routing.NextVar(routing.Start(v)))
        seq = []
        while not routing.IsEnd(index):
            seq.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        routes.append(seq)
    served = {p for r in routes for p in r}
    dropped = [p for p in range(1, n_nodes - 1) if p not in served]
    return RoutePlan(routes=routes, dropped=dropped)


def solve_greedy_baseline(
    time_matrix: list[list[float]],
    demands: list[float],
    service_s: list[float],
    num_vehicles: int,
    capacity: float,
    max_route_s: float,
) -> RoutePlan:
    """Nearest-neighbour baseline that mimics an unplanned daily route.

    Each vehicle repeatedly drives to the nearest unserved point that still fits
    its remaining capacity and shift time, then goes to the facility.
    """
    n_nodes = len(time_matrix)
    facility = n_nodes - 1
    unserved = set(range(1, n_nodes - 1))
    routes = []
    for _ in range(num_vehicles):
        pos, load, elapsed, seq = 0, 0.0, 0.0, []
        while True:
            best, best_t = None, None
            for p in unserved:
                t = time_matrix[pos][p] + service_s[pos]
                finish = elapsed + t + service_s[p] + time_matrix[p][facility]
                if load + demands[p] <= capacity and finish <= max_route_s and (best_t is None or t < best_t):
                    best, best_t = p, t
            if best is None:
                break
            seq.append(best)
            unserved.discard(best)
            elapsed += best_t
            load += demands[best]
            pos = best
        routes.append(seq)
    return RoutePlan(routes=routes, dropped=sorted(unserved))
