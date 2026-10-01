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
5. Truck trips are built per stream with OR-Tools: each trip leaves the MRF, picks up one stream
   at one or more transfer stations and returns, so streams are never mixed (r. 8(h)(v)-(vi)).
   Trips go to the truck of their type that can start them soonest, longest trips first.

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
from backend.routing import parks as PK
from backend.routing import points as P
from backend.routing.matrix import RoutingService, routing_service
from backend.routing.network import _road_class, load_graph, node_lonlat

STREAMS = stream_keys()
MAIN_ROADS = {"primary", "secondary", "tertiary"}  # trunk excluded: elevated or access-controlled in HSR


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
    # Park composting: {"enabled", "method", "share_mode", "dropoff_pct", "radius_m", "overrides", "excluded"}
    park: dict | None = None


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


def _routing(pilot: str) -> RoutingService:
    return routing_service(P._area_for(pilot))


def _nearest_nodes(pilot: str, lonlat: np.ndarray) -> list[int]:
    return _routing(pilot).nearest_nodes(lonlat)


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
    from backend.survey import state
    pts = P.collection_points(inp.pilot, state.building_states(inp.pilot), inp.sector)
    out = []
    for p in pts:
        kg = {s: (0.0 if s == "wet" and p.get("wet_excluded") else p["kg"][s]) for s in inp.streams}
        if sum(kg.values()) <= 0:
            continue
        out.append({**p, "load": kg, "load_kg": sum(kg.values())})
    nodes = _nearest_nodes(inp.pilot, np.array([[p["lon"], p["lat"]] for p in out])) if out else []
    known = _routing(inp.pilot).index
    for p, n in zip(out, nodes):
        # The vehicle drives the point's stretch of street; `node` (its middle) stands for the
        # point when placing stations and parks.
        path = [int(x) for x in p.get("path_nodes") or [] if int(x) in known]
        p["path"] = [x for i, x in enumerate(path) if i == 0 or x != path[i - 1]] or [n]
        p["node"] = p["path"][len(p["path"]) // 2]
    return out


def _ways(p: dict, times: RoutingService) -> list[tuple[list[int], float]]:
    """Ways to drive a point's street: (junctions in driving order, seconds lost against the best way).
    A two-way street can be driven from either end; a one-way street loses the detour against traffic,
    so the optimiser takes it the right way round."""
    path = p["path"]
    if len(path) == 1 or path[0] == path[-1]:
        return [(path, 0.0)]
    ways = [(w, sum(times.along_s(a, b) for a, b in zip(w[:-1], w[1:]))) for w in (path, path[::-1])]
    best = min(c for _, c in ways)
    return [(w, c - best) for w, c in ways]


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


def _park_access_nodes(pilot: str, sites: list[dict], pts: list[dict], radius: float) -> dict:
    """Put each park's composting site at the road junction on the park's edge (within about 60 m)
    that has the most wet waste within the catchment radius, so large parks such as lake parks are
    served from the side where people live, not from their middle."""
    import shapely
    from shapely.geometry import shape
    G, _, nodes, xy, _ = _graph(pilot)
    wet_at = defaultdict(float)
    for p in pts:
        if not p.get("is_bwg"):
            wet_at[p["node"]] += p["load"].get("wet", 0.0)
    out = {}
    for x in sites:
        edge = shape(x["geometry"]).buffer(0.00055)  # about 60 m at this latitude
        near = [int(n) for n in nodes[shapely.contains_xy(edge, xy[:, 0], xy[:, 1])]]
        if not near:
            near = _nearest_nodes(pilot, np.array([[x["lon"], x["lat"]]]))
        out[x["id"]] = max(near, key=lambda n: sum(wet_at[m] for m in _reach(pilot, n, radius) if m in wet_at))
    return out


# ---------- Tier 1: vehicle territories and trips ----------

def _solve_group(pts: list[dict], station: int, t: dict, times: RoutingService, inp: PlanInput,
                 time_limit_s: int) -> tuple[list[dict], list[dict]]:
    """Trips for ONE vehicle over its own territory: a capacitated VRP whose routes are that
    vehicle's successive trips from the transfer station and back."""
    if not pts:
        return [], []
    n = len(pts)
    # Each point's street can be driven either way: one routing node per way, of which one is visited.
    ways = [_ways(p, times) for p in pts]
    copies = [(i, w, extra) for i, ws in enumerate(ways) for w, extra in ws]
    m = len(copies)
    uniq = list(dict.fromkeys([station] + [w[0] for _, w, _ in copies] + [w[-1] for _, w, _ in copies]))
    pos = {x: j for j, x in enumerate(uniq)}
    tm = times.matrix(uniq).time_s
    entry = [pos[station]] + [pos[w[0]] for _, w, _ in copies] + [pos[station]]
    exit_ = [pos[station]] + [pos[w[-1]] for _, w, _ in copies] + [pos[station]]
    of = [None] + [i for i, _, _ in copies] + [None]
    tot_kg = sum(p["load_kg"] for p in pts)
    tot_l = sum(_litres(p["load"], t["density"]) for p in pts)
    k = min(n, math.ceil(max(tot_kg / (0.9 * t["cap_kg"]), tot_l / (0.9 * t["cap_l"]))) + 2)
    manager = pywrapcp.RoutingIndexManager(m + 2, k, [0] * k, [m + 1] * k)
    routing = pywrapcp.RoutingModel(manager)
    svc_pt = [P.service_minutes(p, t["key"], inp.streams) * 60 for p in pts]
    # Leaving a node costs its service time (and any detour against a one-way street).
    svc = [0.0] + [svc_pt[i] + extra for i, _, extra in copies] + [0.0]
    arc = (tm[np.ix_(exit_, entry)] + np.array(svc)[:, None]).astype(np.int64).tolist()
    cb = routing.RegisterTransitCallback(lambda i, j: arc[manager.IndexToNode(i)][manager.IndexToNode(j)])
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    for v in range(k):
        routing.SetFixedCostOfVehicle(int(inp.unload_min * 60), v)
    shift_s = int(inp.shift_h * 3600)
    routing.AddDimension(cb, 0, shift_s, True, "Time")
    kg = [0] + [int(round(pts[i]["load_kg"])) for i, _, _ in copies] + [0]
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(lambda i: kg[manager.IndexToNode(i)]),
                                            0, [int(t["cap_kg"])] * k, True, "Kg")
    lit = [0] + [int(round(_litres(pts[i]["load"], t["density"]))) for i, _, _ in copies] + [0]
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(lambda i: lit[manager.IndexToNode(i)]),
                                            0, [int(t["cap_l"])] * k, True, "Litres")
    by_point = defaultdict(list)
    for c, (i, _, _) in enumerate(copies, start=1):
        by_point[i].append(manager.NodeToIndex(c))
    for i in range(n):
        routing.AddDisjunction(by_point[i], shift_s * 100)  # drive it one way; dropping is a last resort
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
        idx, seq, drive = routing.Start(v), [], []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if 1 <= node <= m:
                seq.append(of[node])
                drive.append(copies[node - 1][1])
            idx = sol.Value(routing.NextVar(idx))
        if not seq:
            continue
        served.update(seq)
        load = {s: sum(pts[i]["load"][s] for i in seq) for s in inp.streams}
        # Wet waste bound for park composting is unloaded at the park site(s) on the way back.
        park_kg = defaultdict(float)
        for i in seq:
            if pts[i].get("park_vehicle_kg", 0) > 0:
                park_kg[(pts[i]["park_node"], pts[i]["park_id"])] += pts[i]["park_vehicle_kg"]
        park_stops, at, todo = [], drive[-1][-1], set(park_kg)
        while todo:
            nxt = min(todo, key=lambda k: times.t(at, k[0]))
            park_stops.append(nxt)
            todo.remove(nxt)
            at = nxt[0]
        stops = [station] + [x for w in drive for x in w] + [k[0] for k in park_stops] + [station]
        stops = [x for j, x in enumerate(stops) if j == 0 or x != stops[j - 1]]
        to_parks = sum(park_kg.values())
        to_station = dict(load)
        if "wet" in to_station:
            to_station["wet"] = max(0.0, to_station["wet"] - to_parks)
        park_unload = PK.reference()["park_unload_min"]["value"] * 60 * len(park_stops)
        trips.append({"type": t["key"], "point_ids": [pts[i]["id"] for i in seq], "nodes": stops,
                      "kg": round(sum(load.values()), 1), "kg_by_stream": {s: round(x, 1) for s, x in to_station.items()},
                      "park_drops": [{"park_id": k[1], "kg": round(park_kg[k], 1)} for k in park_stops],
                      "fill_pct": round(100 * max(sum(load.values()) / t["cap_kg"], _litres(load, t["density"]) / t["cap_l"])),
                      "drive_s": _drive_between(station, drive, [k[0] for k in park_stops], times),
                      "service_s": sum(svc_pt[i] for i in seq), "unload_s": inp.unload_min * 60 + park_unload})
    # Longest trips first leaves the short top-up trip for the end of the shift.
    trips.sort(key=lambda x: -x["kg"])
    return trips, [p for i, p in enumerate(pts) if i not in served]


def _drive_between(station: int, drive: list[list[int]], parks: list[int], times: RoutingService) -> float:
    """Driving time between streets: station to the first street, each street's end to the next
    street's start, then the parks and back to the station. Driving along a street is part of
    its service time (at collection speed), so it is not counted again here."""
    legs = [station] + [x for w in drive for x in (w[0], w[-1])] + parks + [station]
    # Leg 2j+1 -> 2j+2 runs along street j.
    along = {2 * j + 1 for j in range(len(drive))}
    return sum(times.t(a, b) for j, (a, b) in enumerate(zip(legs[:-1], legs[1:])) if j not in along)


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


def _allocate(station_loads: dict, vehicles: list[dict], times: RoutingService, depot: int) -> dict:
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
                      times: RoutingService, inp: PlanInput, budget_s: float, report, frac0: float, frac1: float):
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

    # A part made of whole buildings can still be too big for one load, by weight or by volume
    # (for example one large apartment block in a street of houses). Split such parts into equal loads.
    def fits(part):
        raw = {st: sum(r[1][STREAMS.index(st)] for r in part) for st in streams}
        return sum(raw.values()) <= 0.9 * t["cap_kg"] and _litres(raw, t["density"]) <= 0.9 * t["cap_l"]

    refined = []
    for part in parts:
        if fits(part):
            refined.append(part)
            continue
        raw = {st: sum(r[1][STREAMS.index(st)] for r in part) for st in streams}
        j = math.ceil(max(sum(raw.values()) / (0.9 * t["cap_kg"]), _litres(raw, t["density"]) / (0.9 * t["cap_l"])))
        refined += [[(bid, [x / j for x in kg]) for bid, kg in part] for _ in range(j)]
    parts = refined
    # Building rows hold gross amounts; scale each part to the point's net load (after residents'
    # own drop-off at a park) and give each part its share of the wet waste bound for the park.
    gross = {st: sum(r[1][STREAMS.index(st)] for r in rows) for st in streams}
    out = []
    for i, part in enumerate(parts, start=1):
        kg_rows = [r[1] for r in part]
        raw = {st: sum(r[STREAMS.index(st)] for r in kg_rows) for st in streams}
        load = {st: (raw[st] * p["load"][st] / gross[st] if gross[st] else 0.0) for st in streams}
        if p.get("wet_excluded") and "wet" in load:
            load["wet"] = 0.0
        wet_share = raw.get("wet", 0) / gross["wet"] if gross.get("wet") else 0.0
        extra = {k: p[k] * wet_share for k in ("park_vehicle_kg", "park_wet_kg", "dropoff_kg") if p.get(k)}
        out.append({**p, **extra, "id": f"{p['id']}#{i}", "parent_id": p["id"], "building_ids": [r[0] for r in part],
                    "building_kg": kg_rows, "buildings": len(part), "load": load, "load_kg": sum(load.values()),
                    "span_m": p["span_m"] / len(parts)})
    return out


# ---------- Tier 2: trucks from transfer stations to the MRF ----------

def _secondary_trips(items: list[dict], specs: dict, mrf: int, times: RoutingService, inp: PlanInput,
                     time_limit_s: float) -> tuple[list[dict], list[dict]]:
    """Truck trips for ONE stream: a capacitated VRP from the MRF whose routes are single trips.

    A single-body truck carries one stream per trip so streams are never mixed (SWM Rules 2026,
    r. 8(h)(v)-(vi)), but one trip may pick that stream up at several transfer stations.
    `items` are station loads of the stream, each small enough for the smallest truck.
    `specs` maps truck type to its capacity. Returns (trips, items left over)."""
    if not items:
        return [], []
    stream = items[0]["stream"]
    n = len(items)
    nodes = [mrf] + [it["node"] for it in items] + [mrf]
    tm = times.matrix(nodes).time_s.tolist()
    load_s = inp.truck_load_min * 60
    svc = [0.0] + [load_s] * n + [0.0]
    total_kg = sum(it["kg"] for it in items)
    slots = []  # one routing "vehicle" per possible trip, by truck type
    for key, spec in specs.items():
        cap_kg = min(spec["cap_kg"], spec["cap_l"] / 1000 * spec["density"][stream])
        slots += [(key, spec, cap_kg)] * min(n, math.ceil(total_kg / (0.9 * cap_kg)) + 1)
    k = len(slots)
    manager = pywrapcp.RoutingIndexManager(len(nodes), k, [0] * k, [n + 1] * k)
    routing = pywrapcp.RoutingModel(manager)
    cb = routing.RegisterTransitCallback(
        lambda i, j: int(tm[manager.IndexToNode(i)][manager.IndexToNode(j)] + svc[manager.IndexToNode(i)]))
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    for v in range(k):
        routing.SetFixedCostOfVehicle(int(inp.truck_unload_min * 60), v)
    shift_s = int(inp.shift_h * 3600)
    routing.AddDimension(cb, 0, shift_s, True, "Time")
    kg = [0] + [int(round(it["kg"])) for it in items] + [0]
    routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(lambda i: kg[manager.IndexToNode(i)]),
                                            0, [int(c) for _, _, c in slots], True, "Kg")
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
        return [], items
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
        key, spec, cap_kg = slots[v]
        load = sum(items[i]["kg"] for i in seq)
        stations = list(dict.fromkeys(items[i]["station"] for i in seq))
        trips.append({"type": key, "stream": stream, "stations": stations, "station": stations[0],
                      "stops": [items[i]["node"] for i in seq], "kg": round(load, 1),
                      "fill_pct": round(100 * max(load / spec["cap_kg"],
                                                  load / spec["density"][stream] * 1000 / spec["cap_l"])),
                      "by_station": {st: round(sum(items[i]["kg"] for i in seq if items[i]["station"] == st), 1)
                                     for st in stations}})
    return trips, [it for i, it in enumerate(items) if i not in served]


def _schedule_secondary(station_loads: list[dict], fleet: list[dict], mrf: int, start: int, times: RoutingService,
                        inp: PlanInput, streams: list[str], time_limit_s: float = 2.0) -> tuple[list[dict], list[dict]]:
    """Plan single-stream truck trips with OR-Tools, then give each trip to the truck of its type
    that can start it soonest (longest trips first). Trucks start at `start` (yard or MRF).
    Returns (trucks, loads left at stations because no trip could take them within the shift)."""
    specs, trucks = {}, []
    for row in fleet:
        spec = specs.setdefault(row["type"], _vehicle(row["type"], streams))
        for k in range(int(row["count"])):
            trucks.append({"id": f"{spec['label']} {k + 1}", "type": row["type"], "at": start, "busy_s": 0.0, "trips": []})
    if not trucks:
        return [], []
    # Split station loads into pieces the smallest truck can carry.
    smallest = min(specs.values(), key=lambda t: t["cap_kg"])
    items = defaultdict(list)
    for st in station_loads:
        for stream, kg in st["kg_by_stream"].items():
            if kg <= 0.5:
                continue
            cap = 0.9 * min(smallest["cap_kg"], smallest["cap_l"] / 1000 * smallest["density"][stream])
            parts = math.ceil(kg / cap)
            items[stream] += [{"station": st["id"], "node": st["node"], "stream": stream, "kg": kg / parts}] * parts
    trips, left = [], []
    for stream in streams:
        t, lo = _secondary_trips([dict(x) for x in items.get(stream, [])], specs, mrf, times, inp,
                                 time_limit_s / max(1, len(items)))
        trips += t
        left += lo
    load_s, unload_s = inp.truck_load_min * 60, inp.truck_unload_min * 60

    def duration(at, tr):
        legs = [at] + tr["stops"] + [mrf]
        drive = sum(times.t(a, b) for a, b in zip(legs[:-1], legs[1:]))
        return drive, drive + load_s * len(tr["stops"]) + unload_s

    for tr in sorted(trips, key=lambda x: -duration(mrf, x)[1]):
        own = [t for t in trucks if t["type"] == tr["type"]]
        truck = min(own, key=lambda t: t["busy_s"] + duration(t["at"], tr)[1])
        drive, dur = duration(truck["at"], tr)
        tr.update({"nodes": [truck["at"]] + tr.pop("stops") + [mrf], "start_s": truck["busy_s"],
                   "end_s": truck["busy_s"] + dur, "drive_s": drive})
        truck["trips"].append(tr)
        truck["busy_s"] += dur
        truck["at"] = mrf
    for t in trucks:
        t["total_s"] = t["busy_s"]
    return trucks, [{"station": x["station"], "stream": x["stream"], "kg": round(x["kg"], 1)} for x in left]


# ---------- Public entry point ----------

def plan(inp: PlanInput, progress=None) -> dict:
    """Plan both tiers. `progress(stage, fraction)` is called as work advances (fraction 0-1)."""
    report = progress or (lambda stage, frac: None)
    report("Loading road network", 0.01)
    G, _, _, _, main = _graph(inp.pilot)
    times = paths = _routing(inp.pilot)  # travel times and route geometry, shared between plans
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
    park_cfg = inp.park or {}
    park_on = bool(park_cfg.get("enabled"))
    park_sites, park_stats = [], {}
    pref = PK.reference()
    dropoff_pct = float(park_cfg.get("dropoff_pct", pref["resident_dropoff_pct"]["value"]))
    if park_on:
        report("Allocating wet waste to park composting", 0.05)
        park_sites = PK.sites(inp.pilot, inp.sector, park_cfg.get("method"), park_cfg.get("share_mode", "tiers"),
                              park_cfg.get("overrides"), park_cfg.get("excluded"))
        usable = [x for x in park_sites if x["usable"]]
        radius = float(park_cfg.get("radius_m", pref["catchment_radius_m"]["value"]))
        site_nodes = _park_access_nodes(inp.pilot, usable, pts, radius)
        for x in park_sites:
            x["node"] = site_nodes.get(x["id"])
            if x["node"] is not None:  # show the site where it meets the road
                x["lon"], x["lat"] = node_lonlat(G, x["node"])
        park_stats = PK.allocate(pts, park_sites, site_nodes, lambda n, c: _reach(inp.pilot, n, float(c)), radius, dropoff_pct)
        for p in pts:
            if p.get("dropoff_kg"):  # residents carry this part themselves
                p["load"]["wet"] = max(0.0, p["load"]["wet"] - p["dropoff_kg"])
                p["load_kg"] = sum(p["load"].values())
        pts = [p for p in pts if p["load_kg"] > 0.01]
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
        times.times_to(s["node"])
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
    passes = 4
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
                v["weight"] *= (mean / done["total_s"]) ** 1.0
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
    trucks, left_at_stations = _schedule_secondary([s for s in stations if s["kg"] > 0], secondary_fleet, mrf, truck_start,
                                                   times, inp, inp.streams)
    baseline = None
    if park_on and secondary_fleet:
        # The same stations if the park-bound wet waste went there instead.
        extra = defaultdict(float)
        for p in pts:
            if p.get("park_wet_kg"):
                extra[p["station"]] += p["park_wet_kg"]
        base_st = []
        for st in stations:
            kgs = dict(st["kg_by_stream"])
            if "wet" in kgs:
                kgs["wet"] += extra.get(st["id"], 0.0)
            if sum(kgs.values()) > 0:
                base_st.append({"id": st["id"], "node": st["node"], "kg": sum(kgs.values()), "kg_by_stream": kgs})
        bt, _ = _schedule_secondary(base_st, secondary_fleet, mrf, truck_start, times, inp, inp.streams)
        baseline = {"truck_trips": sum(len(t["trips"]) for t in bt),
                    "truck_min": round(max((t["total_s"] for t in bt), default=0) / 60, 1),
                    "kg_to_mrf": round(sum(x["kg"] for x in base_st), 1)}

    report("Drawing routes", 0.94)
    # Where door-to-door vehicle time goes, summed over all vehicles.
    split = defaultdict(float)
    for v in vehicles:
        for tr in v["trips"]:
            split["collecting"] += tr["service_s"]
            split["driving"] += tr["drive_s"] + tr["reposition_s"]
            split["unloading"] += tr["unload_s"]
        split["driving"] += v["return_s"]

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
    for t in trucks:
        for tr in t["trips"]:
            tr["geometry"], length = paths.route(tr.pop("nodes"))
            tr["km"] = round(length / 1000, 2)
        t["km"] = round(sum(tr["km"] for tr in t["trips"]), 2)
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
    delivered = defaultdict(float)
    for v in vehicles:
        for tr in v["trips"]:
            for d in tr.get("park_drops", []):
                delivered[d["park_id"]] += d["kg"]
    collected = {st: round(sum(s["kg_by_stream"][st] for s in stations), 1) for st in inp.streams}
    if "wet" in collected:
        collected["wet"] = round(collected["wet"] + sum(delivered.values()), 1)
    yield_pct = pref["compost_yield_pct"]["value"]
    park_out = []
    for x in park_sites:
        st = park_stats.get(x["id"], {})
        received = st.get("dropoff_kg", 0.0) + delivered.get(x["id"], 0.0)
        park_out.append({**{k: v for k, v in x.items() if k != "node"},
                         "points": st.get("points", 0), "dropoff_kg": round(st.get("dropoff_kg", 0.0), 1),
                         "vehicle_kg": round(delivered.get(x["id"], 0.0), 1), "received_kg": round(received, 1),
                         "compost_kg": round(received * yield_pct / 100, 1),
                         "use_pct": round(100 * received / x["capacity_kg"]) if x["capacity_kg"] else 0})
    catchment = [{"from": [p["lon"], p["lat"]], "park_id": p["park_id"], "kg": round(p["park_wet_kg"], 1)}
                 for p in pts if p.get("park_id") and "#" not in str(p["id"])]
    catchment += [{"from": [p["lon"], p["lat"]], "park_id": p["park_id"], "kg": round(p["park_wet_kg"], 1)}
                  for p in pts if p.get("park_id") and str(p["id"]).endswith("#1")]
    home_composted = round(sum(p.get("home_composted_kg", 0.0) for p in pts), 1)
    to_parks = round(sum(x["received_kg"] for x in park_out), 1)
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
        "parks": park_out,
        "catchment": catchment,
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
            "left_at_stations_kg": round(sum(x["kg"] for x in left_at_stations), 1),
            "left_at_stations": left_at_stations,
            "bwg_wet_excluded_kg": bwg_wet,
            "time_to_complete_min": round(overall, 1), "shift_min": inp.shift_h * 60,
            "within_shift": overall <= inp.shift_h * 60,
            "primary_time_split_min": {k: round(x / 60, 1) for k, x in split.items()},
            "last_haul_min": round(last_haul, 1),
            "vehicles_over_shift": [v["id"] for v in vehicles if not v["within_shift"]]
                                   + [t["id"] for t in trucks if t["total_min"] > inp.shift_h * 60],
            "composting": {
                "parks_enabled": park_on,
                "wet_to_parks_kg": to_parks,
                "resident_dropoff_kg": round(sum(x["dropoff_kg"] for x in park_out), 1),
                "vehicle_to_parks_kg": round(sum(x["vehicle_kg"] for x in park_out), 1),
                "compost_kg": round(sum(x["compost_kg"] for x in park_out), 1),
                "home_composted_kg": home_composted,
                "wet_diverted_kg": round(to_parks + home_composted, 1),
                "park_sites": sum(1 for x in park_out if x["usable"]),
                "without_parks": baseline,
            },
        },
        "assumptions": [
            "Waste quantities are estimates from building use and units (weighed data replaces them when entered).",
            "Road speeds per road class and service rates per vehicle class are assumptions (reference/vehicles.json).",
            f"Transfer stations cover points within {inp.radius_m:.0f} m along the road network, with similar loads, "
            f"each holding at most two loads of the largest truck ({station_capacity_kg(secondary_fleet, inp.streams)[0] / 1000:.1f} t).",
            "Each door-to-door vehicle works its own territory (a wedge of one station's catchment, or adjoining wedges of two neighbouring stations), so routes do not overlap.",
            f"Unloading at a transfer station takes {inp.unload_min:.0f} min; trucks load {inp.truck_load_min:.0f} min and unload {inp.truck_unload_min:.0f} min.",
            "Trucks shuttle while primary collection is under way; completion adds the last haul to the MRF.",
            "Each truck trip carries one stream and may collect it from several transfer stations; streams are not mixed in transport (SWM Rules 2026, r. 8(h)(v)-(vi)).",
            "Vehicle width limits apply to the streets a vehicle collects from, not to streets it drives through.",
            "Bulk waste generators' wet waste is excluded: processed at source or covered by EBWGR certificates (SWM Rules 2026, r. 6).",
            "Wet waste composted inside buildings (from the survey) is not collected.",
        ] + ([
            f"Park composting: {pref['methods'][park_cfg.get('method') or pref['default_method']]['label'].lower()} at "
            f"{pref['methods'][park_cfg.get('method') or pref['default_method']]['kg_per_day_per_m2']} kg/day per m2 (CPHEEO Table 3.5); "
            f"each site capped at {PK.site_cap_kg()[0] / 1000:.0f} t/day so no buffer zone is needed ({PK.site_cap_kg()[1]}).",
            f"Buildings within {park_cfg.get('radius_m', pref['catchment_radius_m']['value'])} m by road feed the nearest park site with room, residential first; "
            f"residents carry {dropoff_pct:.0f}% themselves and vehicles unload the rest at the park "
            f"({pref['park_unload_min']['value']} min per stop) before the transfer station.",
            f"Compost produced is assumed to be {yield_pct}% of the wet waste received.",
        ] if park_on else []),
    }


# ---------- Fleet sizing ----------

def _resize(rows: list[dict], total: int) -> list[dict]:
    """Share `total` vehicles over the rows in proportion to their current counts (largest remainder)."""
    base = sum(int(r["count"]) for r in rows)
    want = [total * int(r["count"]) / base for r in rows]
    out = [int(w) for w in want]
    for i in sorted(range(len(rows)), key=lambda i: want[i] - out[i], reverse=True)[:total - sum(out)]:
        out[i] += 1
    return [{**r, "count": c} for r, c in zip(rows, out) if c > 0]


def _count(rows: list[dict]) -> int:
    return sum(int(r["count"]) for r in rows)


def suggest_fleet(inp: PlanInput, target_h: float, progress=None, rounds: int = 4) -> dict:
    """Find the smallest fleet, keeping the chosen vehicle types and their mix, whose plan finishes
    within `target_h` hours. Each round runs the full optimiser and rescales the tier that is too
    slow (door-to-door vehicles, or trucks) by how far over or under the target it ran.
    Returns the fleet, its plan, every round tried, and advice on what limits the time."""
    report = progress or (lambda stage, frac: None)
    target = target_h * 60
    primary = [dict(r) for r in inp.primary_fleet if int(r.get("count", 0)) > 0]
    secondary = [dict(r) for r in inp.secondary_fleet if int(r.get("count", 0)) > 0]
    if not primary:
        raise ValueError("Add at least one primary vehicle")
    tried, plans, best, seen = [], [], None, set()
    for i in range(rounds):
        key = (tuple((r["type"], r["count"]) for r in primary), tuple((r["type"], r["count"]) for r in secondary))
        if key in seen:
            break
        seen.add(key)

        def rep(stage, frac, i=i):
            report(f"Round {i + 1}: {stage[:1].lower()}{stage[1:]}", (i + frac) / rounds)
        r = plan(PlanInput(**{**inp.__dict__, "primary_fleet": primary, "secondary_fleet": secondary}), rep)
        plans.append(r)
        s = r["summary"]
        total = s["time_to_complete_min"]
        ok = total <= target and not s["uncollected_points"]
        tried.append({"primary_fleet": primary, "secondary_fleet": secondary, "time_min": total,
                      "primary_min": r["primary"]["time_min"], "secondary_min": r["secondary"]["time_min"], "fits": ok})
        if ok and (best is None or _count(primary) + _count(secondary) < _count(best[0]) + _count(best[1])):
            best = (primary, secondary, r)
        # Door-to-door work must end in time for the last truck haul; trucks shuttle meanwhile.
        p_goal = max(30.0, target - s["last_haul_min"]) if secondary else target
        p_scale = r["primary"]["time_min"] / p_goal
        s_scale = r["secondary"]["time_min"] / target if secondary else 0
        n_p = max(1, math.ceil(_count(primary) * p_scale * (1.08 if p_scale > 1 else 1.0)))
        n_s = max(1, math.ceil(_count(secondary) * s_scale * (1.05 if s_scale > 1 else 1.0))) if secondary else 0
        if ok:  # within target: try one size smaller on the tier with the most slack
            n_p = min(n_p, _count(primary))
            n_s = min(n_s, _count(secondary))
            if (n_p, n_s) == (_count(primary), _count(secondary)):
                break
        n_p = min(n_p, 200)
        n_s = min(n_s, 50)
        primary = _resize(primary, n_p)
        secondary = _resize(secondary, n_s) if secondary else []
    if best is None:  # nothing fitted: return the fastest plan found
        fastest = min(range(len(tried)), key=lambda k: tried[k]["time_min"])
        best = (tried[fastest]["primary_fleet"], tried[fastest]["secondary_fleet"], plans[fastest])
    primary, secondary, r = best
    report("Done", 1.0)
    return {"target_h": target_h, "fits": r["summary"]["time_to_complete_min"] <= target,
            "primary_fleet": primary, "secondary_fleet": secondary, "rounds": tried,
            "advice": _fleet_advice(r, target), "result": r}


def _fleet_advice(r: dict, target: float) -> list[dict]:
    """What limits the time, worded on the page from these codes and numbers."""
    s = r["summary"]
    split = s["primary_time_split_min"]
    total = sum(split.values()) or 1
    out = [{"code": "time_split", **{k: round(100 * v / total) for k, v in split.items()}}]
    over = [x["id"] for x in r["stations"] if x["kg"] > r["station_capacity_kg"]]
    if over:
        out.append({"code": "stations_over_capacity", "stations": over})
    elif split.get("driving", 0) / total < 0.15:
        out.append({"code": "stations_little_effect", "driving_pct": round(100 * split.get("driving", 0) / total)})
    if r["secondary"]["trucks"] and r["secondary"]["time_min"] >= r["primary"]["time_min"] + s["last_haul_min"]:
        out.append({"code": "trucks_limit", "truck_min": r["secondary"]["time_min"]})
    if s["time_to_complete_min"] > target:
        out.append({"code": "not_reached", "time_min": s["time_to_complete_min"]})
    return out
