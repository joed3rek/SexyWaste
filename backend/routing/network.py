"""Road network loading from OpenStreetMap, travel times and geometry helpers."""

from __future__ import annotations

import ast
import math
from functools import lru_cache

import networkx as nx
import osmnx as ox

from backend.config import AREAS, CACHE_DIR, DEFAULT_SPEED_KMPH, SPEEDS_KMPH
from backend.osm import with_overpass_fallback


def _road_class(highway) -> str:
    """Normalise an OSM highway tag, which may be a list or a stringified list."""
    if isinstance(highway, str) and highway.startswith("["):
        highway = ast.literal_eval(highway)
    if isinstance(highway, list):
        highway = highway[0] if highway else ""
    return str(highway or "").replace("_link", "")


def add_travel_times(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Attach speed_kph and travel_time (seconds) to every edge."""
    for _, _, data in G.edges(data=True):
        kmph = SPEEDS_KMPH.get(_road_class(data.get("highway")), DEFAULT_SPEED_KMPH)
        data["speed_kph"] = kmph
        data["travel_time"] = float(data["length"]) / (kmph / 3.6)
    return G


@lru_cache(maxsize=8)
def load_graph(area_key: str) -> nx.MultiDiGraph:
    """Load the drivable road graph for an area, downloading and caching on first use.

    Only the largest strongly connected component is kept, so every node can
    reach every other node while respecting one-way streets.
    """
    area = AREAS[area_key]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{area_key}.graphml"
    if path.exists():
        G = ox.load_graphml(path)
    else:
        if area.pilot:
            # Pilot areas use their mapped boundary, buffered so edge streets stay connected.
            import geopandas as gpd

            from backend.buildings.layers import METRIC_CRS, boundary

            poly = gpd.GeoSeries([boundary(area.pilot)], crs=4326).to_crs(METRIC_CRS).buffer(area.radius_m).to_crs(4326).iloc[0]
            G = with_overpass_fallback(lambda: ox.graph_from_polygon(poly, network_type="drive"))
        else:
            G = with_overpass_fallback(
                lambda: ox.graph_from_point((area.lat, area.lon), dist=area.radius_m, network_type="drive")
            )
        ox.save_graphml(G, path)
    G = ox.truncate.largest_component(G, strongly=True)
    return add_travel_times(G)


def _approx_dist_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Equirectangular distance in metres. Accurate enough at city scale."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6_371_000 * math.hypot(x, y)


def nearest_node(G: nx.MultiDiGraph, lon: float, lat: float) -> int:
    return min(G.nodes, key=lambda n: _approx_dist_m(lon, lat, G.nodes[n]["x"], G.nodes[n]["y"]))


def node_lonlat(G: nx.MultiDiGraph, node: int) -> list[float]:
    return [G.nodes[node]["x"], G.nodes[node]["y"]]


def _best_edge(G: nx.MultiDiGraph, u: int, v: int) -> dict:
    return min(G.get_edge_data(u, v).values(), key=lambda d: d["travel_time"])


def _edge_coords(G: nx.MultiDiGraph, u: int, v: int, data: dict) -> list[list[float]]:
    geom = data.get("geometry")
    if geom is None:
        return [node_lonlat(G, u), node_lonlat(G, v)]
    coords = [list(c) for c in geom.coords]
    ux, uy = node_lonlat(G, u)
    # Orient the geometry from u to v.
    if _approx_dist_m(ux, uy, *coords[-1]) < _approx_dist_m(ux, uy, *coords[0]):
        coords.reverse()
    return coords


def path_geometry(G: nx.MultiDiGraph, path: list[int]) -> tuple[list[list[float]], float, float]:
    """Return (coordinates, length_m, travel_time_s) for a node path."""
    if len(path) < 2:
        return ([node_lonlat(G, path[0])] if path else []), 0.0, 0.0
    coords: list[list[float]] = []
    length = 0.0
    time_s = 0.0
    for u, v in zip(path[:-1], path[1:]):
        data = _best_edge(G, u, v)
        seg = _edge_coords(G, u, v, data)
        coords.extend(seg if not coords else seg[1:])
        length += float(data["length"])
        time_s += float(data["travel_time"])
    return coords, length, time_s


def network_geojson(G: nx.MultiDiGraph) -> dict:
    """Road network as a GeoJSON FeatureCollection for display."""
    features = []
    seen: set[frozenset] = set()
    for u, v, data in G.edges(data=True):
        pair = frozenset((u, v))
        if pair in seen:
            continue
        seen.add(pair)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": _edge_coords(G, u, v, data)},
                "properties": {
                    "road_class": _road_class(data.get("highway")),
                    "name": data.get("name") if isinstance(data.get("name"), str) else None,
                    "oneway": bool(data.get("oneway", False)),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
