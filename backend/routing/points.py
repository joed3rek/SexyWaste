"""Door-to-door collection points for the Route Builder v2: street runs (block faces).

Each building is snapped to its frontage street segment (the stretch of one street between
two junctions, from OSM). Consecutive segments of the same street are merged into runs up
to a length cap. Buildings on a run are grouped by use. Point IDs derive from OSM node IDs,
so they stay stable.

Uses are never mixed in one point. Bulk waste generators are separate points and are
excluded from wet waste (SWM Rules 2026, r. 6(c)-(d)).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from collections import Counter
from functools import lru_cache

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge, polygonize, unary_union

from backend.buildings import generators, layers
from backend.buildings.layers import METRIC_CRS
from backend.config import AREAS, DATA_DIR, ROOT
from backend.regulations import stream_keys
from backend.routing.network import _road_class, load_graph

REFERENCE_DIR = ROOT / "reference"
STRAIGHT_TURN_DEG = 30  # unnamed street runs continue through a junction only if the turn is at most this
OVERRIDES_DIR = DATA_DIR / "reference_overrides"
STREAMS = stream_keys()
USES = ("residential", "commercial", "mixed", "institutional")
CATEGORY_USE = {
    "residential_house": "residential", "residential_apartment": "residential", "unclassified": "residential",
    "mixed_use": "mixed",
    "commercial_retail": "commercial", "commercial_office": "commercial", "food_service": "commercial",
    "hotel": "commercial", "market": "commercial", "industrial": "commercial",
    "educational": "institutional", "healthcare": "institutional", "public_institutional": "institutional",
    "religious": "institutional",
}


# ---------- Reference data (with user overrides) ----------

def _deep_merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else v
        return out
    if isinstance(base, list) and isinstance(over, list) and all(isinstance(x, dict) and "key" in x for x in base):
        by_key = {x["key"]: x for x in over if isinstance(x, dict) and "key" in x}
        return [_deep_merge(x, by_key[x["key"]]) if x["key"] in by_key else x for x in base]
    return over if over is not None else base


def reference(name: str) -> dict:
    """Reference file from /reference, with any saved UI edits merged on top."""
    data = json.loads((REFERENCE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    override = OVERRIDES_DIR / f"{name}.json"
    if override.exists():
        data = _deep_merge(data, json.loads(override.read_text(encoding="utf-8")))
    return data


def vehicle_class(key: str) -> dict:
    for c in reference("vehicles")["classes"]:
        if c["key"] == key:
            return c
    raise KeyError(f"Unknown vehicle class '{key}'")


# ---------- Street segments ----------

def _area_for(pilot_key: str) -> str:
    return next(a.key for a in AREAS.values() if a.pilot == pilot_key)


def _first(v):
    if isinstance(v, str) and v.startswith("["):
        v = re.findall(r"'([^']*)'", v) or [v]
    if isinstance(v, list):
        return v[0] if v else None
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _num(v):
    v = _first(v)
    if v is None:
        return None
    m = re.match(r"\s*(\d+(\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


@lru_cache(maxsize=4)
def street_graph(pilot_key: str):
    """Undirected projected street graph and its segments table."""
    G = load_graph(_area_for(pilot_key))
    Gu = ox.convert.to_undirected(ox.project_graph(G, to_crs=METRIC_CRS))
    roads = reference("roads")
    defaults = roads["default_width_m"]
    lane_w = roads["lane_width_m"]["value"]
    rows = []
    for u, v, k, d in Gu.edges(keys=True, data=True):
        a, b = sorted((u, v))
        width, source = _num(d.get("width")), "OSM width"
        if width is None and _num(d.get("lanes")):
            width, source = _num(d.get("lanes")) * lane_w, "OSM lanes"
        cls = _road_class(d.get("highway"))
        if width is None:
            width, source = defaults.get(cls, defaults["_other"]), "default for road class"
        geom = d.get("geometry") or LineString([(Gu.nodes[u]["x"], Gu.nodes[u]["y"]), (Gu.nodes[v]["x"], Gu.nodes[v]["y"])])
        rows.append({"seg_id": f"{a}-{b}-{k}", "u": u, "v": v, "key": k, "name": _first(d.get("name")),
                     "road_class": cls, "length_m": float(d["length"]), "width_m": float(width),
                     "width_source": source, "geometry": geom})
    seg = gpd.GeoDataFrame(rows, crs=METRIC_CRS)
    return Gu, seg


def _street_name(v) -> str | None:
    """A street name as text, or None when missing. Under pandas 3 a missing name arrives as a
    float NaN, which is truthy and cannot be sorted together with strings."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    v = str(v).strip()
    return v or None


def _names_at_nodes(seg: gpd.GeoDataFrame) -> dict:
    at = {}
    for r in seg.itertuples():
        name = _street_name(r.name)
        if name:
            at.setdefault(r.u, set()).add(name)
            at.setdefault(r.v, set()).add(name)
    return at


def _cross_streets(names_at: dict, node, name: str | None) -> list[str]:
    """Other street names meeting at a node, sorted."""
    names = {n for n in (_street_name(x) for x in names_at.get(node, set())) if n}
    return sorted(names - {name}) if name else sorted(names)


@lru_cache(maxsize=16)
def street_runs(pilot_key: str, max_len_m: float) -> tuple[pd.DataFrame, dict]:
    """Merge consecutive segments of the same named street into runs up to max_len_m.

    Returns (runs table, seg_id -> run_id).
    """
    _, seg = street_graph(pilot_key)
    names_at = _names_at_nodes(seg)
    seg_by_id = seg.set_index("seg_id")
    runs, seg_to_run = [], {}

    def close(chain, name):
        first, last = chain[0], chain[-1]
        start_node, end_node = first[1], last[2]
        run_id = f"R{first[0]}" + (f"+{len(chain) - 1}" if len(chain) > 1 else "")
        geoms = [seg_by_id.loc[c[0], "geometry"] for c in chain]
        merged = unary_union(geoms)
        line = merged if isinstance(merged, LineString) else linemerge(merged)
        cross = [_cross_streets(names_at, n, name) for n in (start_node, end_node)]
        if name:
            ends = [c[0] if c else "dead end" for c in cross]
            label = f"{name}, {ends[0]}–{ends[1]}" if ends[0] != ends[1] else f"{name}, near {ends[0]}"
        else:
            near = next((c[0] for c in cross if c), None)
            label = f"Unnamed street{f' off {near}' if near else ''}"
        widths = [seg_by_id.loc[c[0], "width_m"] for c in chain]
        runs.append({"run_id": run_id, "name": name, "label": label, "seg_ids": [c[0] for c in chain],
                     "nodes": [int(first[1])] + [int(c[2]) for c in chain],
                     "length_m": float(sum(seg_by_id.loc[c[0], "length_m"] for c in chain)),
                     "min_width_m": float(min(widths)), "geometry": line})
        for c in chain:
            seg_to_run[c[0]] = run_id

    node_xy = {n: (d["x"], d["y"]) for n, d in street_graph(pilot_key)[0].nodes(data=True)}

    def bearing(a, b):
        (x1, y1), (x2, y2) = node_xy[a], node_xy[b]
        return math.degrees(math.atan2(y2 - y1, x2 - x1))

    def straight(prev, nxt):
        turn = abs((bearing(nxt[1], nxt[2]) - bearing(prev[1], prev[2]) + 180) % 360 - 180)
        return turn <= STRAIGHT_TURN_DEG

    group_key = seg["name"].fillna("~" + seg["road_class"])
    for key, grp in seg.groupby(group_key):
        name = None if str(key).startswith("~") else _street_name(key)
        H = nx.MultiGraph()
        for r in grp.itertuples():
            H.add_edge(r.u, r.v, key=r.seg_id, length=r.length_m)
        for comp in nx.connected_components(H):
            sub = H.subgraph(comp)
            ends = [n for n in sub.nodes if sub.degree(n) == 1]
            start = ends[0] if ends else next(iter(sub.nodes))
            chain, length, prev_end = [], 0.0, None
            for a, b, k in nx.edge_dfs(sub, source=start):
                seg_len = sub.edges[a, b, k]["length"]
                contiguous = prev_end is None or a == prev_end
                # Unnamed streets only continue a run when the street carries straight on.
                if contiguous and chain and name is None:
                    contiguous = straight(chain[-1], (k, a, b))
                if chain and (not contiguous or length + seg_len > max_len_m):
                    close(chain, name)
                    chain, length = [], 0.0
                chain.append((k, a, b))
                length += seg_len
                prev_end = b
            if chain:
                close(chain, name)
    return gpd.GeoDataFrame(runs, crs=METRIC_CRS), seg_to_run


# ---------- Buildings snapped to streets ----------

@lru_cache(maxsize=4)
def building_snaps(pilot_key: str) -> pd.DataFrame:
    """Nearest street segment and kerb point for every building."""
    Gu, seg = street_graph(pilot_key)
    base = generators.base_table(pilot_key).to_crs(METRIC_CRS)
    pts = base.geometry.representative_point()
    ne, dist = ox.distance.nearest_edges(Gu, pts.x.values, pts.y.values, return_dist=True)
    geom = seg.set_index(["u", "v", "key"])["geometry"]
    rows = []
    for bid, p, (u, v, k), dmin in zip(base["id"], pts, ne, dist):
        line = geom.get((u, v, k))
        if line is None:
            line = geom.get((v, u, k))
        off = line.project(p)
        kerb = line.interpolate(off)
        a, b = sorted((u, v))
        rows.append({"id": bid, "seg_id": f"{a}-{b}-{k}", "edge_u": u, "edge_v": v, "offset_m": off,
                     "snap_m": float(dmin), "bx": p.x, "by": p.y, "kx": kerb.x, "ky": kerb.y})
    return pd.DataFrame(rows)


def generator_table(pilot_key: str, surveys: dict) -> pd.DataFrame:
    """Buildings with use, per-stream kg, BWG status and kerb position. `surveys` maps building id
    to survey state (backend/survey/state.py)."""
    props = pd.DataFrame(generators.all_properties(pilot_key, surveys))
    t = props.merge(building_snaps(pilot_key), on="id")

    def use_of(r):
        s = r["survey"] if isinstance(r["survey"], dict) else {}
        if s.get("use") in USES:
            return s["use"]
        if s.get("use") in ("vacant", "construction"):
            return None
        return CATEGORY_USE.get(r["category"])

    # Collect only what the building does not compost itself.
    if "wet_to_collect" in t:
        t["wet"] = t["wet_to_collect"]
    t["use"] = t.apply(use_of, axis=1)
    t["onsite"] = t["survey"].apply(lambda s: (s or {}).get("onsite_processing") if isinstance(s, dict) else None)
    return t[t["use"].notna() & (t["kg_day"] > 0)].copy()


_TO_WGS84 = Transformer.from_crs(METRIC_CRS, 4326, always_xy=True)
_TO_METRIC = Transformer.from_crs(4326, METRIC_CRS, always_xy=True)


def _to_lonlat(x, y):
    lon, lat = _TO_WGS84.transform(float(x), float(y))
    return float(lon), float(lat)


def _kg_label(use, n, kg):
    return f"{n} {use}, {kg:.0f} kg/day"


def _point(pid, use, g, label, span_m, width_m, lonlat, extra=None) -> dict:
    kg = {s: round(float(g[s].sum()), 1) for s in STREAMS}
    sector = Counter(g["sector"]).most_common(1)[0][0]
    return {
        "id": pid, "use": use, "label": label, "sector": sector,
        "buildings": int(len(g)), "building_ids": list(g["id"]),
        "building_kg": [[round(float(x), 2) for x in row] for row in g[list(STREAMS)].to_numpy()],
        "kg": kg, "total_kg": round(float(sum(kg.values())), 1),
        "home_composted_kg": round(float(g["wet_home_composted"].sum()), 1) if "wet_home_composted" in g else 0.0,
        "span_m": round(float(span_m), 1), "min_width_m": width_m,
        "lon": lonlat[0], "lat": lonlat[1], **(extra or {}),
    }


def bwg_points(t: pd.DataFrame, seg_label: dict | None = None) -> list[dict]:
    """Each bulk waste generator is its own point, with wet waste excluded (processed at source or via EBWGR)."""
    seg_label = seg_label or {}
    out = []
    for _, r in t[t["bwg_status"] == "bwg_confirmed"].iterrows():
        g = r.to_frame().T.copy()
        g["wet"] = 0.0
        street = seg_label.get(r["seg_id"]) or "unnamed street"
        label = (f"{r['name'] or r['address']} (bulk waste generator)" if (r["name"] or r["address"])
                 else f"Unnamed {r['use']} bulk waste generator on {street}")
        out.append(_point(f"BWG:{r['id']}", r["use"], g, label, 0.0, None, _to_lonlat(r["kx"], r["ky"]),
                          {"is_bwg": True, "wet_excluded": True, "wet_kg_excluded": round(float(r["wet"]), 1),
                           "onsite_processing": r["onsite"] or "not_surveyed", "compliance": r["bwg_compliance"],
                           "path_nodes": [int(r["edge_u"]), int(r["edge_v"])]}))
    return out


# ---------- Option A: street runs ----------

def points_street_runs(pilot_key: str, surveys: dict, max_run_m: float = 150, max_buildings: int = 40,
                       min_buildings: int = 3, merge_radius_m: float = 120) -> list[dict]:
    runs, seg_to_run = street_runs(pilot_key, float(max_run_m))
    runs = runs.set_index("run_id")
    t = generator_table(pilot_key, surveys)
    run_names = {rid: _street_name(n) for rid, n in runs["name"].items()}
    out = bwg_points(t, {sid: run_names[rid] for sid, rid in seg_to_run.items() if run_names[rid]})
    t = t[t["bwg_status"] != "bwg_confirmed"].copy()
    t["run_id"] = t["seg_id"].map(seg_to_run)
    for run_id, grp in t.groupby("run_id"):
        run = runs.loc[run_id]
        line = run.geometry
        if isinstance(line, MultiLineString):
            line = max(line.geoms, key=lambda g: g.length)
        grp = grp.assign(pos=[line.project(Point(x, y)) for x, y in zip(grp["kx"], grp["ky"])])
        seg_index = {sid: i for i, sid in enumerate(run["seg_ids"])}
        for use, g in grp.groupby("use"):
            g = g.sort_values("pos")
            chunks = [g.iloc[i:i + max_buildings] for i in range(0, len(g), max_buildings)]
            for n, c in enumerate(chunks, start=1):
                span = c["pos"].max() - c["pos"].min()
                mid = line.interpolate(float(c["pos"].median()))
                suffix = f" (part {n} of {len(chunks)})" if len(chunks) > 1 else ""
                pid = f"A:{run_id}:{use}" + (f":{n}" if len(chunks) > 1 else "")
                # The junctions along the stretch of street these buildings front, in street order:
                # the vehicle drives this stretch to collect from them.
                covered = [seg_index[sid] for sid in c["seg_id"]]
                path = run["nodes"][min(covered):max(covered) + 2]
                out.append(_point(pid, use, c, f"{run['label']}{suffix}", span, run["min_width_m"],
                                  _to_lonlat(mid.x, mid.y), {"run_id": run_id, "run_length_m": round(float(run["length_m"]), 1),
                                                             "path_nodes": path}))
    return _merge_small(out, runs, int(min_buildings), float(merge_radius_m))


def _join_paths(a: list, b: list) -> list:
    """Join two street paths that meet at a junction, so the vehicle drives both in one pass.
    Paths that do not meet are simply chained; the route fills the gap by the shortest way."""
    if not a or not b:
        return list(a or b)
    if a[-1] == b[0]:
        return a + b[1:]
    if a[-1] == b[-1]:
        return a + b[-2::-1]
    if a[0] == b[-1]:
        return b + a[1:]
    if a[0] == b[0]:
        return b[::-1] + a[1:]
    return a + b


def _merge_small(points: list[dict], runs: gpd.GeoDataFrame, min_buildings: int, radius_m: float) -> list[dict]:
    """Fold points with fewer than min_buildings into the nearest same-use point on a touching run."""
    if min_buildings <= 1:
        return points
    ends = {}
    for run_id, r in runs.iterrows():
        geom = r.geometry
        line = max(geom.geoms, key=lambda g: g.length) if isinstance(geom, MultiLineString) else geom
        ends[run_id] = {tuple(np.round(line.coords[0], 1)), tuple(np.round(line.coords[-1], 1))}
    keep = [p for p in points if p.get("is_bwg") or p["buildings"] >= min_buildings]
    small = [p for p in points if not p.get("is_bwg") and p["buildings"] < min_buildings]
    xy = {p["id"]: Point(_TO_METRIC.transform(p["lon"], p["lat"])) for p in points if not p.get("is_bwg")}
    for sp in small:
        best, best_d = None, radius_m
        for t in keep:
            if t.get("is_bwg") or t["use"] != sp["use"] or not ends.get(t.get("run_id"), set()) & ends.get(sp.get("run_id"), set()):
                continue
            d = xy[t["id"]].distance(xy[sp["id"]])
            if d <= best_d:
                best, best_d = t, d
        if best is None:
            keep.append(sp)
            continue
        best["building_ids"] += sp["building_ids"]
        best["building_kg"] += sp["building_kg"]
        best["buildings"] += sp["buildings"]
        for s_ in STREAMS:
            best["kg"][s_] = round(best["kg"][s_] + sp["kg"][s_], 1)
        best["total_kg"] = round(sum(best["kg"].values()), 1)
        best["span_m"] = round(best["span_m"] + best_d, 1)
        best["path_nodes"] = _join_paths(best.get("path_nodes", []), sp.get("path_nodes", []))
        best.setdefault("merged_from", []).append(sp["id"])
    return keep


# ---------- Service time ----------

def service_minutes(point: dict, vehicle_key: str, streams: list[str]) -> float:
    """Sum of per-building service times (1-5 min by vehicle class and kg) plus travel along the street."""
    cls = vehicle_class(vehicle_key)["service"]
    speed = reference("vehicles")["collection_speed_kmph"]["value"]
    idx = [STREAMS.index(s) for s in streams]
    total = 0.0
    for row in point["building_kg"]:
        kg = sum(row[i] for i in idx)
        if kg <= 0:
            continue
        total += min(cls["max_minutes"], max(cls["min_minutes"], cls["base_min_per_stop"] + kg / cls["kg_per_min"]))
    return round(total + point["span_m"] / (speed * 1000 / 60), 2)


# ---------- Street blocks (display only) ----------

@lru_cache(maxsize=4)
def blocks(pilot_key: str) -> gpd.GeoDataFrame:
    _, seg = street_graph(pilot_key)
    polys = [p for p in polygonize(unary_union(list(seg.geometry))) if p.area > 500]
    gdf = gpd.GeoDataFrame({"block_id": [f"K{i:04d}" for i in range(len(polys))]}, geometry=polys, crs=METRIC_CRS)
    gdf["area_m2"] = gdf.area.round()
    sec = layers.sectors(pilot_key).to_crs(METRIC_CRS)
    j = gpd.sjoin(gpd.GeoDataFrame(geometry=gdf.representative_point(), crs=METRIC_CRS), sec[["name", "geometry"]], predicate="within", how="left")
    gdf["sector"] = j.groupby(level=0)["name"].first().reindex(gdf.index)
    return gdf[gdf["sector"].notna()].to_crs(4326)


# ---------- Public entry point ----------

def survey_fingerprint(surveys: dict) -> str:
    """Changes whenever any survey record is added, edited or removed."""
    return hashlib.sha1(json.dumps(surveys, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# Building every point takes seconds (mostly pandas overhead per street run), and the same set is
# asked for by the points, stations and plan endpoints. Keep the latest set per pilot and params;
# a survey edit changes the fingerprint and so rebuilds it.
_POINTS_CACHE: dict[tuple, tuple[str, list[dict]]] = {}
_POINTS_LOCK = threading.Lock()


def collection_points(pilot_key: str, surveys: dict, sector: str | None = None,
                      params: dict | None = None) -> list[dict]:
    p = params or {}
    args = (float(p.get("max_run_m", 150)), int(p.get("max_buildings", 40)),
            int(p.get("min_buildings", 3)), float(p.get("merge_radius_m", 120)))
    key, fp = (pilot_key, *args), survey_fingerprint(surveys)
    with _POINTS_LOCK:
        hit = _POINTS_CACHE.get(key)
        if hit is None or hit[0] != fp:
            hit = (fp, points_street_runs(pilot_key, surveys, *args))
            _POINTS_CACHE[key] = hit
    # Callers add fields to points, so each gets its own copy.
    return copy.deepcopy([x for x in hit[1] if not sector or x["sector"] == sector])


def summarise(points: list[dict]) -> dict:
    df = pd.DataFrame([{k: p[k] for k in ("sector", "use", "buildings", "total_kg")} | {"bwg": p.get("is_bwg", False)} for p in points])
    if df.empty:
        return {"points": 0}
    normal = df[~df["bwg"]]
    return {
        "points": int(len(df)),
        "bwg_points": int(df["bwg"].sum()),
        "by_sector": normal.groupby("sector").size().to_dict(),
        "by_use": normal.groupby("use").size().to_dict(),
        "buildings_per_point": {"median": float(normal["buildings"].median()), "p90": float(normal["buildings"].quantile(0.9)), "max": int(normal["buildings"].max())},
        "kg_per_point": {"median": float(normal["total_kg"].median()), "p90": float(normal["total_kg"].quantile(0.9)), "max": float(normal["total_kg"].max())},
    }
