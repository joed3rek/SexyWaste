"""Building use configuration (building_uses.json) and use-mix contradiction checks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend import regulations as regs

CONFIG_FILE = Path(__file__).resolve().parent / "building_uses.json"


@lru_cache(maxsize=1)
def config() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def building_use(key: str) -> dict:
    try:
        return config()["building_uses"][key]
    except KeyError:
        raise KeyError(f"Unknown building use '{key}'") from None


def collection(key: str) -> dict:
    """Collection mode for a building use, with its rule citation."""
    mode = building_use(key)["collection_mode"]
    m = config()["collection_modes"][mode]
    return {"mode": mode, "label": m["label"], "rule": regs.cite(m["rule"]) if m["rule"] else None}


def bwg_entity(key: str) -> dict | None:
    """The BWG entity group for a building use, from the regulations library, or None."""
    e = building_use(key)["bwg_entity"]
    if not e:
        return None
    bwg = regs.swm()["generator_types"]["bulk_waste_generator"]
    if e["entity"] not in bwg["entity_groups"][e["group"]]:
        raise ValueError(f"BWG entity '{e['entity']}' is not in the regulations library")
    return {"group": e["group"], "entity": e["entity"], "rule": regs.cite(bwg["rule"])}


def contradictions(key: str, rows: list[dict]) -> list[dict]:
    """Warnings when active use-mix rows contradict the building use.

    `rows` are active rows: {"use": ..., "count": n, "beds_total": n, "occupants_total": n}.
    Each warning has a `code` and its details, for the screens to word in the user's language,
    and an English `message` for logs. The surveyor is warned and can still save; the warnings
    are logged for the supervisor."""
    bu = building_use(key)
    checks = bu.get("checks", {})
    labels = {k: v["label"] for k, v in config()["uses"].items()}
    out = []
    if not rows:
        return out
    present = {r["use"] for r in rows}
    expects = checks.get("expects_any")
    if expects and not present & set(expects):
        out.append({"code": "expects_any", "building_use": key, "uses": list(expects),
                    "message": f"{bu['label']} with no {' or '.join(labels[u].lower() for u in expects)} recorded."})
    for rule in checks.get("warn_over", []):
        total = sum(float(r.get(rule["field"]) or 0) for r in rows if r["use"] == rule["use"])
        if total > rule["max"]:
            field = {"beds_total": "beds", "count": "units", "occupants_total": "occupants"}[rule["field"]]
            out.append({"code": "warn_over", "building_use": key, "use": rule["use"], "field": rule["field"],
                        "total": total, "max": rule["max"],
                        "message": f"{bu['label']} with {total:g} {field} of {labels[rule['use']].lower()} (more than {rule['max']})."})
    if checks.get("no_occupied_uses"):
        occupied = present & set(config()["occupied_uses"])
        if occupied:
            out.append({"code": "no_occupied_uses", "building_use": key, "uses": sorted(occupied),
                        "message": f"{bu['label']} with occupied uses recorded: {', '.join(labels[u].lower() for u in sorted(occupied))}."})
    return out
