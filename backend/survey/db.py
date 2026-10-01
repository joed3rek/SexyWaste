"""Survey database (data/survey.db): visits, use mix and an append-only log of field values.

Every value carries a source: assumed, surveyed, verified or weighed. A newer or stronger value
is added beside the old one, never written over it: field_value has no UPDATE or DELETE anywhere,
and triggers reject both. The current value of a field is worked out by resolve.py.

Round 1 (quick) uses entity types 'building' and 'use_mix'. Later rounds add entity types (for
example 'unit') and fields as new rows, without changing these tables.

Login is a dummy for now, so a visit or value records the name and role the person gave
(user_name, user_role), not a verified user id.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import ROOT

DB_PATH = ROOT / "data" / "survey.db"

ROUNDS = ("quick", "detailed", "weighed")
PURPOSES = ("survey", "spot_check")
OUTCOMES = ("completed", "partial", "refused", "locked", "revisit_requested", "demolished", "not_a_building",
            "footprint_issue")
ENDS_VISIT = ("refused", "locked", "revisit_requested", "demolished", "not_a_building")  # nothing more is recorded
RESPONDENTS = ("owner", "tenant", "security_or_caretaker", "neighbour", "observed_only")
SOURCES = ("assumed", "surveyed", "verified", "weighed")  # weakest to strongest
ENTITY_TYPES = ("building", "use_mix")  # extend in later rounds, e.g. "unit"
USES = ("residential_dwelling", "pg_coliving", "hostel", "hotel_guesthouse", "shop_retail", "food_service", "office",
        "clinic_healthcare", "educational", "religious", "market_stall", "workshop_industrial", "institutional",
        "storage_parking", "vacant", "under_construction")
FLAG_KINDS = ("merged_with_neighbour", "should_be_split", "missing_from_map", "wrong_shape", "not_a_building")

# Round 1 building-level fields and their allowed values (None: free value of the stated type).
BUILDING_FIELDS = {
    "building_use": None,  # values come from building_uses.json
    "floors": "number",
    "society_name": "text",
    "collection_arrangement": ("municipal_door_to_door", "society_or_private_contractor",
                               "informal_picker_or_scrap_dealer", "self_disposal", "none", "unknown"),
    "segregation_reported": ("four_stream", "wet_dry_only", "mixed", "unknown"),
    # In-building composting is kept in Round 1 by decision of the project lead (it reduces wet waste collected).
    "home_compost": ("no", "all", "most", "some"),
    "home_compost_method": ("pit", "bin", "aerobic", "biogas"),
    "home_compost_kg": "number",
}
USE_MIX_FIELDS = {"count": "number", "occupants_total": "number", "beds_total": "number"}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS visit (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    building_id TEXT,                        -- NULL for a building missing from the map
    round TEXT NOT NULL CHECK (round IN {ROUNDS}),
    purpose TEXT NOT NULL CHECK (purpose IN {PURPOSES}),
    spot_check_of TEXT REFERENCES visit(id), -- the visit a spot-check re-examines
    user_name TEXT,
    user_role TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT CHECK (outcome IS NULL OR outcome IN {OUTCOMES}),
    respondent TEXT CHECK (respondent IS NULL OR respondent IN {RESPONDENTS}),
    gps_lat REAL, gps_lon REAL, gps_accuracy_m REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS visit_building ON visit (pilot, building_id);

CREATE TABLE IF NOT EXISTS use_mix (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    building_id TEXT NOT NULL,
    use TEXT NOT NULL CHECK (use IN {USES}),
    created_visit_id TEXT NOT NULL REFERENCES visit(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    removed_visit_id TEXT REFERENCES visit(id)
);
CREATE INDEX IF NOT EXISTS use_mix_building ON use_mix (pilot, building_id);

CREATE TABLE IF NOT EXISTS field_value (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,               -- validated in code (ENTITY_TYPES) so later rounds can add types
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,                     -- JSON
    source TEXT NOT NULL CHECK (source IN {SOURCES}),
    visit_id TEXT REFERENCES visit(id),
    recorded_by TEXT,
    recorded_role TEXT,
    recorded_at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS field_value_entity ON field_value (entity_type, entity_id, field);

CREATE TABLE IF NOT EXISTS geometry_flag (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    building_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN {FLAG_KINDS}),
    lat REAL, lon REAL,
    related_building_id TEXT,
    visit_id TEXT REFERENCES visit(id),
    note TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_by TEXT, created_at TEXT NOT NULL,
    resolved_by TEXT, resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS photo (
    id TEXT PRIMARY KEY,
    visit_id TEXT NOT NULL REFERENCES visit(id),
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    lat REAL, lon REAL
);

-- Things a supervisor reviews: use-mix contradictions, GPS far from the footprint, spot-check mismatches.
CREATE TABLE IF NOT EXISTS review_item (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    building_id TEXT,
    visit_id TEXT REFERENCES visit(id),
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,                    -- JSON
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    created_at TEXT NOT NULL,
    resolved_by TEXT, resolved_at TEXT
);

CREATE TRIGGER IF NOT EXISTS field_value_no_update BEFORE UPDATE ON field_value
BEGIN SELECT RAISE(ABORT, 'field_value is append-only: add a new row instead'); END;
CREATE TRIGGER IF NOT EXISTS field_value_no_delete BEFORE DELETE ON field_value
BEGIN SELECT RAISE(ABORT, 'field_value is append-only: rows are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS use_mix_no_delete BEFORE DELETE ON use_mix
BEGIN SELECT RAISE(ABORT, 'use_mix rows are never deleted: set status to removed'); END;
CREATE TRIGGER IF NOT EXISTS use_mix_only_status BEFORE UPDATE ON use_mix
WHEN NEW.id IS NOT OLD.id OR NEW.pilot IS NOT OLD.pilot OR NEW.building_id IS NOT OLD.building_id
  OR NEW.use IS NOT OLD.use OR NEW.created_visit_id IS NOT OLD.created_visit_id
BEGIN SELECT RAISE(ABORT, 'only the status of a use_mix row can change'); END;
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)
    return con


def record_value(con: sqlite3.Connection, entity_type: str, entity_id: str, field: str, value, source: str,
                 visit_id: str | None, actor: dict | None, note: str | None = None, recorded_at: str | None = None) -> str:
    """The only way a field value is written. Appends a row; never changes an existing one."""
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {ENTITY_TYPES}")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    vid = new_id()
    actor = actor or {}
    con.execute(
        "INSERT INTO field_value (id, entity_type, entity_id, field, value, source, visit_id, recorded_by, recorded_role,"
        " recorded_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (vid, entity_type, entity_id, field, json.dumps(value, ensure_ascii=False), source, visit_id,
         actor.get("name"), actor.get("role"), recorded_at or now(), note))
    return vid
