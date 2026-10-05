"""Facilities: one record per place (depot, truck yard, transfer station, MRF), closed not deleted,
every change logged; a sector's route-plan places saved and loaded."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import facilities as F
from backend.api.main import app

PLANNER = {"name": "Arun", "role": "planner"}
SURVEYOR = {"name": "Asha", "role": "surveyor"}
A, B, C = (77.6445, 12.9170), (77.6300, 12.9050), (77.6400, 12.9120)


@pytest.fixture()
def con():
    c = F.connect()
    yield c
    c.close()


def test_a_facility_is_added_moved_and_closed_with_a_log(con):
    f = F.add(con, "hsr", {"kind": "mrf", "lon": B[0], "lat": B[1], "capacity_t_day": 20, "streams": ["dry"]}, PLANNER)
    assert f["name"] == "MRF 1" and f["sector"] is None and f["status"] == "active" and f["streams"] == ["dry"]
    f = F.update(con, "hsr", f["id"], {"lon": C[0], "lat": C[1], "name": "HSR MRF"}, PLANNER)
    assert (f["lon"], f["lat"], f["name"]) == (C[0], C[1], "HSR MRF")
    F.update(con, "hsr", f["id"], {"status": "closed"}, PLANNER)
    assert not F.items(con, "hsr", "mrf") and F.items(con, "hsr", "mrf", include_closed=True)
    log = F.history(con, f["id"])
    assert "created" in log[0]["change"] and log[1]["change"]["lon"] == [B[0], C[0]] and log[2]["change"]["status"][1] == "closed"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM facility")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM facility_log")


def test_only_planners_change_facilities_and_values_are_checked(con):
    with pytest.raises(F.FacilityError) as e:
        F.add(con, "hsr", {"kind": "depot", "lon": A[0], "lat": A[1]}, SURVEYOR)
    assert e.value.status == 403
    for bad in ({"kind": "landfill", "lon": 1, "lat": 1}, {"kind": "depot"}, {"kind": "depot", "lon": 500, "lat": 1},
                {"kind": "mrf", "lon": 1, "lat": 1, "capacity_t_day": -3}, {"kind": "mrf", "lon": 1, "lat": 1, "streams": ["ash"]}):
        with pytest.raises(F.FacilityError):
            F.add(con, "hsr", bad, PLANNER)


def test_a_sectors_plan_places_are_saved_and_shared_where_they_should_be(con):
    assert F.plan_places(con, "hsr", "Sector 1") == {"depot": None, "truck_yard": None, "mrf": None, "transfer_stations": []}
    p = F.save_plan_places(con, "hsr", "Sector 1", {
        "depot": {"lon": A[0], "lat": A[1]}, "mrf": {"lon": B[0], "lat": B[1]}, "truck_yard": None,
        "transfer_stations": [{"lon": C[0], "lat": C[1]}, {"lon": C[0] + 0.01, "lat": C[1]}]}, PLANNER)
    assert p["depot"]["sector"] == "Sector 1" and p["mrf"]["sector"] is None and len(p["transfer_stations"]) == 2
    # Another sector sees the city's MRF but not Sector 1's depot or stations.
    s4 = F.plan_places(con, "hsr", "Sector 4")
    assert s4["mrf"]["id"] == p["mrf"]["id"] and s4["depot"] is None and s4["transfer_stations"] == []
    # Saving again: a moved station keeps its record, a station left out is closed, a new one is added.
    keep, drop = p["transfer_stations"]
    p2 = F.save_plan_places(con, "hsr", "Sector 1", {"transfer_stations": [
        {"id": keep["id"], "lon": C[0], "lat": C[1] + 0.002}, {"lon": A[0], "lat": A[1] + 0.003}]}, PLANNER)
    ids = {s["id"] for s in p2["transfer_stations"]}
    assert keep["id"] in ids and drop["id"] not in ids and len(ids) == 2
    assert F.get(con, "hsr", drop["id"])["status"] == "closed"
    assert F.get(con, "hsr", keep["id"])["lat"] == round(C[1] + 0.002, 6)
    # Saving the same places again changes nothing.
    n = con.execute("SELECT COUNT(*) FROM facility_log").fetchone()[0]
    F.save_plan_places(con, "hsr", "Sector 1", {"depot": {"lon": A[0], "lat": A[1]}, "transfer_stations": [
        {"id": s["id"], "lon": s["lon"], "lat": s["lat"]} for s in p2["transfer_stations"]]}, PLANNER)
    assert con.execute("SELECT COUNT(*) FROM facility_log").fetchone()[0] == n


def test_facilities_api():
    client = TestClient(app)
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    r = client.put("/api/pilots/hsr/plan-places", json={"sector": "Sector 4", "depot": {"lon": A[0], "lat": A[1]},
                                                        "mrf": {"lon": B[0], "lat": B[1]}, "transfer_stations": [{"lon": C[0], "lat": C[1]}]},
                   headers=h)
    assert r.status_code == 200 and len(r.json()["transfer_stations"]) == 1
    assert client.get("/api/pilots/hsr/plan-places?sector=Sector 4").json()["depot"]["lon"] == A[0]
    fs = client.get("/api/pilots/hsr/facilities?sector=Sector 4").json()["facilities"]
    assert {f["kind"] for f in fs} == {"depot", "mrf", "transfer_station"}
    mrf = next(f for f in fs if f["kind"] == "mrf")
    assert client.patch(f"/api/pilots/hsr/facilities/{mrf['id']}", json={"capacity_t_day": 30}, headers=h).json()["capacity_t_day"] == 30
    assert len(client.get(f"/api/pilots/hsr/facilities/{mrf['id']}/history").json()["history"]) == 2
    assert client.put("/api/pilots/hsr/plan-places", json={"sector": "Sector 4"},
                      headers={"X-SWM-Role": "surveyor", "X-SWM-User": "Asha", "X-SWM-Sectors": "Sector 4"}).status_code == 403
    assert client.get("/api/pilots/hsr/plan-places?sector=Sector 99").status_code == 404
