"""Synthetic collection scenarios for testing routing before municipal data arrives.

Every number here is a placeholder. Replace with ward collection-point lists,
weighbridge tonnage and real depot / facility locations.
"""

from __future__ import annotations

import math
import random

import networkx as nx

from backend.config import AREAS, WASTE_STREAMS
from backend.routing.network import _approx_dist_m, nearest_node, node_lonlat

# ASSUMPTION: typical share of each stream in Indian municipal waste.
STREAM_SHARES = {"wet": 0.55, "dry": 0.35, "sanitary": 0.07, "special": 0.03}


def default_depot_and_facility(G: nx.MultiDiGraph, area_key: str) -> tuple[int, int]:
    """Depot near the area centre, facility at the node farthest from it."""
    area = AREAS[area_key]
    depot = nearest_node(G, area.lon, area.lat)
    facility = max(G.nodes, key=lambda n: _approx_dist_m(area.lon, area.lat, G.nodes[n]["x"], G.nodes[n]["y"]))
    return depot, facility


def generate_points(G: nx.MultiDiGraph, n_points: int, seed: int, exclude: set[int] = frozenset()) -> list[dict]:
    """Place n_points collection points on random road-network nodes."""
    rng = random.Random(seed)
    candidates = sorted(n for n in G.nodes if n not in exclude)
    nodes = rng.sample(candidates, min(n_points, len(candidates)))
    points = []
    for i, node in enumerate(nodes, start=1):
        total_kg = rng.uniform(80, 500)
        demand = {s: round(total_kg * STREAM_SHARES[s] * rng.uniform(0.7, 1.3), 1) for s in WASTE_STREAMS}
        lon, lat = node_lonlat(G, node)
        points.append(
            {
                "id": f"P{i:03d}",
                "lon": lon,
                "lat": lat,
                "demand_kg": demand,
                # Loading time grows with the amount collected.
                "service_min": round(2 + math.sqrt(total_kg) / 4, 1),
            }
        )
    return points
