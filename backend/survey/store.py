"""Bridge for the old survey form until the Round 1 surveyor screens replace it (Phase 5).

The old form sends one flat record (use, units, floors, ...). save() turns it into a quick visit
and appends field values and use-mix rows in the Round 1 model; nothing is overwritten. The old
"clear survey" action cannot be supported because survey history is never deleted.
"""

from __future__ import annotations

from pathlib import Path

from backend.survey import db

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


def save(pilot: str, building_id: str, record: dict, actor: dict | None = None, osm: dict | None = None,
         db_path: Path | None = None) -> dict:
    """Record an old-form survey as a completed quick visit. Returns the clean record."""
    from backend.buildings.generators import NORMS
    from backend.survey import migrate

    clean = validate(record)
    actor = {"name": (actor or {}).get("name") or clean["surveyor"], "role": (actor or {}).get("role")}
    osm = osm or {}
    con = db.connect(db_path)
    try:
        with con:
            at, vid = db.now(), db.new_id()
            con.execute("INSERT INTO visit (id, pilot, building_id, round, purpose, user_name, user_role, started_at, ended_at,"
                        " outcome, notes) VALUES (?, ?, ?, 'quick', 'survey', ?, ?, ?, ?, 'completed', ?)",
                        (vid, pilot, building_id, actor["name"], actor["role"], at, at, clean["notes"]))
            bu = migrate.map_building_use(clean["use"], clean["units"], osm.get("category"),
                                          NORMS["surveyed_use"]["apartment_if_units_over"])
            note = "Recorded with the older survey form."
            if bu:
                db.record_value(con, "building", building_id, "building_use", bu, "surveyed", vid, actor, note, at)
            if clean["units"] is not None:
                # New unit counts replace the building's active use mix.
                con.execute("UPDATE use_mix SET status = 'removed', removed_visit_id = ? WHERE pilot = ? AND building_id = ?"
                            " AND status = 'active'", (vid, pilot, building_id))
                for use, n in migrate.use_mix_rows(bu, clean["units"], clean["commercial_units"], osm.get("sub_use")):
                    mid = db.new_id()
                    con.execute("INSERT INTO use_mix (id, pilot, building_id, use, created_visit_id) VALUES (?, ?, ?, ?, ?)",
                                (mid, pilot, building_id, use, vid))
                    db.record_value(con, "use_mix", mid, "count", n, "surveyed", vid, actor, note, at)
            for field in ("floors", "water_lpd", "water_source", "onsite_processing", "segregation_observed",
                          "home_compost", "home_compost_method", "home_compost_kg"):
                if clean[field] is not None:
                    db.record_value(con, "building", building_id, field, clean[field], "surveyed", vid, actor, note, at)
            if clean["weighed_kg_day"] is not None:
                db.record_value(con, "building", building_id, "weighed_kg_day", clean["weighed_kg_day"], "weighed", vid, actor, note, at)
    finally:
        con.close()
    return clean
