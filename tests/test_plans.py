"""Route plans: every optimiser run kept with routes and stops, its feasibility under the route
optimiser contract, and adoption (demands planned, vehicles and crews assigned, older plan superseded)."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import cycle as C
from backend import demand as D
from backend import plans as PL
from backend import resources as R
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
H = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
SUN = "2026-10-11"
BODY = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050], "date": SUN, "time_limit_s": 5,
        "primary_fleet": [{"type": "e_loader_3w", "count": 3}], "secondary_fleet": [{"type": "rear_loader_compactor", "count": 1}]}


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")  # no GVPs from the real survey
    c = PL.connect()
    C.load_template(c, "hsr", [], PLANNER)
    yield c
    c.close()


def plan(body=None):
    r = TestClient(app).post("/api/pilots/hsr/v2/plan", json=body or BODY, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def result(uncollected=0.0, over=(), left=0.0, fleet="inventory"):
    return {"summary": {"uncollected_kg": uncollected, "uncollected_points": ["A"] if uncollected else [], "kg_collected": 1000.0,
                        "vehicles_over_shift": list(over), "left_at_stations_kg": left, "shift_min": 300},
            "stations": [], "station_capacity_kg": 1e9, "fleet_basis": fleet, "crew_basis": "hypothetical"}


def test_feasibility_follows_the_contract():
    assert PL.assess(result()) == ("feasible", [])
    f, ex = PL.assess(result(uncollected=200.0))
    assert f == "infeasible" and ex[0]["code"] == "unserved_demand" and ex[0]["share_pct"] == pytest.approx(16.7, 0.1)
    assert ex[0]["actions"] and "cannot be collected" in ex[0]["reason"]
    assert PL.assess(result(over=["E-loader 1"]))[0] == "infeasible"
    assert PL.assess(result(left=50.0))[0] == "infeasible"
    f, ex = PL.assess(result(fleet="hypothetical"))
    assert f == "feasible_with_warnings" and ex[0]["code"] == "hypothetical_fleet"


def test_point_ids_map_to_their_demands():
    assert PL.demand_key("A:R1-2-0:residential#2") == ("collection_point", "A:R1-2-0:residential")
    assert PL.demand_key("GVP:abc") == ("gvp", "abc") and PL.demand_key("BIN:xyz#1") == ("bin", "xyz")


def test_every_run_is_kept_with_routes_and_stops(con):
    r = plan()
    p = PL.get(con, "hsr", r["plan"]["id"])
    assert p["status"] == "draft" and p["service_date"] == SUN and p["feasibility"] == r["plan"]["feasibility"]
    prim = [x for x in p["routes"] if x["tier"] == "primary"]
    assert prim and all(x["stops"] and x["km"] > 0 for x in prim)
    collected = PL.served_points(p)
    routed = {pid.split("#")[0] for v in r["primary"]["vehicles"] for t in v["trips"] for pid in t["point_ids"]}
    assert collected == routed
    assert any(s["kind"] == "unload" for x in prim for s in x["stops"])
    assert any(x["tier"] == "secondary" and x["stops"][-1]["kind"] == "deliver" for x in p["routes"])
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM route_plan")


def test_adopting_plans_the_demands_assigns_resources_and_supersedes(con):
    for i in range(3):
        R.add(con, "vehicle", "hsr", ["Sector 4"], {"type": "e_loader_3w", "registration": f"KA-{i}", "home_sector": "Sector 4"},
              {"name": "M", "role": "fleet_workforce_manager"})
    R.add(con, "vehicle", "hsr", ["Sector 4"], {"type": "rear_loader_compactor", "registration": "KA-T"},
          {"name": "M", "role": "fleet_workforce_manager"})
    for i in range(4):
        R.add(con, "staff", "hsr", ["Sector 4"], {"worker_id": f"D{i}", "name": f"Driver {i}", "role": "driver"}, {"name": "H", "role": "hr_manager"})
    for i in range(5):
        R.add(con, "staff", "hsr", ["Sector 4"], {"worker_id": f"C{i}", "name": f"Collector {i}", "role": "waste_collector"},
              {"name": "H", "role": "hr_manager"})
    first = plan()
    p = PL.adopt(con, "hsr", first["plan"]["id"], PLANNER, accept_exceptions=True)
    assert p["status"] == "adopted" and p["demands_changed"]["planned"] > 0
    served = PL.served_points(p)
    for d in D.demands(con, "hsr", SUN, "Sector 4", "collection", D.STATUSES):
        assert d["status"] == ("planned" if d["source_id"] in served else "open")
    assert all(r["vehicle_id"] for r in p["routes"])  # 3 loaders and a truck exist
    assert all(r["crew"] for r in p["routes"]) and not p["assignment_notes"]
    assert len({m for r in p["routes"] for m in r["crew"]}) == sum(len(r["crew"]) for r in p["routes"])  # nobody on two routes
    by_vehicle = PL.vehicle_routes(con, "hsr", SUN)
    assert len(by_vehicle) == len(p["routes"])
    with pytest.raises(PL.PlanError):
        PL.adopt(con, "hsr", first["plan"]["id"], PLANNER, accept_exceptions=True)  # already adopted
    second = plan()
    PL.adopt(con, "hsr", second["plan"]["id"], PLANNER, accept_exceptions=True)
    assert PL.get(con, "hsr", first["plan"]["id"])["status"] == "superseded"
    assert [x["id"] for x in PL.adopted(con, "hsr", SUN)] == [second["plan"]["id"]]


def test_an_infeasible_plan_is_not_adopted_without_saying_so(con):
    tiny = plan({**BODY, "primary_fleet": [{"type": "e_loader_3w", "count": 1}], "shift_h": 1})
    assert tiny["plan"]["feasibility"] == "infeasible"
    assert {e["code"] for e in tiny["plan"]["exceptions"]} & {"unserved_demand", "over_shift"}
    with pytest.raises(PL.PlanError) as e:
        PL.adopt(con, "hsr", tiny["plan"]["id"], PLANNER)
    assert e.value.status == 409
    what_if = plan({**BODY, "date": None})
    with pytest.raises(PL.PlanError):
        PL.adopt(con, "hsr", what_if["plan"]["id"], PLANNER, accept_exceptions=True)
    with pytest.raises(PL.PlanError) as e:
        PL.adopt(con, "hsr", tiny["plan"]["id"], {"name": "Asha", "role": "surveyor"}, accept_exceptions=True)
    assert e.value.status == 403


def test_route_plans_api(con):
    client = TestClient(app)
    r = plan()
    assert client.get(f"/api/pilots/hsr/route-plans?sector=Sector 4&date={SUN}").json()["plans"][0]["id"] == r["plan"]["id"]
    a = client.post(f"/api/pilots/hsr/route-plans/{r['plan']['id']}/adopt", json={"accept_exceptions": True}, headers=H)
    assert a.status_code == 200 and a.json()["status"] == "adopted"
    day = client.get(f"/api/pilots/hsr/routes?date={SUN}").json()
    assert day["plans"][0]["id"] == r["plan"]["id"]
    assert client.get("/api/pilots/hsr/route-plans/nope").status_code == 404
