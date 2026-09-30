"""OpenStreetMap access through Overpass, with mirror fallback and local caching."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, TypeVar

import geopandas as gpd
import osmnx as ox

log = logging.getLogger(__name__)
T = TypeVar("T")

# The main Overpass server often refuses connections from this network; mirrors are tried in order.
OVERPASS_ENDPOINTS = [
    "https://maps.mail.ru/osm/tools/overpass/api",
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
]

ox.settings.requests_timeout = 120
ox.settings.overpass_rate_limit = False
ox.settings.use_cache = True


def with_overpass_fallback(fn: Callable[[], T]) -> T:
    """Run an osmnx call, retrying against each Overpass mirror."""
    last_error: Exception | None = None
    for url in OVERPASS_ENDPOINTS:
        ox.settings.overpass_url = url
        try:
            return fn()
        except Exception as err:  # network errors surface as several exception types
            log.warning("Overpass endpoint %s failed: %s", url, err)
            last_error = err
    raise RuntimeError(f"All Overpass endpoints failed: {last_error}")


def cached_layer(path: Path, fetch: Callable[[], gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Return a cached GeoJSON layer, fetching and saving it on first use."""
    if path.exists():
        return gpd.read_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = with_overpass_fallback(fetch)
    gdf.to_file(path, driver="GeoJSON")
    return gpd.read_file(path)
