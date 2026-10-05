"""Collection cycle: schedules, sector overrides, built-up days, rule checks, and plans that follow it."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import cycle as C
from backend.api.main import app
from backend.config import CACHE_DIR

PLANNER = {"name": "Arun", "role": "planner"}
SECTORS = ["Sector 1", "Sector 4"]
H = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}


@pytest.fixture()
def con():
    c = C.connect()
    yield c
    c.close()


def entry(con, **kw):
    e = {"stream": "dry", "generator": "households", "days": ["tue", "fri"], "start": "07:00", "end": "12:00", **kw}
    return C.add(con, "hsr", SECTORS, e, PLANNER)


def test_only_planners_edit_and_values_are_checked(con):
    with pytest.raises(C.CycleError) as err:
        C.add(con, "hsr", SECTORS, {"stream": "dry", "generator": "households", "days": ["tue"], "start": "07:00", "end": "12:00"},
              {"name": "S", "role": "surveyor"})
    assert err.value.status == 403
    for bad in ({"stream": "plastic"}, {"generator": "aliens"}, {"days": []}, {"days": ["funday"]}, {"start": "7am"},
                {"start": "12:00", "end": "07:00"}, {"method": "teleport"}, {"vehicle_types": ["hovercraft"]}):
        with pytest.raises(C.CycleError):
            entry(con, **bad)


def test_one_active_entry_per_stream_generator_and_sector(con):
    e = entry(con)
    with pytest.raises(C.CycleError) as err:
        entry(con)
    assert err.value.status == 409
    entry(con, sector="Sector 4")  # a sector's own entry is allowed beside the every-sector one
    C.update(con, "hsr", SECTORS, e["id"], {"active": False}, PLANNER)
    entry(con)  # switched off: a new one can take its place
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM collection_schedule")


def test_built_up_days():
    assert C.built_up_days(["tue", "fri"], "tue") == 4  # Sat, Sun, Mon, Tue
    assert C.built_up_days(["tue", "fri"], "fri") == 3
    assert C.built_up_days(["tue", "fri"], "wed") == 0
    assert C.built_up_days(["mon", "tue", "wed", "thu", "fri", "sat"], "mon") == 2  # Sunday's waste too
    assert C.built_up_days(["sat"], "sat") == 7
    assert C.built_up_days(list(C.DAYS), "thu") == 1


def test_a_sectors_own_entry_overrides_the_every_sector_one(con):
    entry(con)
    entry(con, sector="Sector 4", days=["mon", "thu"], start="08:00", end="10:00")
    assert C.day_plan(con, "hsr", "Sector 1", "tue")["factors"]["households"]["dry"] == 4
    p4 = C.day_plan(con, "hsr", "Sector 4", "tue")
    assert p4["factors"]["households"]["dry"] == 0 and p4["window"] is None
    p4 = C.day_plan(con, "hsr", "Sector 4", "thu")
    assert p4["factors"]["households"]["dry"] == 3 and p4["window"] == {"start": "08:00", "end": "10:00", "hours": 2.0}


def test_template_and_rule_checks(con):
    assert C.checks(con, "hsr", SECTORS) == []  # nothing scheduled yet: nothing to check
    entry(con)
    gaps = C.checks(con, "hsr", SECTORS)
    assert gaps and gaps[0]["rule"] == "8(h)(iii)" and {"stream": "wet", "generator": "households"} in gaps[0]["missing"]
    con2 = C.connect()
    for e in C.entries(con2, "hsr"):
        C.update(con2, "hsr", SECTORS, e["id"], {"active": False}, PLANNER)
    C.load_template(con2, "hsr", SECTORS, PLANNER)
    assert C.checks(con2, "hsr", SECTORS) == []
    with pytest.raises(C.CycleError):
        C.load_template(con2, "hsr", SECTORS, PLANNER)
    week = C.week(con2, "hsr", "Sector 1")
    assert week[1]["day"] == "tue" and "dry" in week[1]["streams"] and week[6]["streams"] == ["wet"]
    con2.close()


def test_generator_type_of_route_points():
    assert C.generator_of({"use": "residential"}) == "households"
    assert C.generator_of({"use": "mixed"}) == "commercial"
    assert C.generator_of({"use": "commercial", "is_bwg": True}) == "bulk_generators"
    assert C.generator_of({"use": "gvp", "is_gvp": True}) is None


def test_cycle_api():
    client = TestClient(app)
    assert client.post("/api/pilots/hsr/cycle/template", headers={"X-SWM-Role": "surveyor", "X-SWM-User": "S"}).status_code == 403
    assert len(client.post("/api/pilots/hsr/cycle/template", headers=H).json()["entries"]) == len(C.template())
    d = client.get("/api/pilots/hsr/cycle").json()
    assert d["checks"] == [] and len(d["week"]) == 7
    day = client.get("/api/pilots/hsr/cycle/day?sector=Sector 4&day=tue").json()
    assert day["factors"]["households"]["dry"] == 4 and day["factors"]["households"]["wet"] == 1 and day["window"]["start"] == "06:00"
    assert client.get("/api/cycle/config").json()["rules"][0]["rule"] == "8(h)(iii)"


@pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")
def test_a_plan_for_a_day_carries_only_the_streams_due_with_built_up_waste():
    client = TestClient(app)
    client.post("/api/pilots/hsr/cycle/template", headers=H)
    body = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050], "time_limit_s": 5,
            "primary_fleet": [{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}], "secondary_fleet": []}
    tue = client.post("/api/pilots/hsr/v2/plan", json={**body, "day": "tue"}).json()
    wed = client.post("/api/pilots/hsr/v2/plan", json={**body, "day": "wed"}).json()
    thu = client.post("/api/pilots/hsr/v2/plan", json={**body, "day": "thu"}).json()
    assert tue["cycle"]["day"] == "tue" and "dry" in tue["cycle"]["due"]["households"]
    assert tue["summary"]["kg_by_stream"]["dry"] > 0 and thu["summary"]["kg_by_stream"]["dry"] == 0  # no dry waste on Thursdays
    assert wed["summary"]["kg_by_stream"]["sanitary"] > 0 and tue["summary"]["kg_by_stream"]["sanitary"] == 0
    assert tue["summary"]["shift_min"] == 6 * 60  # 06:00-12:00, the day's collection window
    # Sunday: only commercial and bulk wet waste is due.
    sun = client.post("/api/pilots/hsr/v2/plan", json={**body, "day": "sun"}).json()
    assert sun["summary"]["kg_by_stream"]["dry"] == 0 and sun["cycle"]["due"]["households"] == []
