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

# Garbage vulnerable points (SWM Rules 2026, r. 15(1): geo-mapped and assessed for accumulation).
# GVP lifecycle: reported -> verified -> assigned -> cleaning -> cleared -> monitoring, or recurred when
# waste is reported again at a cleared or monitored GVP. "rejected": not a GVP (false or duplicate report).
GVP_STATUSES = ("reported", "verified", "assigned", "cleaning", "cleared", "monitoring", "recurred", "rejected")
GVP_SEVERITIES = ("low", "medium", "high", "critical")  # lowest to highest priority
GVP_REPORT_SOURCES = ("surveyor", "worker", "supervisor", "citizen", "other")  # who reported it
GVP_FREQUENCIES = ("daily", "few_times_a_week", "weekly", "occasionally")  # how often waste builds up again
GVP_SOURCES = ("households", "shops", "street_vendors", "market", "construction", "passers_by", "unknown")  # likely dumpers
GVP_INTERVENTIONS = ("bin_placed", "signage", "cctv", "beautification", "awareness_drive", "notice_issued", "other")

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
    respondent TEXT CHECK (respondent IS NULL OR respondent IN {RESPONDENTS}),  -- optional; not asked in Round 1
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

-- Sectors a supervisor assigns to a surveyor (by name, as login is a dummy). Ending sets ended_at.
CREATE TABLE IF NOT EXISTS sector_assignment (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    surveyor_name TEXT NOT NULL,
    sector TEXT NOT NULL,
    assigned_by TEXT,
    assigned_at TEXT NOT NULL,
    ended_by TEXT,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS gvp (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    sector TEXT,                              -- the pilot's ward-level unit
    lon REAL NOT NULL, lat REAL NOT NULL,     -- on the street, not on a property
    seg_id TEXT,                              -- street segment ID (street graph), the unit of responsibility
    road_u INTEGER, road_v INTEGER, road_name TEXT,
    snap_m REAL,                              -- how far the first pin was moved onto the street
    landmark TEXT,
    status TEXT NOT NULL DEFAULT 'reported' CHECK (status IN {GVP_STATUSES}),
    severity TEXT NOT NULL CHECK (severity IN {GVP_SEVERITIES}),
    assigned_to TEXT,                         -- cleaning team or person, while assigned or cleaning
    verified_at TEXT,
    created_by TEXT, created_role TEXT,
    created_source TEXT NOT NULL CHECK (created_source IN {GVP_REPORT_SOURCES}),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gvp_observation (    -- one report: the first, or a later one at the same place
    id TEXT PRIMARY KEY,
    gvp_id TEXT NOT NULL REFERENCES gvp(id),
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    source TEXT NOT NULL CHECK (source IN {GVP_REPORT_SOURCES}),
    lon REAL, lat REAL,                       -- where the reporter placed the pin
    streams TEXT NOT NULL,                    -- JSON list of waste streams seen
    quantity_kg REAL NOT NULL,                -- approximate amount
    frequency TEXT NOT NULL CHECK (frequency IN {GVP_FREQUENCIES}),
    sources TEXT NOT NULL,                    -- JSON list of likely dumpers
    severity TEXT NOT NULL CHECK (severity IN {GVP_SEVERITIES}),  -- as the reporter saw it
    note TEXT
);
CREATE TABLE IF NOT EXISTS gvp_photo (
    id TEXT PRIMARY KEY,
    gvp_id TEXT NOT NULL REFERENCES gvp(id),
    observation_id TEXT NOT NULL REFERENCES gvp_observation(id),
    file_path TEXT NOT NULL, sha256 TEXT NOT NULL,
    taken_at TEXT NOT NULL, lon REAL, lat REAL,
    user_name TEXT
);
CREATE TABLE IF NOT EXISTS gvp_event (
    id TEXT PRIMARY KEY,
    gvp_id TEXT NOT NULL REFERENCES gvp(id),
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    kind TEXT NOT NULL,                       -- 'status', 'severity', or an intervention
    value TEXT,                               -- new status or severity
    note TEXT
);
CREATE TABLE IF NOT EXISTS gvp_demand (         -- waste cleared from a GVP and waiting for a collection vehicle
    id TEXT PRIMARY KEY,
    gvp_id TEXT NOT NULL REFERENCES gvp(id),
    created_at TEXT NOT NULL, created_by TEXT,
    kg REAL NOT NULL,
    streams TEXT NOT NULL,                    -- JSON list
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'collected')),
    collected_at TEXT, collected_by TEXT
);
CREATE TRIGGER IF NOT EXISTS gvp_observation_no_update BEFORE UPDATE ON gvp_observation
BEGIN SELECT RAISE(ABORT, 'gvp_observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS gvp_observation_no_delete BEFORE DELETE ON gvp_observation
BEGIN SELECT RAISE(ABORT, 'gvp_observation is append-only'); END;
CREATE TRIGGER IF NOT EXISTS gvp_photo_no_delete BEFORE DELETE ON gvp_photo
BEGIN SELECT RAISE(ABORT, 'gvp_photo rows are kept (the file may be purged)'); END;
CREATE TRIGGER IF NOT EXISTS gvp_event_no_update BEFORE UPDATE ON gvp_event
BEGIN SELECT RAISE(ABORT, 'gvp_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS gvp_event_no_delete BEFORE DELETE ON gvp_event
BEGIN SELECT RAISE(ABORT, 'gvp_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS gvp_no_delete BEFORE DELETE ON gvp
BEGIN SELECT RAISE(ABORT, 'GVPs are never deleted: reject or monitor them'); END;
CREATE TRIGGER IF NOT EXISTS gvp_demand_no_delete BEFORE DELETE ON gvp_demand
BEGIN SELECT RAISE(ABORT, 'collection demands are never deleted'); END;

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
    _replace_first_gvp_tables(con)
    con.executescript(_SCHEMA)
    return con


def _replace_first_gvp_tables(con: sqlite3.Connection) -> None:
    """The first GVP tables (before the lifecycle) were never deployed. Replace them while empty."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(gvp)")}
    if not cols or "severity" in cols:
        return
    if con.execute("SELECT COUNT(*) FROM gvp").fetchone()[0]:
        raise RuntimeError("data/survey.db has GVPs in the old format; migrate them before upgrading.")
    con.executescript("DROP TABLE IF EXISTS gvp_event; DROP TABLE IF EXISTS gvp_observation; DROP TABLE IF EXISTS gvp;")


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
