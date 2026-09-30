"""Project-wide settings: study areas, cache paths and routing assumptions."""

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
FRONTEND_DIR = ROOT / "frontend"


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
        Area("shivajinagar", "Shivajinagar, Bengaluru", 12.9857, 77.6050, 1200),
        Area("jayanagar", "Jayanagar, Bengaluru", 12.9299, 77.5826, 1200),
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
