"""Regulations library.

Every rule-driven term, threshold and flow in the platform is read from the
documents registered in backend/regulations/library/index.json, so that the
software speaks the language of the notified rules and cites them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LIBRARY_DIR = Path(__file__).resolve().parent / "library"


@lru_cache(maxsize=1)
def index() -> dict:
    return json.loads((LIBRARY_DIR / "index.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=16)
def load(doc_id: str) -> dict:
    for doc in index()["documents"]:
        if doc["id"] == doc_id:
            return json.loads((LIBRARY_DIR / doc["file"]).read_text(encoding="utf-8"))
    raise KeyError(f"Unknown regulatory document '{doc_id}'")


def swm() -> dict:
    """The Solid Waste Management Rules, 2026."""
    return load("swm_rules_2026")


def cite(rule: str, doc_id: str = "swm_rules_2026") -> str:
    """Human-readable citation, e.g. 'SWM Rules 2026, r. 3(1)(i)'."""
    short = {"swm_rules_2026": "SWM Rules 2026"}.get(doc_id, doc_id)
    return f"{short}, r. {rule}" if not rule.lower().startswith("schedule") else f"{short}, {rule}"


def streams() -> list[dict]:
    return swm()["waste_streams"]["streams"]


def stream_keys() -> tuple[str, ...]:
    return tuple(s["key"] for s in streams())


def bwg_criteria() -> list[dict]:
    return swm()["generator_types"]["bulk_waste_generator"]["criteria_any_one"]
