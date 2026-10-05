"""Project-wide settings: study areas, cache paths and routing assumptions."""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
FRONTEND_DIR = ROOT / "frontend"

# Survey frontage photos (Round 1: at most one optional photo per visit). Stored under data/photos/,
# which is git-ignored. Surveyors are told not to photograph people or the inside of homes.
PHOTO_DIR = ROOT / "data" / "photos"
PHOTO_MAX_BYTES = 3 * 1024 * 1024
# Photos older than this are deleted by the retention job; their database rows keep the hash so the
# record shows a photo existed. A policy choice for the pilot, not a regulatory requirement.
PHOTO_RETENTION_DAYS = 365

# Survey quality settings (not regulation).
GPS_WARN_DISTANCE_M = 50        # warn when the phone's position is this far from the building footprint
GVP_MAX_ROAD_DISTANCE_M = 30    # a garbage vulnerable point is on a road: a pin further than this from any road is refused
GVP_MERGE_DISTANCE_M = 25       # a new report this close to an open GVP is a new observation of it, not a new GVP
GVP_MAX_PHOTOS = 3              # photos per GVP report
# Operating targets, not regulation: hours from verification within which a GVP should be cleared, by severity.
GVP_RESPONSE_HOURS = {"critical": 24, "high": 48, "medium": 72, "low": 168}
SPOT_CHECK_RATE = 0.05          # share of each surveyor's completed quick visits sampled per week
SPOT_CHECK_WINDOW_DAYS = 7


@dataclass(frozen=True)
class Area:
    key: str
    name: str
    lat: float
    lon: float
    radius_m: int  # download radius, or buffer around the pilot boundary when pilot is set
    pilot: str | None = None  # key into PILOTS when the area is a mapped pilot


# Pilot study areas. The road network is downloaded within radius_m of the centre point.
AREAS: dict[str, Area] = {
    a.key: a
    for a in [
        Area("hsr", "HSR Layout Sectors 1-7, Bengaluru (pilot)", 12.9125, 77.6410, 300, pilot="hsr"),
    ]
}


@dataclass(frozen=True)
class Pilot:
    key: str
    name: str
    # OSM relation ids of the sector boundaries that make up the pilot area.
    sector_relations: tuple[str, ...]


PILOTS: dict[str, Pilot] = {
    "hsr": Pilot(
        "hsr",
        "HSR Layout Sectors 1-7, Bengaluru",
        ("R17168009", "R17168008", "R17168007", "R17168006", "R17168005", "R17168004", "R17168003"),
    ),
}

# ASSUMPTION: congested urban speeds in km/h by OSM road class for a loaded
# collection vehicle. Replace with speeds derived from vehicle GPS traces.
SPEEDS_KMPH: dict[str, float] = {
    "motorway": 35,
    "trunk": 25,
    "primary": 20,
    "secondary": 18,
    "tertiary": 15,
    "unclassified": 12,
    "residential": 10,
    "living_street": 6,
    "service": 8,
}
DEFAULT_SPEED_KMPH = 12

# Four source-segregated streams, from the regulations library (SWM Rules 2026, r. 5(1)(b)).
from backend.regulations import stream_keys  # noqa: E402

WASTE_STREAMS = stream_keys()
