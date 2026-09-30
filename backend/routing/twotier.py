"""Two-tier door-to-door collection planner (Route Builder v2).

Tier 1 (primary): small vehicles start at the user's depot, collect door to door from
street-run collection points and unload at a transfer station (SWM "secondary storage
point"), making as many trips as capacity requires.
Tier 2 (secondary): trucks shuttle from transfer stations to the MRF.

Method, per sector:
1. Transfer stations are suggested by greedy set cover: a candidate road junction on a main
   road (tertiary or above) covers every collection point within the service radius along
   the road network; stations are picked by most uncovered kg until all points are covered.
   Users can move, add or remove stations.
2. Each point is assigned to its nearest station along the network.
3. For each station, trips are built with OR-Tools (a capacitated VRP whose "vehicles" are
   single trips from the station and back, with weight and volume capacities, per-type
   service times and road-width restrictions). Each trip carries a fixed cost equal to the
   unloading time, so the solver uses as few trips as it sensibly can.
4. Trips are assigned to the physical fleet by longest-trip-first scheduling to the least
   busy vehicle of the right type, adding depot and station-to-station travel.
5. Truck loads are scheduled the same way, earliest-available truck first.

All times are estimates from assumed road speeds and service rates.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

import networkx as nx
import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from shapely.geometry import Point

from backend.buildings import layers
from backend.regulations import stream_keys
from backend.routing import points as P
from backend.routing.network import _road_class, load_graph, node_lonlat, path_geometry

STREAMS = stream_keys()
MAIN_ROADS = {"primary", "secondary", "tertiary"}  # trunk excluded: elevated or access-controlled in HSR
BIG = 10**7


@dataclass
class PlanInput:
    pilot: str
    sector: str
    depot: tuple[float, float]
    mrf: tuple[float, float]
    primary_fleet: list[dict]            # [{"type": key, "count": n}]
    secondary_fleet: list[dict]
    stations: list[tuple[float, float]] | None = None
    truck_depot: tuple[float, float] | None = None
    radius_m: float = 500
    streams: list[str] = field(default_factory=lambda: list(STREAMS))
    shift_h: float = 8
    unload_min: float = 10
    truck_load_min: float = 15
    truck_unload_min: float = 15
    time_limit_s: int = 20


# ---------- Graph helpers ----------

@lru_cache(maxsize=2)
def _graph(pilot: str):
    G = load_graph(P._area_for(pilot))
    nodes = np.array(list(G.nodes))
    xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in nodes])
    U = G.to_undirected(as_view=True)
    main = set()
    for u, v, d in G.edges(data=True):
        if _road_class(d.get("highway")) in MAIN_ROADS:
            main.update((u, v))
    return G, U, nodes, xy, main


def _nearest_nodes(pilot: str, lonlat: np.ndarray) -> list[int]:
    G, _, nodes, xy, _ = _graph(pilot)
    lat0 = math.radians(float(np.mean(xy[:, 1])))
    scale = np.array([math.cos(lat0), 1.0])
    out = []
    for p in np.atleast_2d(lonlat):
        d = (((xy - p) * scale) ** 2).sum(axis=1)
        out.append(int(nodes[int(np.argmin(d))]))
    return out


class _Times:
    """Cached one-to-all travel times (seconds) on the directed drive network."""

    def __init__(self, G):
        self.G = G
        self._from: dict[int, dict] = {}
        self._to: dict[int, dict] = {}

    def frm(self, a: int) -> dict:
        if a not in self._from:
            self._from[a] = nx.single_source_dijkstra_path_length(self.G, a, weight="travel_time")
        return self._from[a]

    def to(self, b: int) -> dict:
        if b not in self._to:
            self._to[b] = nx.single_source_dijkstra_path_length(self.G.reverse(copy=False), b, weight="travel_time")
        return self._to[b]

    def t(self, a: int, b: int) -> float:
        if a == b:
            return 0.0
        if a in self._from:
            return self._from[a].get(b, BIG)
        return self.to(b).get(a, BIG)


# ---------- Vehicles ----------

def _vehicle(key: str, streams: list[str]) -> dict:
    c = P.vehicle_class(key)
    dens = P.reference("vehicles")["stream_densities_kg_m3"]
    comp = c["compaction_ratio"]["value"] or 1.0
    return {
        "key": key, "label": c["label"], "tier": c["tier"],
        "cap_kg": float(c["payload_kg"]["value"]),
        # Loose-waste litres the body can take, allowing for compaction.
        "cap_l": float(c["body_volume_m3"]["value"]) * comp * 1000,
        "min_width": float(c["min_road_width_m"]["value"]),
        "density": {s: float(dens[s]["value"]) for s in STREAMS},
    }


def _litres(kg_by_stream: dict, density: dict) -> float:
    return sum(kg_by_stream.get(s, 0) / density[s] * 1000 for s in kg_by_stream)


# ---------- Transfer stations ----------

def _sector_points(inp: PlanInput) -> list[dict]:
    from backend.survey import store
    pts = P.collection_points(inp.pilot, store.all_records(inp.pilot), inp.sector)
    out = []
    for p in pts:
        kg = {s: (0.0 if s == "wet" and p.get("wet_excluded") else p["kg"][s]) for s in inp.streams}
        if sum(kg.values()) <= 0:
            continue
        out.append({**p, "load": kg, "load_kg": sum(kg.values())})
    nodes = _nearest_nodes(inp.pilot, np.array([[p["lon"], p["lat"]] for p in out])) if out else []
    for p, n in zip(out, nodes):
        p["node"] = n
    return out


def station_capacity_kg(secondary_fleet: list[dict], streams: list[str]) -> tuple[float, str]:
    """A transfer station holds at least two loads of the largest truck in use (default: rear-loader compactor)."""
    keys = [r["type"] for r in secondary_fleet if int(r.get("count", 0)) > 0] or ["rear_loader_compactor"]
    best = max((_vehicle(k, streams) for k in keys), key=lambda t: t["cap_kg"])
    return 2 * best["cap_kg"], best["label"]


def suggest_stations(pilot: str, sector: str, radius_m: float, streams: list[str] | None = None,
                     secondary_fleet: list[dict] | None = None) -> list[dict]:
    inp = PlanInput(pilot=pilot, sector=sector, depot=(0, 0), mrf=(0, 0), primary_fleet=[],
                    secondary_fleet=secondary_fleet or [], radius_m=radius_m, streams=streams or list(STREAMS))
    pts = _sector_points(inp)
    stations = _suggest(inp, pts)
    assign = _assign_balanced(inp, pts, [s["node"] for s in stations])
    for st in stations:
        st["kg"] = round(sum(p["load_kg"] for p, n in zip(pts, assign) if n == st["node"]), 1)
        st["points"] = sum(1 for n in assign if n == st["node"])
    return stations


@lru_cache(maxsize=8192)
def _reach(pilot: str, node: int, cutoff: float) -> dict:
    """Road distance (m, ignoring one-way rules) from a node to every node within the cutoff."""
    _, U, _, _, _ = _graph(pilot)
    return nx.single_source_dijkstra_path_length(U, node, cutoff=cutoff, weight="length")


def _assign_balanced(inp: PlanInput, pts: list[dict], station_nodes: list[int]) -> list[int]:
    """Assign each point to a station so station loads are similar and within capacity.

    Points are placed in order of how strongly they prefer one station (regret), each to the
    nearest station that still has room under the balanced target and lies within the service
    radius. Points that fit nowhere go to their nearest station."""
    if not station_nodes:
        return []
    cap, _ = station_capacity_kg(inp.secondary_fleet, inp.streams)
    total = sum(p["load_kg"] for p in pts)
    target = min(cap, 1.15 * total / len(station_nodes))
    far = 10 * inp.radius_m
    dist = {s: _reach(inp.pilot, s, far) for s in station_nodes}
    d = [[dist[s].get(p["node"], 1e9) for s in station_nodes] for p in pts]

    def regret(i):
        row = sorted(d[i])
        return (row[1] - row[0]) if len(row) > 1 else 0

    order = sorted(range(len(pts)), key=lambda i: (-regret(i), -pts[i]["load_kg"]))
    load = [0.0] * len(station_nodes)
    out = [None] * len(pts)
    for i in order:
        ranked = sorted(range(len(station_nodes)), key=lambda j: d[i][j])
        pick = next((j for j in ranked if d[i][j] <= inp.radius_m and load[j] + pts[i]["load_kg"] <= target), None)
        if pick is None:
            pick = next((j for j in ranked if load[j] + pts[i]["load_kg"] <= cap), ranked[0])
        load[pick] += pts[i]["load_kg"]
        out[i] = station_nodes[pick]
    return out


def _suggest(inp: PlanInput, pts: list[dict]) -> list[dict]:
    """Choose transfer stations on main roads: enough to cover every point within the radius AND to keep
    each station at or under two truck loads, then move them so their loads are similar."""
    G, U, nodes, xy, main = _graph(inp.pilot)
    if not pts:
        return []
    poly = layers.sectors(inp.pilot).set_index("name").to_crs(32643).loc[inp.sector, "geometry"].buffer(150)
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, 32643, always_xy=True)
    in_sector = [int(n) for n, (x, y) in zip(nodes, xy) if poly.contains(Point(tr.transform(x, y)))]
    cand = [n for n in in_sector if n in main] or in_sector
    node_kg = defaultdict(float)
    for p in pts:
        node_kg[p["node"]] += p["load_kg"]
    cover = {c: {n for n in _reach(inp.pilot, c, inp.radius_m) if n in node_kg} for c in cand}

    # 1. Radius: greedy set cover by uncovered kg.
    uncovered, chosen = dict(node_kg), []
    while uncovered:
        best = max(cand, key=lambda c: sum(uncovered.get(n, 0) for n in cover[c]))
        if sum(uncovered.get(n, 0) for n in cover[best]) <= 0:
            for n in list(uncovered):  # unreachable from main roads: use the nearest junction to the point
                chosen.append(n)
                for m in _reach(inp.pilot, n, inp.radius_m):
                    uncovered.pop(m, None)
            break
        chosen.append(best)
        for n in cover[best]:
            uncovered.pop(n, None)

    # 2. Quantity: at least enough stations that none exceeds its two-truck capacity.
    cap, _ = station_capacity_kg(inp.secondary_fleet, inp.streams)
    total = sum(node_kg.values())
    while len(chosen) < math.ceil(total / cap) and len(chosen) < len(cand):
        assign = _assign_balanced(inp, pts, chosen)
        loads = defaultdict(float)
        for p, n in zip(pts, assign):
            loads[n] += p["load_kg"]
        heavy = max(chosen, key=lambda n: loads[n])
        members = [p["node"] for p, n in zip(pts, assign) if n == heavy]
        extra = max((c for c in cand if c not in chosen),
                    key=lambda c: sum(node_kg[m] for m in set(members) if m in cover[c]))
        chosen.append(extra)

    # 3. Balance: move each station to the main-road junction that best serves its balanced catchment.
    for _ in range(4):
        assign = _assign_balanced(inp, pts, chosen)
        moved = False
        for k, st in enumerate(list(chosen)):
            members = [(p["node"], p["load_kg"]) for p, n in zip(pts, assign) if n == st]
            if not members:
                continue
            nearby = [c for c in _reach(inp.pilot, st, inp.radius_m) if c in set(cand) and c not in chosen[:k] + chosen[k + 1:]]

            def cost(c):
                r = _reach(inp.pilot, c, 10 * inp.radius_m)
                return sum(w * r.get(m, 1e6) for m, w in members)

            better = min(nearby or [st], key=cost)
            if better != st and cost(better) < 0.97 * cost(st):
                chosen[k] = better
                moved = True
        if not moved:
            break

    return [{"id": f"TS{i + 1}", "node": c, "lon": node_lonlat(G, c)[0], "lat": node_lonlat(G, c)[1],
             "on_main_road": c in main, "suggested": True} for i, c in enumerate(chosen)]


# ---------- Tier 1: vehicle territories and trips ----------

def _solve_group(pts: list[dict], station: int, t: dict, times: _Times, inp: PlanInput,
                 time_limit_s: int) -> tuple[list[dict], list[dict]]:
    """Trips for ONE vehicle over its own territory: a capacitated VRP whose routes are that
    vehicle's successive trips from the transfer station and back."""
    if not pts:
        return [], []
    n = len(pts)
    nodes = [station] + [p["node"] for p in pts] + [station]
    for a in nodes:
        times.frm(a)
    tm = [[times.t(a, b) for b in nodes] for a in nodes]
    tot_kg = sum(p["load_kg"] for p in pts)
    tot_l = sum(_litres(p["load"], t["density"]) for p in pts)
    k = min(n, math.ceil(max(tot_kg / (0.9 * t["cap_kg"]), tot_l / (0.9 * t["cap_l"]))) + 2)
    manager = pywrapcp.RoutingIndexManager(len(nodes), k, [0] * k, [n + 1] * k)
    routing = pywrapcp.RoutingModel(manager)
    svc = [0.0] + [P.service_minutes(p, t["key"], inp.streams) * 60 for p in pts] + [0.0]
    cb = routing.RegisterTransitCallback(
        lambda i, j: int(tm[manager.IndexToNode(i)][manager.IndexToNode(j)] + svc[manager.IndexToNode(i)]))
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    for v in range(k):
        routing.SetFixedCostOfVehicle(int(inp.unload_min * 60), v)
    shift_s = int(inp.shift_h * 3600)
    routing.AddDimension(cb, 0, shift_s, True, "Time")
    kg = [0] + [int(round(p["load_kg"])) for p in pts] + [0]
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(lambda i: kg[manager.IndexToNode(i)]),
                                            0, [int(t["cap_kg"])] * k, True, "Kg")
    lit = [0] + [int(round(_litres(p["load"], t["density"]))) for p in pts] + [0]
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(lambda i: lit[manager.IndexToNode(i)]),
                                            0, [int(t["cap_l"])] * k, True, "Litres")
    for i in range(1, n + 1):
        routing.AddDisjunction([manager.NodeToIndex(i)], shift_s * 100)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    lim = max(0.3, float(time_limit_s))
    params.time_limit.seconds = int(lim)
    params.time_limit.nanos = int((lim - int(lim)) * 1e9)
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return [], pts
    trips, served = [], set()
    for v in range(k):
        idx, seq = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if 1 <= node <= n:
                seq.append(node - 1)
            idx = sol.Value(routing.NextVar(idx))
        if not seq:
            continue
        served.update(seq)
        stops = [station] + [pts[i]["node"] for i in seq] + [station]
        load = {s: sum(pts[i]["load"][s] for i in seq) for s in inp.streams}
        trips.append({"type": t["key"], "point_ids": [pts[i]["id"] for i in seq], "nodes": stops,
                      "kg": round(sum(load.values()), 1), "kg_by_stream": {s: round(x, 1) for s, x in load.items()},
                      "fill_pct": round(100 * max(sum(load.values()) / t["cap_kg"], _litres(load, t["density"]) / t["cap_l"])),
                      "drive_s": sum(times.t(a, b) for a, b in zip(stops[:-1], stops[1:])),
                      "service_s": sum(svc[i + 1] for i in seq), "unload_s": inp.unload_min * 60})
    # Longest trips first leaves the short top-up trip for the end of the shift.
    trips.sort(key=lambda x: -x["kg"])
    return trips, [p for i, p in enumerate(pts) if i not in served]


def _sweep(pts: list[dict], centre: tuple[float, float], shares: list[float]) -> list[list[dict]]:
    """Split a station's catchment into contiguous wedges around the station, sized by share of kg.
    Wedges do not overlap, so neither do the vehicles working them."""
    if len(shares) == 1:
        return [list(pts)]
    lat0 = math.radians(centre[1])
    ang = sorted(((math.atan2(p["lat"] - centre[1], (p["lon"] - centre[0]) * math.cos(lat0)), p) for p in pts),
                 key=lambda x: x[0])
    # Start the sweep at the widest empty gap so no natural cluster is cut in two.
    gaps = [(ang[(i + 1) % len(ang)][0] - ang[i][0]) % (2 * math.pi) for i in range(len(ang))]
    start = (max(range(len(gaps)), key=lambda i: gaps[i]) + 1) % len(ang)
    seq = [p for _, p in ang[start:] + ang[:start]]
    total = sum(p["load_kg"] for p in seq)
    bounds, acc = [], 0.0
    for sh in shares[:-1]:
        acc += sh / sum(shares) * total
        bounds.append(acc)
    groups, cur, run = [[] for _ in shares], 0, 0.0
    for p in seq:
        while cur < len(bounds) and run + p["load_kg"] / 2 > bounds[cur]:
            cur += 1
        groups[cur].append(p)
        run += p["load_kg"]
    return groups


def _allocate(station_loads: dict, vehicles: list[dict], times: _Times, depot: int) -> dict:
    """Share the work so each vehicle's load is proportional to its weight, without overlap.

    Stations are chained geographically (nearest next, starting near the depot). The chain's total
    load is cut into consecutive slices, one per vehicle, sized by weight. A vehicle therefore works
    one station, or the adjoining parts of two or three neighbouring stations.
    Returns {station_node: [(vehicle index, kg share), ...]}."""
    stations = [s for s, L in station_loads.items() if L > 0]
    if not stations:
        return {}
    chain, left, at = [], set(stations), depot
    while left:
        nxt = min(left, key=lambda s: times.t(at, s))
        chain.append(nxt)
        left.remove(nxt)
        at = nxt
    total_L = sum(station_loads[s] for s in chain)
    total_w = sum(v["weight"] for v in vehicles)
    quota = [v["weight"] / total_w * total_L for v in vehicles]
    order = sorted(range(len(vehicles)), key=lambda i: vehicles[i]["type"])  # keep same-type vehicles adjacent
    alloc = {s: [] for s in chain}
    k, remaining = 0, quota[order[0]]
    for s in chain:
        need = station_loads[s]
        while need > 1e-6 and k < len(order):
            i = order[k]
            take = min(need, remaining)
            alloc[s].append((i, take))
            need -= take
            remaining -= take
            if remaining <= 1e-6 and k < len(order) - 1:
                k += 1
                remaining = quota[order[k]]
        if need > 1e-6:  # rounding: last vehicle takes the rest
            alloc[s].append((order[-1], need))
    # Merge slivers (under 15% of a vehicle's quota) into the neighbouring vehicle at that station,
    # so no vehicle crosses to another station for a handful of houses.
    for s, parts in alloc.items():
        merged = []
        for i, amt in parts:
            if merged and (amt < 0.15 * quota[i] or merged[-1][1] < 0.15 * quota[merged[-1][0]]):
                j, prev = merged[-1]
                keep = j if prev >= amt else i
                merged[-1] = (keep, prev + amt)
            else:
                merged.append((i, amt))
        alloc[s] = merged
    return alloc


def _plan_territories(groups: dict, stations: list[dict], vehicles: list[dict], types: dict, depot: int,
                      times: _Times, inp: PlanInput, budget_s: float, report, frac0: float, frac1: float):
    """One planning pass: allocate vehicles to stations, sweep territories, route each territory."""
    loads = {s["node"]: sum(p["load_kg"] for p in groups.get(s["node"], [])) for s in stations}
    alloc = _allocate(loads, vehicles, times, depot)
    work = []  # (vehicle index, station node, points)
    for st in stations:
        vs = alloc.get(st["node"], [])
        if not vs:
            continue
        parts = _sweep(groups.get(st["node"], []), (st["lon"], st["lat"]), [amt for _, amt in vs])
        for (i, _), part in zip(vs, parts):
            if part:
                work.append((i, st["node"], part))
    # Points a vehicle cannot serve (street too narrow) move to another vehicle at the same station.
    for k, (i, st, part) in enumerate(work):
        t = types[vehicles[i]["type"]]
        bad = [p for p in part if p["min_width_m"] is not None and p["min_width_m"] < t["min_width"]]
        if not bad:
            continue
        other = [(j, w) for j, w in enumerate(work) if w[1] == st and types[vehicles[w[0]]["type"]]["min_width"] <= min(p["min_width_m"] for p in bad)]
        if other:
            j = other[0][0]
            work[j] = (work[j][0], st, work[j][2] + bad)
            work[k] = (i, st, [p for p in part if p not in bad])
    total_pts = max(1, sum(len(w[2]) for w in work))
    for v in vehicles:
        v.update({"at": depot, "busy_s": 0.0, "trips": []})
    dropped = []
    for n_done, (i, st, part) in enumerate(sorted(work, key=lambda w: (w[0], times.t(depot, w[1]))), start=1):
        v = vehicles[i]
        limit = budget_s * len(part) / total_pts
        trips, drop = _solve_group(part, st, types[v["type"]], times, inp, limit)
        dropped += drop
        for tr in trips:
            reposition = times.t(v["at"], st)
            dur = tr["drive_s"] + tr["service_s"] + tr["unload_s"]
            tr.update({"reposition_s": reposition, "start_s": v["busy_s"] + reposition, "station_node": st})
            tr["end_s"] = tr["start_s"] + dur
            v["busy_s"], v["at"] = tr["end_s"], st
            v["trips"].append(tr)
        report(None, frac0 + (frac1 - frac0) * n_done / max(1, len(work)))
    shift_s = inp.shift_h * 3600
    for v in vehicles:
        v["return_s"] = times.t(v["at"], depot) if v["trips"] else 0.0
        v["total_s"] = v["busy_s"] + v["return_s"]
        v["within_shift"] = v["total_s"] <= shift_s
    return vehicles, dropped


def _split_point(p: dict, t: dict, streams: list[str]) -> list[dict]:
    litres = _litres(p["load"], t["density"])
    k = math.ceil(max(p["load_kg"] / (0.9 * t["cap_kg"]), litres / (0.9 * t["cap_l"])))
    if k <= 1:
        return [p]
    idx = [streams.index(s) if s in streams else None for s in STREAMS]
    rows = list(zip(p["building_ids"], p["building_kg"]))
    rows_kg = [sum(r[1][STREAMS.index(s)] for s in streams) for r in rows]
    if len(rows) >= k:
        # Greedy: fill parts in street order up to an equal share of kg.
        target, parts, cur, acc = sum(rows_kg) / k, [], [], 0.0
        for r, w in zip(rows, rows_kg):
            if cur and acc + w > target * 1.05 and len(parts) < k - 1:
                parts.append(cur); cur, acc = [], 0.0
            cur.append(r); acc += w
        parts.append(cur)
    else:
        # One large building (for example an apartment complex): split its waste into equal loads.
        parts = [[(bid, [x / k for x in kg]) for bid, kg in rows] for _ in range(k)]
    out = []
    for i, part in enumerate(parts, start=1):
        kg_rows = [r[1] for r in part]
        load = {s: sum(r[STREAMS.index(s)] for r in kg_rows) for s in streams}
        if p.get("wet_excluded") and "wet" in load:
            load["wet"] = 0.0
        out.append({**p, "id": f"{p['id']}#{i}", "parent_id": p["id"], "building_ids": [r[0] for r in part],
                    "building_kg": kg_rows, "buildings": len(part), "load": load, "load_kg": sum(load.values()),
                    "span_m": p["span_m"] / len(parts)})
    return out


# ---------- Scheduling ----------

def _schedule_secondary(station_loads: list[dict], fleet: list[dict], mrf: int, start: int, times: _Times,
                        inp: PlanInput, streams: list[str]) -> list[dict]:
    trucks = []
    for row in fleet:
        t = _vehicle(row["type"], streams)
        for k in range(int(row["count"])):
            trucks.append({"id": f"{t['label']} {k + 1}", "type": row["type"], "spec": t, "at": start,
                           "busy_s": 0.0, "trips": []})
    if not trucks:
        return []
    queue = []
    for s in sorted(station_loads, key=lambda s: -s["kg"]):
        queue.append({"station": s, "left_kg": s["kg"], "left_by_stream": dict(s["kg_by_stream"])})
    for q in queue:
        while q["left_kg"] > 0.5:
            tr = min(trucks, key=lambda t: t["busy_s"] + times.t(t["at"], q["station"]["node"]))
            spec = tr["spec"]
            litres = _litres(q["left_by_stream"], spec["density"])
            frac = min(1.0, spec["cap_kg"] / q["left_kg"], spec["cap_l"] / max(litres, 1e-6))
            take = {s: x * frac for s, x in q["left_by_stream"].items()}
            kg = sum(take.values())
            to_st = times.t(tr["at"], q["station"]["node"])
            to_mrf = times.t(q["station"]["node"], mrf)
            dur = to_st + inp.truck_load_min * 60 + to_mrf + inp.truck_unload_min * 60
            tr["trips"].append({"station": q["station"]["id"], "kg": round(kg, 1), "start_s": tr["busy_s"],
                                "end_s": tr["busy_s"] + dur, "drive_s": to_st + to_mrf,
                                "fill_pct": round(100 * max(kg / spec["cap_kg"], _litres(take, spec["density"]) / spec["cap_l"]))})
            tr["busy_s"] += dur
            tr["at"] = mrf
            for s in take:
                q["left_by_stream"][s] -= take[s]
            q["left_kg"] -= kg
    for t in trucks:
        t["total_s"] = t["busy_s"]
        t.pop("spec")
    return trucks


# ---------- Geometry ----------

class _Paths:
    def __init__(self, G):
        self.G = G
        self.cache = {}

    def leg(self, a: int, b: int):
        if (a, b) not in self.cache:
            try:
                path = nx.shortest_path(self.G, a, b, weight="travel_time")
            except nx.NetworkXNoPath:
                path = [a, b] if a != b else [a]
            coords, length, _ = path_geometry(self.G, path)
            self.cache[(a, b)] = (coords, length)
        return self.cache[(a, b)]

    def route(self, stops: list[int]):
        coords, length = [], 0.0
        for a, b in zip(stops[:-1], stops[1:]):
            c, l = self.leg(a, b)
            coords.extend(c if not coords else c[1:])
            length += l
        return coords, length


# ---------- Public entry point ----------

def plan(inp: PlanInput, progress=None) -> dict:
    """Plan both tiers. `progress(stage, fraction)` is called as work advances (fraction 0-1)."""
    report = progress or (lambda stage, frac: None)
    report("Loading road network", 0.01)
    G, _, _, _, main = _graph(inp.pilot)
    times, paths = _Times(G), _Paths(G)
    shift_s = inp.shift_h * 3600
    depot, mrf = _nearest_nodes(inp.pilot, np.array([inp.depot, inp.mrf]))
    truck_start = _nearest_nodes(inp.pilot, np.array([inp.truck_depot]))[0] if inp.truck_depot else mrf

    primary_fleet = [r for r in inp.primary_fleet if int(r.get("count", 0)) > 0]
    secondary_fleet = [r for r in inp.secondary_fleet if int(r.get("count", 0)) > 0]
    if not primary_fleet:
        raise ValueError("Add at least one primary vehicle")
    types = {}
    for r in primary_fleet:
        t = types.setdefault(r["type"], {**_vehicle(r["type"], inp.streams), "count": 0})
        t["count"] += int(r["count"])
    for r in primary_fleet + secondary_fleet:
        if P.vehicle_class(r["type"])["tier"] != ("primary" if r in primary_fleet else "secondary"):
            raise ValueError(f"{r['type']} is not a {'primary' if r in primary_fleet else 'secondary'} vehicle")

    report("Preparing collection points", 0.03)
    pts = _sector_points(inp)
    report("Placing transfer stations", 0.07)
    if inp.stations:
        snodes = _nearest_nodes(inp.pilot, np.array(inp.stations))
        stations = [{"id": f"TS{i + 1}", "node": n, "lon": node_lonlat(G, n)[0], "lat": node_lonlat(G, n)[1],
                     "on_main_road": n in main, "suggested": False} for i, n in enumerate(snodes)]
    else:
        stations = _suggest(inp, pts)

    # Assign points to stations: nearest within the radius, keeping station loads similar and
    # within two truck loads.
    for s in stations:
        times.to(s["node"])
    groups = defaultdict(list)
    node_to_id = {s["node"]: s["id"] for s in stations}
    for p, n in zip(pts, _assign_balanced(inp, pts, [s["node"] for s in stations])):
        p["station"] = node_to_id[n]
        groups[n].append(p)

    # Split points that no single vehicle load can carry, keeping whole buildings together.
    # Split to the smallest vehicle in the fleet, so whichever vehicle gets the territory can carry it.
    smallest = min(types.values(), key=lambda t: min(t["cap_kg"], t["cap_l"] * 0.15))
    for node, g in list(groups.items()):
        groups[node] = [part for p in g for part in _split_point(p, smallest, inp.streams)]

    # Each vehicle works its own territory. Pass 1 sizes territories by capacity; later passes
    # resize them by how long each vehicle actually ran, so work is balanced without overlap.
    vehicles = []
    for row in primary_fleet:
        for k in range(int(row["count"])):
            vehicles.append({"id": f"{types[row['type']]['label']} {k + 1}", "type": row["type"],
                             "weight": types[row["type"]]["cap_kg"]})
    passes = 3
    best = None
    for i in range(passes):
        def rep(_stage, frac, i=i):
            report(f"Optimising routes: pass {i + 1} of {passes}", frac)
        vs, dropped = _plan_territories(groups, stations, [dict(v) for v in vehicles], types, depot, times, inp,
                                        inp.time_limit_s / passes, rep, 0.10 + 0.78 * i / passes, 0.10 + 0.78 * (i + 1) / passes)
        span = max(v["total_s"] for v in vs)
        if best is None or (len(dropped), span) < (len(best[1]), best[2]):
            best = (vs, dropped, span)
        busy = [v["total_s"] for v in vs if v["trips"]]
        mean = sum(busy) / len(busy) if busy else 0
        for v, done in zip(vehicles, vs):
            if done["total_s"] > 0 and mean > 0:
                v["weight"] *= (mean / done["total_s"]) ** 0.8
    vehicles, dropped, _ = best
    for v in vehicles:
        v.pop("weight", None)
    trips_by_station = defaultdict(list)
    for v in vehicles:
        for tr in v["trips"]:
            trips_by_station[tr["station_node"]].append(tr)

    # Station loads for tier 2.
    node_to_station = {s["node"]: s for s in stations}
    for s in stations:
        trs = trips_by_station.get(s["node"], [])
        s["points"] = len({p.get("parent_id", p["id"]) for p in groups.get(s["node"], [])})
        s["kg_by_stream"] = {st: round(sum(t["kg_by_stream"][st] for t in trs), 1) for st in inp.streams}
        s["kg"] = round(sum(s["kg_by_stream"].values()), 1)
        s["primary_trips"] = len(trs)
    report("Scheduling vehicles and trucks", 0.90)
    trucks = _schedule_secondary([s for s in stations if s["kg"] > 0], secondary_fleet, mrf, truck_start, times, inp, inp.streams)

    report("Drawing routes", 0.94)
    # Geometry and tidy output.
    trip_no = 0
    for v in vehicles:
        prev = depot
        for tr in v["trips"]:
            trip_no += 1
            tr["trip_id"] = f"T{trip_no}"
            tr["station"] = node_to_station[tr["station_node"]]["id"]
            coords, length = paths.route([prev] + tr["nodes"]) if prev != tr["nodes"][0] else paths.route(tr["nodes"])
            tr["geometry"] = coords
            tr["km"] = round(length / 1000, 2)
            tr["minutes"] = round((tr["end_s"] - tr["start_s"]) / 60, 1)
            tr["start_min"] = round(tr["start_s"] / 60, 1)
            tr["end_min"] = round(tr["end_s"] / 60, 1)
            prev = tr["nodes"][-1]
            for k in ("nodes", "station_node", "start_s", "end_s", "drive_s", "service_s", "unload_s", "reposition_s"):
                tr.pop(k, None)
        if v["trips"]:
            coords, length = paths.route([prev, depot])
            v["return_geometry"] = coords
            v["km"] = round(sum(t["km"] for t in v["trips"]) + length / 1000, 2)
        else:
            v["km"] = 0.0
        v["total_min"] = round(v.pop("total_s") / 60, 1)
        for k in ("at", "busy_s", "return_s"):
            v.pop(k, None)
    for s in stations:
        coords, length = paths.route([s["node"], mrf])
        s["to_mrf_geometry"] = coords
        s["to_mrf_km"] = round(length / 1000, 2)
        s["haul_min"] = round((times.t(s["node"], mrf) + times.t(mrf, s["node"])) / 60
                              + inp.truck_load_min + inp.truck_unload_min, 1)
    km_of = {s["id"]: s["to_mrf_km"] for s in stations}
    for t in trucks:
        t["km"] = round(sum(2 * km_of[tr["station"]] for tr in t["trips"]), 2)
        t["total_min"] = round(t.pop("total_s") / 60, 1)
        for tr in t["trips"]:
            tr["minutes"] = round((tr["end_s"] - tr["start_s"]) / 60, 1)
            tr["start_min"], tr["end_min"] = round(tr.pop("start_s") / 60, 1), round(tr.pop("end_s") / 60, 1)
            tr.pop("drive_s")
        for k in ("at", "busy_s"):
            t.pop(k, None)
    for s in stations:
        s.pop("node")

    report("Done", 1.0)
    collected = {st: round(sum(s["kg_by_stream"][st] for s in stations), 1) for st in inp.streams}
    uncollected = {st: round(sum(p["load"][st] for p in dropped), 1) for st in inp.streams}
    dropped_ids = sorted({p.get("parent_id", p["id"]) for p in dropped})
    primary_min = max((v["total_min"] for v in vehicles), default=0)
    secondary_min = max((t["total_min"] for t in trucks), default=0)
    last_haul = max((s["haul_min"] for s in stations if s["kg"] > 0), default=0)
    overall = max(secondary_min, primary_min + last_haul) if trucks else primary_min
    bwg_wet = round(sum(p.get("wet_kg_excluded", 0) for p in pts if p.get("is_bwg")), 1)

    return {
        "sector": inp.sector,
        "depot": node_lonlat(G, depot), "mrf": node_lonlat(G, mrf), "truck_depot": node_lonlat(G, truck_start),
        "stations": stations,
        "station_capacity_kg": round(station_capacity_kg(secondary_fleet, inp.streams)[0]),
        "primary": {"vehicles": vehicles, "trips": sum(len(v["trips"]) for v in vehicles),
                    "time_min": primary_min, "km": round(sum(v["km"] for v in vehicles), 1)},
        "secondary": {"trucks": trucks, "trips": sum(len(t["trips"]) for t in trucks),
                      "time_min": secondary_min, "km": round(sum(t["km"] for t in trucks), 1)},
        "summary": {
            "points": len(pts), "points_served": len(pts) - len(dropped_ids),
            "kg_collected": round(sum(collected.values()), 1), "kg_by_stream": collected,
            "uncollected_kg": round(sum(uncollected.values()), 1), "uncollected_by_stream": uncollected,
            "uncollected_points": dropped_ids,
            "bwg_wet_excluded_kg": bwg_wet,
            "time_to_complete_min": round(overall, 1), "shift_min": inp.shift_h * 60,
            "within_shift": overall <= inp.shift_h * 60,
            "vehicles_over_shift": [v["id"] for v in vehicles if not v["within_shift"]],
        },
        "assumptions": [
            "Waste quantities are estimates from building use and units (weighed data replaces them when entered).",
            "Road speeds per road class and service rates per vehicle class are assumptions (reference/vehicles.json).",
            f"Transfer stations cover points within {inp.radius_m:.0f} m along the road network, with similar loads, "
            f"each holding at most two loads of the largest truck ({station_capacity_kg(secondary_fleet, inp.streams)[0] / 1000:.1f} t).",
            "Each door-to-door vehicle works its own territory (a wedge of one station's catchment, or adjoining wedges of two neighbouring stations), so routes do not overlap.",
            f"Unloading at a transfer station takes {inp.unload_min:.0f} min; trucks load {inp.truck_load_min:.0f} min and unload {inp.truck_unload_min:.0f} min.",
            "Trucks shuttle while primary collection is under way; completion adds the last haul to the MRF.",
            "Vehicle width limits apply to the streets a vehicle collects from, not to streets it drives through.",
            "Bulk waste generators' wet waste is excluded: processed at source or covered by EBWGR certificates (SWM Rules 2026, r. 6).",
        ],
    }
