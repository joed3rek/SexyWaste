"""Park composting: public parks set aside part of their area to compost the neighbourhood's wet waste.

Each public park (OSM leisure=park or garden, not tagged access=private/no) gets a composting site of
a share of its area: tiered by park size (small parks a larger share) or a flat average, editable per
park. Capacity = site area x the land norm of the composting method (CPHEEO Table 3.5), capped at the
SWM Rules 2026 buffer-zone threshold so no park site needs a buffer zone.

Wet waste from buildings within the catchment radius (by road) is allocated to the nearest site with
room, residential buildings first. Residents carry a share there themselves; collection vehicles
bring the rest and unload at the park before taking the other streams to the transfer station.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

import geopandas as gpd
from shapely.geometry import mapping

from backend import regulations as regs
from backend.buildings import layers
from backend.buildings.layers import METRIC_CRS
from backend.config import ROOT


@lru_cache(maxsize=1)
def reference() -> dict:
    return json.loads((ROOT / "reference" / "composting.json").read_text(encoding="utf-8"))


def site_cap_kg() -> tuple[float, str]:
    bz = regs.swm()["facilities"]["buffer_zone"]
    return bz["capacity_tpd_threshold"] * 1000.0, regs.cite(bz["rule"])


def tier_pct(area_m2: float) -> float:
    for t in reference()["share"]["tiers"]:
        if t["below_m2"] is None or area_m2 < t["below_m2"]:
            return float(t["pct"])
    return float(reference()["share"]["min_pct"])


@lru_cache(maxsize=16)
def _parks_in_sector(pilot: str, sector: str) -> gpd.GeoDataFrame:
    pk = layers.parks(pilot).to_crs(METRIC_CRS)
    excluded = set(reference()["excluded_access"])
    pk = pk[~pk["access"].isin(excluded)] if "access" in pk else pk
    sec = layers.sectors(pilot).to_crs(METRIC_CRS).set_index("name").loc[[sector]]
    pk = gpd.sjoin(pk, sec.reset_index()[["name", "geometry"]].rename(columns={"name": "sector"}), predicate="intersects").drop_duplicates("osm_id")
    pk = pk.assign(area_m2=pk.area)
    return pk.sort_values("area_m2", ascending=False).reset_index(drop=True)


def sites(pilot: str, sector: str, method: str | None = None, share_mode: str = "tiers",
          overrides: dict | None = None, excluded: list | None = None) -> list[dict]:
    """Composting sites for the public parks in a sector."""
    ref = reference()
    method = method or ref["default_method"]
    if method not in ref["methods"]:
        raise ValueError(f"method must be one of {list(ref['methods'])}")
    density = ref["methods"][method]["kg_per_day_per_m2"]
    cap, cap_rule = site_cap_kg()
    lo, hi = ref["share"]["min_pct"], ref["share"]["max_pct"]
    overrides, excluded = overrides or {}, set(excluded or [])
    pk = _parks_in_sector(pilot, sector)
    rep = gpd.GeoSeries(pk.geometry.representative_point(), crs=METRIC_CRS).to_crs(4326)
    shapes = gpd.GeoSeries(pk.geometry, crs=METRIC_CRS).to_crs(4326)
    out = []
    for i, r in pk.iterrows():
        pid = r["osm_id"]
        auto = tier_pct(r["area_m2"]) if share_mode == "tiers" else float(ref["share"]["average_pct"])
        pct = float(overrides.get(pid, auto))
        pct = min(hi, max(lo, pct))
        site_m2 = r["area_m2"] * pct / 100
        raw = site_m2 * density
        usable = site_m2 >= ref["min_site_m2"]["value"] and pid not in excluded
        out.append({
            "id": pid, "name": r["name"] if isinstance(r["name"], str) else None,
            "label": r["name"] if isinstance(r["name"], str) else f"Park {pid.split('/')[-1]}",
            "area_m2": round(r["area_m2"]), "share_pct": round(pct, 2), "share_auto_pct": auto,
            "site_m2": round(site_m2, 1), "capacity_kg": round(min(raw, cap), 1) if usable else 0.0,
            "capped": usable and raw > cap, "cap_rule": cap_rule, "usable": usable,
            "excluded": pid in excluded, "method": method,
            "lon": float(rep.iloc[i].x), "lat": float(rep.iloc[i].y), "geometry": mapping(shapes.iloc[i]),
        })
    return out


def allocate(pts: list[dict], site_list: list[dict], site_nodes: dict, reach, radius_m: float,
             dropoff_pct: float) -> dict:
    """Allocate points' wet waste to park sites.

    `reach(node, cutoff)` returns road distances from a node. Candidates within the radius are taken
    residential first, then by distance, until each site is full; a point can be split between a site
    and the transfer station. Sets on each point: park_id, park_node, park_wet_kg (allocated),
    dropoff_kg (carried by residents) and park_vehicle_kg (carried by the collection vehicle).
    Returns {site id: {"allocated_kg", "dropoff_kg", "points"}}."""
    left = {s["id"]: s["capacity_kg"] for s in site_list if s["usable"] and s["capacity_kg"] > 0}
    cands = []
    for s in site_list:
        if s["id"] not in left:
            continue
        dist = reach(site_nodes[s["id"]], radius_m)
        for i, p in enumerate(pts):
            if p.get("is_bwg") or p["load"].get("wet", 0) <= 0:
                continue
            d = dist.get(p["node"])
            if d is not None and d <= radius_m:
                cands.append((p["use"] != "residential", d, i, s["id"]))
    cands.sort()
    stats = defaultdict(lambda: {"allocated_kg": 0.0, "dropoff_kg": 0.0, "points": 0})
    for _, d, i, sid in cands:
        p = pts[i]
        if p.get("park_id") or left[sid] <= 0:
            continue
        take = min(p["load"]["wet"], left[sid])
        if take <= 0.01:
            continue
        left[sid] -= take
        drop = take * dropoff_pct / 100
        p.update({"park_id": sid, "park_node": site_nodes[sid], "park_dist_m": round(d),
                  "park_wet_kg": take, "dropoff_kg": drop, "park_vehicle_kg": take - drop})
        stats[sid]["allocated_kg"] += take
        stats[sid]["dropoff_kg"] += drop
        stats[sid]["points"] += 1
    return dict(stats)
