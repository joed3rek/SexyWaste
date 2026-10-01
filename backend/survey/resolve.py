"""The one resolver for current field values. Use it everywhere a survey value is read.

A field's current value is the row with the strongest source (weighed > verified > surveyed >
assumed); within a source, the most recently recorded row wins (insertion order breaks ties).
Older and weaker rows stay in the history.
"""

from __future__ import annotations

import json
import sqlite3

from backend.survey.db import SOURCES

RANK = {s: i for i, s in enumerate(SOURCES)}  # higher is stronger


def pick(rows: list[dict]) -> dict | None:
    """The winning row among candidate rows for one field (dicts with source, recorded_at, seq)."""
    if not rows:
        return None
    return max(rows, key=lambda r: (RANK[r["source"]], r["recorded_at"], r.get("seq", 0)))


def resolve(con: sqlite3.Connection, entity_type: str, entity_ids: list[str] | None = None,
            fields: list[str] | None = None) -> dict[str, dict[str, dict]]:
    """Current values: {entity_id: {field: {"value", "source", "recorded_at", "recorded_by", "visit_id", "note"}}}.
    Pass entity_ids to limit the lookup."""
    sql = "SELECT f.rowid AS seq, f.* FROM field_value f WHERE f.entity_type = ?"
    args: list = [entity_type]
    if entity_ids is not None:
        if not entity_ids:
            return {}
        sql += f" AND f.entity_id IN ({','.join('?' * len(entity_ids))})"
        args += list(entity_ids)
    if fields:
        sql += f" AND f.field IN ({','.join('?' * len(fields))})"
        args += list(fields)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in con.execute(sql, args):
        grouped.setdefault((r["entity_id"], r["field"]), []).append(dict(r))
    out: dict[str, dict[str, dict]] = {}
    for (eid, field), rows in grouped.items():
        w = pick(rows)
        out.setdefault(eid, {})[field] = {
            "value": json.loads(w["value"]), "source": w["source"], "recorded_at": w["recorded_at"],
            "recorded_by": w["recorded_by"], "visit_id": w["visit_id"], "note": w["note"],
        }
    return out


def history(con: sqlite3.Connection, entity_type: str, entity_id: str) -> list[dict]:
    """Every value ever recorded for an entity, oldest first."""
    rows = con.execute(
        "SELECT f.*, v.purpose FROM field_value f LEFT JOIN visit v ON v.id = f.visit_id"
        " WHERE f.entity_type = ? AND f.entity_id = ? ORDER BY f.recorded_at, f.rowid", (entity_type, entity_id))
    return [{**dict(r), "value": json.loads(r["value"])} for r in rows]
