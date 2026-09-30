"""The regulations library loads, and the code reads its values rather than hard-coding them."""

from backend import regulations as regs
from backend.config import WASTE_STREAMS


def test_library_registry_lists_swm_rules():
    ids = [d["id"] for d in regs.index()["documents"]]
    assert "swm_rules_2026" in ids


def test_four_streams_in_rule_order():
    assert regs.stream_keys() == ("wet", "dry", "sanitary", "special")
    assert WASTE_STREAMS == regs.stream_keys()


def test_bwg_criteria_match_rule_3_1_i():
    crit = {c["key"]: c["value"] for c in regs.bwg_criteria()}
    assert crit == {"floor_area_m2": 20000, "water_litres_per_day": 40000, "waste_kg_per_day": 100}


def test_dry_fractions_are_not_streams():
    fractions = [f["key"] for f in regs.swm()["waste_streams"]["dry_waste_fractions"]["fractions"]]
    assert {"paper", "plastic", "metal", "glass", "wood", "rubber"} <= set(fractions)
    assert not set(fractions) & set(regs.stream_keys())


def test_every_stream_has_rule_and_definition():
    for s in regs.streams():
        assert s["rule"] and s["definition"] and s["official_term"]


def test_cite_format():
    assert regs.cite("3(1)(i)") == "SWM Rules 2026, r. 3(1)(i)"
    assert regs.cite("Schedule I") == "SWM Rules 2026, Schedule I"


def test_superseded_documents_point_to_successors():
    docs = {d["id"]: d for d in regs.index()["documents"]}
    assert docs["batteries_rules_2001"]["superseded_by"] == "battery_waste_rules_2022"
    assert docs["ewaste_guidelines_2016"]["superseded_by"] == "ewaste_rules_2022"
    for d in docs.values():
        assert regs.load(d["id"])["id"] == d["id"]


def test_ewaste_bulk_consumer_threshold():
    defs = {x["term"]: x for x in regs.load("ewaste_rules_2022")["definitions"]}
    assert defs["bulk consumer"]["threshold_units_per_year"] == 1000
