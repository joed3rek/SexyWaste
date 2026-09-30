"""Downloads and caches the OSM layers for a pilot area: sectors, buildings, land use and POIs."""

from __future__ import annotations

from functools import lru_cache

import geopandas as gpd
import osmnx as ox

from backend.config import CACHE_DIR, PILOTS
from backend.osm import cached_layer

METRIC_CRS = 32643  # UTM zone 43N, covers Bengaluru

BUILDING_COLS = ["building", "building:levels", "addr:housenumber", "addr:housename", "addr:street", "addr:full",
                 "name", "amenity", "shop", "office", "tourism", "healthcare"]
LANDUSE_COLS = ["landuse", "leisure", "amenity", "name"]
POI_COLS = ["amenity", "shop", "office", "tourism", "healthcare", "craft", "name", "addr:housenumber", "addr:street"]


def _tidy(gdf: gpd.GeoDataFrame, cols: list[str]) -> gpd.GeoDataFrame:
    """Keep id, chosen tag columns and geometry, as plain strings."""
    gdf = gdf.reset_index()
    gdf["osm_id"] = gdf["element"].astype(str) + "/" + gdf["id"].astype(str)
    keep = ["osm_id", *[c for c in cols if c in gdf.columns], "geometry"]
    out = gdf[keep].copy()
    for c in keep[1:-1]:
        out[c] = out[c].astype("string")
    return out


def _dir(pilot_key: str):
    return CACHE_DIR / "pilots" / pilot_key


@lru_cache(maxsize=4)
def sectors(pilot_key: str) -> gpd.GeoDataFrame:
    pilot = PILOTS[pilot_key]

    def fetch():
        gdf = ox.geocoder.geocode_to_gdf(list(pilot.sector_relations), by_osmid=True)
        gdf["osm_id"] = ["relation/" + r[1:] for r in pilot.sector_relations]
        return gdf[["osm_id", "name", "geometry"]]

    gdf = cached_layer(_dir(pilot_key) / "sectors.geojson", fetch)
    gdf["sort"] = gdf["name"].str.extract(r"(\d+)").astype(float)
    return gdf.sort_values("sort").drop(columns="sort").reset_index(drop=True)


def boundary(pilot_key: str):
    return sectors(pilot_key).union_all()


@lru_cache(maxsize=4)
def buildings(pilot_key: str) -> gpd.GeoDataFrame:
    poly = boundary(pilot_key)
    gdf = cached_layer(
        _dir(pilot_key) / "buildings.geojson",
        lambda: _tidy(ox.features_from_polygon(poly, tags={"building": True}), BUILDING_COLS),
    )
    return gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)


@lru_cache(maxsize=4)
def landuse(pilot_key: str) -> gpd.GeoDataFrame:
    poly = boundary(pilot_key)
    tags = {"landuse": True, "leisure": ["park", "garden", "playground", "pitch"], "amenity": ["marketplace"]}
    gdf = cached_layer(
        _dir(pilot_key) / "landuse.geojson",
        lambda: _tidy(ox.features_from_polygon(poly, tags=tags), LANDUSE_COLS),
    )
    return gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)


@lru_cache(maxsize=4)
def pois(pilot_key: str) -> gpd.GeoDataFrame:
    poly = boundary(pilot_key)
    tags = {"amenity": True, "shop": True, "office": True, "tourism": True, "healthcare": True, "craft": True}
    return cached_layer(
        _dir(pilot_key) / "pois.geojson",
        lambda: _tidy(ox.features_from_polygon(poly, tags=tags), POI_COLS),
    )
