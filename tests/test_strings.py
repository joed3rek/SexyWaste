"""Every user-facing string of the built roles comes from frontend/strings.json."""

import json
import re
from pathlib import Path

from backend import auth
from backend.survey import db, uses

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
BUILT_PAGES = ["index.html", "admin.html", "surveyor.html", "supervisor.html", "surveyor.js", "supervisor.js", "survey_flow.js", "ui.js",
               "report.html", "report.js"]
STRINGS = json.loads((FRONTEND / "strings.json").read_text(encoding="utf-8"))["strings"]


def test_every_string_has_english_and_a_kannada_column():
    for key, entry in STRINGS.items():
        assert entry["en"].strip(), key
        assert "kn" in entry and isinstance(entry["kn"], str), key


def test_every_string_id_used_by_the_built_screens_exists():
    used = set()
    for name in BUILT_PAGES:
        text = (FRONTEND / name).read_text(encoding="utf-8")
        used |= set(re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']""", text))
        used |= set(re.findall(r'data-t(?:-placeholder)?="([a-z0-9_.]+)"', text))
    missing = sorted(u for u in used if u not in STRINGS)
    assert not missing, missing


def test_every_value_shown_to_surveyors_has_a_label():
    cfg = uses.config()
    needed = [f"building_use.{k}" for k in cfg["building_uses"]] + [f"use.{k}" for k in cfg["uses"]]
    needed += [f"outcome.{o}" for o in db.OUTCOMES] + [f"flag.{k}" for k in db.FLAG_KINDS] + [f"source.{s}" for s in db.SOURCES]
    for field, values in db.BUILDING_FIELDS.items():
        if isinstance(values, tuple):
            needed += [f"value.{field}.{v}" for v in values]
    needed += [f"field.{f}" for f in db.USE_MIX_FIELDS]
    needed += [f"freq.{k}" for k in db.GVP_FREQUENCIES] + [f"src.{k}" for k in db.GVP_SOURCES]
    needed += [f"int.{k}" for k in db.GVP_INTERVENTIONS] + [f"gvp.status.{k}" for k in db.GVP_STATUSES]
    from backend.survey import gvp
    needed += [f"pub.size.{k}" for k in gvp.size_kg()]
    needed += [f"role.{r['key']}" for r in auth.roles()] + [f"role.{r['key']}.description" for r in auth.roles()]
    missing = [k for k in needed if k not in STRINGS]
    assert not missing, missing


def test_placeholders_are_kept_in_translations():
    for key, entry in STRINGS.items():
        if entry["kn"]:
            assert set(re.findall(r"\{\w+\}", entry["en"])) == set(re.findall(r"\{\w+\}", entry["kn"])), key
