"""Building survey records: the ground truth that overrides OSM-based guesses.

Stored in SQLite (data/survey.db, git-ignored). One current record per building.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.config import ROOT

DB_PATH = ROOT / "data" / "survey.db"

USES = ("residential", "commercial", "mixed", "institutional", "vacant", "construction")
ONSITE_PROCESSING = ("none", "compost", "biogas", "certificate")  # certificate = EBWGR certificate
SEGREGATION = ("mixed", "partial", "four_stream")
WATER_SOURCES = ("surveyor", "bwssb")
HOME_COMPOST = ("no", "all", "most", "some")
HOME_COMPOST_METHODS = ("pit", "bin", "aerobic", "biogas")

FIELDS = (
    "use", "units", "commercial_units", "floors", "water_lpd", "water_source",
    "onsite_processing", "segregation_observed", "weighed_kg_day", "surveyor", "notes",
    "home_compost", "home_compost_method", "home_compost_kg",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS building_survey (
    pilot TEXT NOT NULL,
    building_id TEXT NOT NULL,
    use TEXT, units INTEGER, commercial_units INTEGER, floors REAL,
    water_lpd REAL, water_source TEXT,
    onsite_processing TEXT, segregation_observed TEXT, weighed_kg_day REAL,
    surveyor TEXT, notes TEXT, updated_at TEXT NOT NULL,
    home_compost TEXT, home_compost_method TEXT, home_compost_kg REAL,
    PRIMARY KEY (pilot, building_id)
)
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    # Upgrade databases created before a column existed.
    have = {r[1] for r in con.execute("PRAGMA table_info(building_survey)")}
    for col, kind in (("home_compost", "TEXT"), ("home_compost_method", "TEXT"), ("home_compost_kg", "REAL")):
        if col not in have:
            con.execute(f"ALTER TABLE building_survey ADD COLUMN {col} {kind}")
    return con


def validate(record: dict) -> dict:
    """Return a clean record or raise ValueError."""
    clean = {k: record.get(k) for k in FIELDS}
    for key, allowed in (("use", USES), ("onsite_processing", ONSITE_PROCESSING),
                         ("segregation_observed", SEGREGATION), ("water_source", WATER_SOURCES),
                         ("home_compost", HOME_COMPOST), ("home_compost_method", HOME_COMPOST_METHODS)):
        if clean[key] in ("", None):
            clean[key] = None
        elif clean[key] not in allowed:
            raise ValueError(f"{key} must be one of {allowed}")
    for key in ("units", "commercial_units", "floors", "water_lpd", "weighed_kg_day", "home_compost_kg"):
        v = clean[key]
        if v in ("", None):
            clean[key] = None
            continue
        v = float(v)
        if v < 0:
            raise ValueError(f"{key} cannot be negative")
        clean[key] = int(v) if key in ("units", "commercial_units") else v
    if clean["home_compost"] in (None, "no"):
        clean["home_compost_method"] = None
        clean["home_compost_kg"] = None
    if clean["units"] is not None and clean["commercial_units"] is not None and clean["commercial_units"] > clean["units"]:
        raise ValueError("commercial_units cannot exceed units")
    return clean


def all_records(pilot: str, db_path: Path | None = None) -> dict[str, dict]:
    with _connect(db_path) as con:
        rows = con.execute("SELECT * FROM building_survey WHERE pilot = ?", (pilot,)).fetchall()
    return {r["building_id"]: {k: r[k] for k in (*FIELDS, "updated_at")} for r in rows}


def get(pilot: str, building_id: str, db_path: Path | None = None) -> dict | None:
    return all_records(pilot, db_path).get(building_id)


def save(pilot: str, building_id: str, record: dict, db_path: Path | None = None) -> dict:
    clean = validate(record)
    clean["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = ", ".join(("pilot", "building_id", *clean))
    marks = ", ".join("?" for _ in range(len(clean) + 2))
    with _connect(db_path) as con:
        con.execute(f"INSERT OR REPLACE INTO building_survey ({cols}) VALUES ({marks})", (pilot, building_id, *clean.values()))
    return clean


def delete(pilot: str, building_id: str, db_path: Path | None = None) -> None:
    with _connect(db_path) as con:
        con.execute("DELETE FROM building_survey WHERE pilot = ? AND building_id = ?", (pilot, building_id))
