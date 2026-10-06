"""Operations: actual outcomes against plans, append-only, moving demand statuses; routes record their stops."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import cycle as C
from backend import demand as D
from backend import operations as O
from backend import plans as PL
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
H = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
SUN = "2026-10-11"


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    c = O.connect()
    C.load_template(c, "hsr", [], PLANNER)
    yield c
    c.close()


def adopted_plan(con):
    body = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050], "date": SUN, "time_limit_s": 5,
            "primary_fleet": [{"type": "e_loader_3w", "count": 3}], "secondary_fleet": [{"type": "rear_loader_compactor", "count": 1}]}
    r = TestClient(app).post("/api/pilots/hsr/v2/plan", json=body, headers=H).json()
    return PL.adopt(con, "hsr", r["plan"]["id"], PLANNER, accept_exceptions=True)


def test_a_demand_outcome_is_recorded_and_moves_its_status(con):
    D.generate(con, "hsr", SUN, ["Sector 4"])
    street = next(d for d in D.demands(con, "hsr", SUN, "Sector 4", "cleaning") if d["source_type"] == "street")
    with pytest.raises(O.OperationError):
        O.record_demand(con, "hsr", street["id"], {"outcome": "missed"}, PLANNER)  # a miss needs a reason
    with pytest.raises(O.OperationError) as e:
        O.record_demand(con, "hsr", street["id"], {"outcome": "done"}, {"name": "A", "role": "surveyor"})
    assert e.value.status == 403
    O.record_demand(con, "hsr", street["id"], {"outcome": "partial", "actual_m": street["length_m"] / 2, "reason": "worker_absent"}, PLANNER)
    assert D.get(con, "hsr", street["id"])["status"] == "missed"
    O.record_demand(con, "hsr", street["id"], {"outcome": "done", "actual_m": street["length_m"]}, PLANNER)  # a correction
    assert D.get(con, "hsr", street["id"])["status"] == "done"
    [rec] = O.records(con, "hsr", SUN, "Sector 4", "demand")
    assert rec["outcome"] == "done" and rec["planned_m"] == street["length_m"] and rec["weekday"] == "sun"
    assert len(O.records(con, "hsr", SUN, latest_only=False)) == 2
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE operation SET outcome = 'missed'")


def test_a_route_records_its_actuals_and_its_stops(con):
    plan = adopted_plan(con)
    route = next(r for r in plan["routes"] if r["tier"] == "primary")
    stops = sorted({PL.base_point(s["point_id"]) for s in route["stops"] if s["kind"] == "collect"})
    with pytest.raises(O.OperationError):
        O.record_route(con, "hsr", route["id"], {"outcome": "partial", "missed_points": ["A:nowhere"], "reason": "road_closed"}, PLANNER)
    r = O.record_route(con, "hsr", route["id"], {"outcome": "partial", "actual_km": route["km"] * 1.2, "actual_min": route["minutes"] + 30,
                                                 "actual_kg": route["kg"] * 1.1, "missed_points": stops[:1], "reason": "road_closed"}, PLANNER)
    assert r["demands"]["missed"] >= 1 and r["demands"]["done"] >= 1
    sheet = O.day_sheet(con, "hsr", SUN, "Sector 4")
    rec = next(x for x in sheet["routes"] if x["route_id"] == route["id"])["recorded"]
    assert rec["actual_km"] == pytest.approx(route["km"] * 1.2) and rec["planned_km"] == route["km"] and rec["vehicle_type"] == route["vehicle_type"]
    statuses = {d["source_id"]: d["status"] for d in D.demands(con, "hsr", SUN, "Sector 4", "collection", D.STATUSES)}
    assert statuses[stops[0]] == "missed" and all(statuses[s] == "done" for s in stops[1:] if s in statuses)


def test_only_adopted_routes_are_recorded(con):
    body = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050], "date": SUN, "time_limit_s": 5,
            "primary_fleet": [{"type": "e_loader_3w", "count": 3}], "secondary_fleet": []}
    r = TestClient(app).post("/api/pilots/hsr/v2/plan", json=body, headers=H).json()
    route = PL.get(con, "hsr", r["plan"]["id"])["routes"][0]
    with pytest.raises(O.OperationError) as e:
        O.record_route(con, "hsr", route["id"], {"outcome": "done"}, PLANNER)
    assert e.value.status == 409


def test_operations_api(con):
    plan = adopted_plan(con)
    client = TestClient(app)
    sheet = client.get(f"/api/pilots/hsr/operations/day?sector=Sector 4&date={SUN}").json()
    assert sheet["routes"] and "road_closed" in sheet["reasons"]
    assert all(s["label"] and s["id"] for r in sheet["routes"] for s in r["stops"])
    rid = sheet["routes"][0]["route_id"]
    assert client.post(f"/api/pilots/hsr/operations/routes/{rid}", json={"outcome": "done", "actual_km": 10}, headers=H).status_code == 200
    assert client.get(f"/api/pilots/hsr/operations?date={SUN}&subject=route").json()["records"][0]["actual_km"] == 10
    assert client.post(f"/api/pilots/hsr/operations/routes/{rid}", json={"outcome": "done", "actual_km": -1}, headers=H).status_code == 422
    assert plan["status"] == "adopted"
