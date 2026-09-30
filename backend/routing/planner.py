"""Turns a collection scenario into optimised and baseline route plans on the road network."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from backend.routing.network import nearest_node, node_lonlat, path_geometry
from backend.routing.solver import RoutePlan, solve_greedy_baseline, solve_optimised, travel_time_matrix

ROUTE_COLOURS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#db2777", "#0891b2", "#65a30d", "#dc2626"]


@dataclass
class PlanRequest:
    depot: tuple[float, float]  # lon, lat
    facility: tuple[float, float]
    points: list[dict]  # each: id, lon, lat, demand_kg {stream: kg}, service_min
    stream: str
    num_vehicles: int
    capacity_kg: float
    shift_min: float
    time_limit_s: int = 3


def _summarise(G, plan: RoutePlan, nodes, points, demands, service_s, method: str) -> dict:
    """Build route geometries and totals for one plan."""
    vehicles = []
    total_len = total_time = total_kg = 0.0
    for v, seq in enumerate(plan.routes):
        if not seq:
            continue
        stops = [0, *seq, len(nodes) - 1]
        coords: list[list[float]] = []
        length = drive_s = 0.0
        for a, b in zip(stops[:-1], stops[1:]):
            path = nx.shortest_path(G, nodes[a], nodes[b], weight="travel_time")
            seg, seg_len, seg_s = path_geometry(G, path)
            coords.extend(seg if not coords else seg[1:])
            length += seg_len
            drive_s += seg_s
        load = sum(demands[p] for p in seq)
        work_s = sum(service_s[p] for p in seq)
        vehicles.append(
            {
                "vehicle": v + 1,
                "colour": ROUTE_COLOURS[v % len(ROUTE_COLOURS)],
                "stops": [points[p - 1]["id"] for p in seq],
                "load_kg": round(load, 1),
                "distance_km": round(length / 1000, 2),
                "drive_min": round(drive_s / 60, 1),
                "total_min": round((drive_s + work_s) / 60, 1),
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
        total_len += length
        total_time += drive_s + work_s
        total_kg += load
    return {
        "method": method,
        "vehicles": vehicles,
        "dropped": [points[p - 1]["id"] for p in plan.dropped],
        "totals": {
            "vehicles_used": len(vehicles),
            "distance_km": round(total_len / 1000, 2),
            "total_min": round(total_time / 60, 1),
            "collected_kg": round(total_kg, 1),
            "points_served": sum(len(v["stops"]) for v in vehicles),
            "points_dropped": len(plan.dropped),
        },
    }


def plan_routes(G: nx.MultiDiGraph, req: PlanRequest) -> dict:
    points = [p for p in req.points if p["demand_kg"].get(req.stream, 0) > 0]
    depot_node = nearest_node(G, *req.depot)
    facility_node = nearest_node(G, *req.facility)
    nodes = [depot_node, *(nearest_node(G, p["lon"], p["lat"]) for p in points), facility_node]
    demands = [0.0, *(p["demand_kg"][req.stream] for p in points), 0.0]
    service_s = [0.0, *(p["service_min"] * 60 for p in points), 0.0]
    max_route_s = req.shift_min * 60

    matrix = travel_time_matrix(G, nodes)
    optimised = solve_optimised(
        matrix, demands, service_s, req.num_vehicles, req.capacity_kg, max_route_s, req.time_limit_s
    )
    baseline = solve_greedy_baseline(matrix, demands, service_s, req.num_vehicles, req.capacity_kg, max_route_s)

    result = {
        "stream": req.stream,
        "depot": node_lonlat(G, depot_node),
        "facility": node_lonlat(G, facility_node),
        "baseline": _summarise(G, baseline, nodes, points, demands, service_s, "Nearest-neighbour baseline"),
        "optimised": None,
    }
    if optimised is not None:
        result["optimised"] = _summarise(G, optimised, nodes, points, demands, service_s, "OR-Tools optimised")
    return result
