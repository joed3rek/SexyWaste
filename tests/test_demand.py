"""Service planning: requirements read from the cycle, streets, bins and GVPs; the stored service
demands they produce for a day; GVP and bin events; the route builder reading collection demands."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import cleancity as CC
from backend import cycle as C
from backend import demand as D
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db, gvp

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
SUPERVISOR = {"name": "Ravi", "role": "survey_supervisor", "sectors": ["Sector 4"]}
SURVEYOR = {"name": "Asha", "role": "surveyor", "sectors": ["Sector 4"]}
COLLECTOR = {"name": "Lakshmi", "role": "collector"}
TUE, SUN = "2026-10-06", "2026-10-11"


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")  # no GVPs from the real survey
    c = D.connect()
    C.load_template(c, "hsr", [], PLANNER)
    yield c
    c.close()


def collection(con, day, sector="Sector 4", source="collection_point"):
    return [d for d in D.demands(con, "hsr", day, sector, "collection") if d["source_type"] == source]


def test_cleanings_are_spread_over_the_week():
    assert [D.cleanings_on(7, i) for i in range(7)] == [1] * 7
    assert [D.cleanings_on(14, i) for i in range(7)] == [2] * 7
    assert [D.cleanings_on(3, i) for i in range(7)] == [0, 0, 1, 0, 1, 0, 1]  # Wed, Fri, Sun
    assert all(sum(D.cleanings_on(n, i, o) for i in range(7)) == n for n in (1, 2, 3, 5, 6, 10, 21) for o in range(7))
    assert sum(D.cleanings_on(3.5, i) for i in range(14)) == 7  # a half carries over: 3 one week, 4 the next
    assert D.day_number("2024-01-01") == 0 and D.weekday("2024-01-01") == "mon"


def test_a_weeks_sweeping_matches_the_workload_and_is_level(con):
    km = [sum(d["length_m"] for d in D.build(con, "hsr", "Sector 4", day) if d["source_type"] == "street") / 1000
          for day in [f"2026-10-{n:02d}" for n in range(5, 19)]]  # two weeks
    daily = CC.workload(con, "hsr", "Sector 4")["parts"]["street_sweeping"] * CC.standards()["productivity"]["street_m_per_worker_hour"] / 1000
    assert sum(km) / 14 == pytest.approx(daily, rel=0.03)
    assert max(km) < 1.6 * min(km)  # staggered, not all on the same days


def test_dates_and_weekdays():
    assert D.weekday(TUE) == "tue" and D.weekday(SUN) == "sun"
    assert D.next_date_for("tue", "2026-10-05") == TUE and D.next_date_for("mon", "2026-10-05") == "2026-10-05"
    with pytest.raises(D.DemandError):
        D.parse_date("6 Oct")


def test_requirements_come_from_where_they_are_kept(con):
    reqs = D.requirements(con, "hsr", ["Sector 4"])
    kinds = {(r["kind"], r["source_type"]) for r in reqs}
    assert ("collection", "schedule") in kinds and ("cleaning", "street") in kinds and ("cleaning", "gvp") in kinds
    wet = next(r for r in reqs if r["source_type"] == "schedule" and r["stream"] == "wet" and r["generator"] == "households")
    assert wet["window"] == {"start": "06:00", "end": "11:00"} and "mon" in wet["frequency"]["days"]
    assert len([r for r in reqs if r["source_type"] == "street"]) == len(CC.streets(con, "hsr", "Sector 4"))


def test_a_days_demands_follow_the_cycle_and_the_streets(con):
    r = D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    assert r["created"] > 0 and r["closed"] == 0
    tue = collection(con, TUE)
    assert {d["stream"] for d in tue} >= {"wet", "dry"}
    assert all(d["window_start"] and d["quantity_kg"] > 0 and d["status"] == "open" for d in tue)
    D.generate(con, "hsr", SUN, ["Sector 4"], PLANNER)
    assert {d["stream"] for d in collection(con, SUN)} == {"wet"}  # Sunday: wet waste from commercial and bulk generators only
    assert {d["generator"] for d in collection(con, SUN)} <= {"commercial", "bulk_generators"}
    sweeping = [d for d in D.demands(con, "hsr", TUE, "Sector 4", "cleaning") if d["source_type"] == "street"]
    due = [s for s in CC.streets(con, "hsr", "Sector 4")
           if D.cleanings_on(s["cleanings_per_week"], D.day_number(TUE), D.street_offset(s["seg_id"]))]
    assert len(sweeping) == len(due) and all(d["length_m"] > 0 for d in sweeping)


def test_generating_again_changes_nothing(con):
    first = D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    n = con.execute("SELECT COUNT(*) FROM service_demand").fetchone()[0]
    again = D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    assert again["created"] == 0 and again["closed"] == 0 and again["kept"] == first["created"]
    assert con.execute("SELECT COUNT(*) FROM service_demand").fetchone()[0] == n


def test_a_schedule_change_closes_demands_no_longer_required(con):
    D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    dry = next(e for e in C.entries(con, "hsr") if e["stream"] == "dry" and e["generator"] == "households")
    C.update(con, "hsr", [], dry["id"], {"days": ["fri"]}, PLANNER)
    r = D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    assert r["closed"] > 0
    gone = D.demands(con, "hsr", TUE, "Sector 4", "collection", ("cancelled",))
    assert gone and all(d["stream"] == "dry" and d["generator"] == "households" for d in gone)
    assert D.get(con, "hsr", gone[0]["id"])["history"][-1]["value"] == "cancelled"


def test_a_bin_needing_service_is_a_collection_demand_until_serviced(con):
    s = next(s for s in CC.streets(con, "hsr", "Sector 4") if s["length_m"] > 60)
    b = CC.add_bin(con, "hsr", *s["coords"][0], {"capacity_l": 240, "stream": "dry", "services_per_day": 0.5}, PLANNER)
    CC.update_bin(con, "hsr", b["id"], {"serviced": True}, COLLECTOR)
    D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    assert not collection(con, TUE, source="bin")  # just serviced, not full
    CC.update_bin(con, "hsr", b["id"], {"status": "full"}, COLLECTOR)
    D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    [d] = collection(con, TUE, source="bin")
    assert d["source_id"] == b["id"] and d["stream"] == "dry" and d["quantity_kg"] > 0
    CC.update_bin(con, "hsr", b["id"], {"serviced": True, "status": "normal"}, COLLECTOR)
    D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    assert not collection(con, TUE, source="bin")
    assert D.get(con, "hsr", d["id"])["status"] == "done"


def test_a_gvp_becomes_a_cleaning_then_a_collection_demand(con):
    from tests.test_gvp import _kerb_points
    where = _kerb_points("Sector 4")[0]
    g = gvp.report("hsr", SURVEYOR, *where, ["wet", "dry"], 60, "daily", ["street_vendors"], severity="high")
    assert not D.demands(con, "hsr", D.today(), "Sector 4")  # reported, not yet verified
    gvp.act("hsr", g["id"], SUPERVISOR, "verify")
    [clean] = [d for d in D.demands(con, "hsr", D.today(), "Sector 4", "cleaning") if d["source_type"] == "gvp"]
    assert clean["source_id"] == g["id"] and clean["quantity_kg"] == 60 and clean["priority"] > 1
    gvp.act("hsr", g["id"], SUPERVISOR, "assign", value="Team A")
    gvp.act("hsr", g["id"], SUPERVISOR, "clear", kg=40)
    assert D.get(con, "hsr", clean["id"])["status"] == "done"
    pickup = [d for d in D.demands(con, "hsr", D.today(), "Sector 4", "collection") if d["source_type"] == "gvp"]
    assert {d["stream"] for d in pickup} == {"wet", "dry"} and sum(d["quantity_kg"] for d in pickup) == pytest.approx(40)
    gvp.act("hsr", g["id"], COLLECTOR, "collected")
    assert all(D.get(con, "hsr", d["id"])["status"] == "done" for d in pickup)


def test_the_route_builder_reads_the_days_collection_demands(con):
    from backend.routing import twotier as T
    cyc = C.day_plan(con, "hsr", "Sector 4", "sun")
    pts = T._sector_points(T.PlanInput(pilot="hsr", sector="Sector 4", depot=(0, 0), mrf=(0, 0), primary_fleet=[],
                                       secondary_fleet=[], cycle=cyc, date=SUN))
    stored = collection(con, SUN)  # planning the day stored its demands first
    assert stored and {p["id"] for p in pts} == {d["source_id"] for d in stored}
    assert sum(p["load_kg"] for p in pts) == pytest.approx(sum(d["quantity_kg"] for d in stored))
    assert all(p["load"]["dry"] == 0 for p in pts)  # only wet waste is due on Sunday


def test_demands_are_never_deleted_and_their_history_is_append_only(con):
    D.generate(con, "hsr", TUE, ["Sector 4"], PLANNER)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM service_demand")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE service_demand_event SET note = 'x'")


def test_service_planning_api(con):
    client = TestClient(app)
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    assert client.post("/api/pilots/hsr/service-demands/generate", json={"date": TUE, "sector": "Sector 4"},
                       headers={"X-SWM-Role": "surveyor", "X-SWM-User": "Asha", "X-SWM-Sectors": "Sector 4"}).status_code == 403
    r = client.post("/api/pilots/hsr/service-demands/generate", json={"date": TUE, "sector": "Sector 4"}, headers=h).json()
    assert r["created"] > 0
    day = client.get(f"/api/pilots/hsr/service-demands?date={TUE}&sector=Sector 4").json()
    assert day["weekday"] == "tue" and day["summary"]["collection"]["demands"] > 0 and day["summary"]["cleaning"]["m"] > 0
    one = client.get(f"/api/pilots/hsr/service-demands/{day['demands'][0]['id']}").json()
    assert one["history"][0]["kind"] == "created"
    reqs = client.get("/api/pilots/hsr/service-requirements?sector=Sector 4&kind=collection").json()["requirements"]
    assert reqs and all(x["kind"] == "collection" for x in reqs)
    assert client.get("/api/pilots/hsr/service-demands?date=tomorrow").status_code == 422
    assert client.get("/api/pilots/hsr/service-demands?sector=Sector 99").status_code == 404
