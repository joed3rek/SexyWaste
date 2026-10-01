"""Routing service: nearest road nodes, travel-time and distance matrices, and route geometry.

    road network -> RoutingService -> RoutingMatrix (time_s, dist_m) -> optimiser

Travel times come from a SpeedModel. Today that is the baseline of assumed speeds per road
class (backend.config.SPEEDS_KMPH). A better source, such as historical GPS speeds, a traffic
API or time-of-day factors, can be added as another SpeedModel without changing the optimiser.

Shortest-path rows are cached per service and shared by every plan on the same network, so a
second optimisation does not repeat the searches. Rows are float64 arrays indexed by node
position; a full matrix for the HSR pilot (about 2,100 nodes) is about 35 MB, and a plan
typically fills well under that.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

import networkx as nx
import numpy as np

from backend.routing.network import load_graph, path_geometry

UNREACHABLE_S = 10**7  # seconds; large enough that the optimiser never prefers it, small enough for int arithmetic


class SpeedModel:
    """Edge weights for travel time. Subclass and set `name` for a new source of speeds."""

    name = "baseline"

    def edge_seconds(self, data: dict) -> float:
        # Set by network.add_travel_times from assumed congested speeds per road class.
        return float(data["travel_time"])


@dataclass
class RoutingMatrix:
    """Travel time (s) and distance (m) between `nodes`, in that order. Directed: [i, j] is i -> j."""

    nodes: list[int]
    time_s: np.ndarray
    dist_m: np.ndarray | None = None
    speed_model: str = "baseline"

    def __len__(self) -> int:
        return len(self.nodes)


class _RowCache:
    """Bounded cache of one-to-all shortest-path rows, keyed by source node."""

    def __init__(self, max_rows: int):
        self.max_rows = max_rows
        self.rows: OrderedDict[int, np.ndarray] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: int, compute) -> np.ndarray:
        with self.lock:
            row = self.rows.get(key)
            if row is not None:
                self.rows.move_to_end(key)
                return row
        row = compute(key)  # outside the lock: searches from different sources can run together
        with self.lock:
            self.rows[key] = row
            while len(self.rows) > self.max_rows:
                self.rows.popitem(last=False)
        return row


class RoutingService:
    """Shortest paths on a directed drive network, respecting one-way streets."""

    def __init__(self, G: nx.MultiDiGraph, speed_model: SpeedModel | None = None, max_rows: int = 4096):
        self.G = G
        self.speed = speed_model or SpeedModel()
        self.weight = f"_w_{self.speed.name}"
        for _, _, data in G.edges(data=True):
            data[self.weight] = self.speed.edge_seconds(data)
        self.R = G.reverse(copy=False)
        self.nodes = list(G.nodes)
        self.index = {n: i for i, n in enumerate(self.nodes)}
        self.xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in self.nodes])
        self._scale = np.array([math.cos(math.radians(float(np.mean(self.xy[:, 1])))), 1.0]) if self.nodes else np.ones(2)
        self._time_from = _RowCache(max_rows)
        self._time_to = _RowCache(max_rows)
        self._dist_from = _RowCache(max_rows)
        self._paths: dict[tuple[int, int], tuple[list, float]] = {}

    # ----- nodes -----

    def nearest_nodes(self, lonlat) -> list[int]:
        """Nearest network node to each (lon, lat), by distance on a locally flat projection."""
        out = []
        for p in np.atleast_2d(np.asarray(lonlat, dtype=float)):
            d = (((self.xy - p) * self._scale) ** 2).sum(axis=1)
            out.append(int(self.nodes[int(np.argmin(d))]))
        return out

    # ----- rows -----

    def _row(self, graph, source: int, weight: str) -> np.ndarray:
        lengths = nx.single_source_dijkstra_path_length(graph, source, weight=weight)
        row = np.full(len(self.nodes), np.inf)
        for node, value in lengths.items():
            row[self.index[node]] = value
        return row

    def times_from(self, a: int) -> np.ndarray:
        return self._time_from.get(a, lambda s: self._row(self.G, s, self.weight))

    def times_to(self, b: int) -> np.ndarray:
        return self._time_to.get(b, lambda s: self._row(self.R, s, self.weight))

    def dists_from(self, a: int) -> np.ndarray:
        return self._dist_from.get(a, lambda s: self._row(self.G, s, "length"))

    # ----- pairs -----

    def time_s(self, a: int, b: int) -> float:
        """Shortest travel time a -> b in seconds; UNREACHABLE_S if there is no path."""
        if a == b:
            return 0.0
        row = self._time_from.rows.get(a)
        v = row[self.index[b]] if row is not None else self.times_to(b)[self.index[a]]
        return float(v) if math.isfinite(v) else float(UNREACHABLE_S)

    t = time_s  # short alias used throughout the planner

    def dist_m(self, a: int, b: int) -> float:
        if a == b:
            return 0.0
        v = self.dists_from(a)[self.index[b]]
        return float(v) if math.isfinite(v) else float(UNREACHABLE_S)

    # ----- matrices -----

    def matrix(self, nodes: list[int], distances: bool = False) -> RoutingMatrix:
        """Travel-time (and optionally distance) matrix between the given nodes."""
        cols = [self.index[n] for n in nodes]
        time = np.array([self.times_from(a)[cols] for a in nodes], dtype=np.float64).reshape(len(nodes), len(nodes))
        np.fill_diagonal(time, 0.0)
        time[~np.isfinite(time)] = UNREACHABLE_S
        dist = None
        if distances:
            dist = np.array([self.dists_from(a)[cols] for a in nodes], dtype=np.float64).reshape(len(nodes), len(nodes))
            np.fill_diagonal(dist, 0.0)
            dist[~np.isfinite(dist)] = UNREACHABLE_S
        return RoutingMatrix(list(nodes), time, dist, self.speed.name)

    # ----- geometry -----

    def leg(self, a: int, b: int) -> tuple[list, float]:
        """Coordinates and length (m) of the fastest path a -> b."""
        key = (a, b)
        if key not in self._paths:
            try:
                path = nx.shortest_path(self.G, a, b, weight=self.weight)
            except nx.NetworkXNoPath:
                path = [a, b] if a != b else [a]
            coords, length, _ = path_geometry(self.G, path)
            self._paths[key] = (coords, length)
        return self._paths[key]

    def route(self, stops: list[int]) -> tuple[list, float]:
        """Coordinates and length (m) of a route through the stops in order."""
        coords, length = [], 0.0
        for a, b in zip(stops[:-1], stops[1:]):
            c, l = self.leg(a, b)
            coords.extend(c if not coords else c[1:])
            length += l
        return coords, length


@lru_cache(maxsize=4)
def routing_service(area_key: str) -> RoutingService:
    """Shared routing service for an area's road network (baseline speeds)."""
    return RoutingService(load_graph(area_key))
