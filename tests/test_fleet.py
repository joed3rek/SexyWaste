"""Fleet inventory: who may change it, the change log, availability per sector and the plan cap."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import fleet
from backend.api.main import app
from backend.config import CACHE_DIR

MANAGER = {"X-SWM-Role": "fleet_workforce_manager", "X-SWM-User": "Meena"}
PLANNER = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
SECTORS = ["Sector 1", "Sector 4"]


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "DB_PATH", tmp_path / "ops.db")
    c = fleet.connect()
    yield c
    c.close()


def add(con, reg, type_="e_loader_3w", home=None, status="available"):
    return fleet.add_vehicle(con, "hsr", SECTORS, {"type": type_, "registration": reg, "home_sector": home, "status": status},
                             {"name": "Meena", "role": "fleet_workforce_manager"})


def test_only_fleet_manager_or_admin_can_change_the_fleet(con):
    with pytest.raises(fleet.FleetError) as err:
        fleet.add_vehicle(con, "hsr", SECTORS, {"type": "e_loader_3w", "registration": "X1"}, {"name": "A", "role": "planner"})
    assert err.value.status == 403
    v = fleet.add_vehicle(con, "hsr", SECTORS, {"type": "e_loader_3w", "registration": "ka 01  ab 1"}, {"name": "R", "role": "admin"})
    assert v["registration"] == "KA 01 AB 1" and v["status"] == "available" and v["streams"] == ["wet", "dry", "sanitary", "special"]


def test_registration_is_unique_and_type_and_sector_are_checked(con):
    add(con, "KA-1")
    with pytest.raises(fleet.FleetError) as err:
        add(con, "ka-1")
    assert err.value.status == 409
    with pytest.raises(fleet.FleetError):
        add(con, "KA-2", type_="hovercraft")
    with pytest.raises(fleet.FleetError):
        add(con, "KA-3", home="Sector 99")


def test_specs_default_to_the_type_and_own_figures_are_kept(con):
    v = add(con, "KA-1")
    assert v["spec"]["payload_kg"] == {"value": 550, "own": False}
    v = fleet.update_vehicle(con, "hsr", SECTORS, v["id"], {"payload_kg": 600, "verify": True},
                             {"name": "Meena", "role": "fleet_workforce_manager"})
    assert v["spec"]["payload_kg"] == {"value": 600.0, "own": True}
    assert v["verified_by"] == "Meena" and v["verified_at"]


def test_every_change_is_logged_and_the_log_is_append_only(con):
    v = add(con, "KA-1")
    fleet.update_vehicle(con, "hsr", SECTORS, v["id"], {"status": "under_repair"}, {"name": "Meena", "role": "fleet_workforce_manager"})
    log = fleet.history(con, v["id"])
    assert len(log) == 2 and log[1]["change"] == {"status": ["available", "under_repair"]}
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE vehicle_log SET change = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM vehicle_log")


def test_available_counts_the_sector_and_the_shared_pool(con):
    assert fleet.available(con, "hsr", "Sector 1") == {"inventory": False, "by_type": {}}
    add(con, "A", home="Sector 1")
    add(con, "B", home="Sector 4")
    add(con, "C")  # shared pool
    add(con, "D", home="Sector 1", status="under_repair")
    add(con, "T", type_="rear_loader_compactor")
    assert fleet.available(con, "hsr", "Sector 1") == {"inventory": True, "by_type": {"e_loader_3w": 2, "rear_loader_compactor": 1}}
    assert fleet.available(con, "hsr", "Sector 4")["by_type"]["e_loader_3w"] == 2


def test_plan_cannot_use_more_vehicles_than_exist(con):
    fleet.check_plan_fleet(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 40}])  # no inventory: not capped
    add(con, "A", home="Sector 1")
    add(con, "B")
    fleet.check_plan_fleet(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 2}])
    with pytest.raises(fleet.FleetError, match="available: 2"):
        fleet.check_plan_fleet(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 3}])
    with pytest.raises(fleet.FleetError, match="available: 0"):
        fleet.check_plan_fleet(con, "hsr", "Sector 1", [{"type": "mini_tipper", "count": 1}])


def test_fleet_api_reads_for_all_and_writes_for_the_manager(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "DB_PATH", tmp_path / "ops.db")
    client = TestClient(app)
    assert client.post("/api/pilots/hsr/fleet", json={"type": "e_loader_3w", "registration": "KA1"}).status_code == 401
    assert client.post("/api/pilots/hsr/fleet", json={"type": "e_loader_3w", "registration": "KA1"}, headers=PLANNER).status_code == 403
    v = client.post("/api/pilots/hsr/fleet", json={"type": "e_loader_3w", "registration": "KA1", "home_sector": "Sector 4"}, headers=MANAGER).json()
    assert client.patch(f"/api/pilots/hsr/fleet/{v['id']}", json={"status": "retired"}, headers=MANAGER).json()["status"] == "retired"
    assert client.get("/api/pilots/hsr/fleet/available?sector=Sector 4").json() == {"inventory": True, "by_type": {}}
    assert len(client.get(f"/api/pilots/hsr/fleet/{v['id']}/history").json()["history"]) == 2


@pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")
def test_plan_endpoint_refuses_a_fleet_larger_than_the_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "DB_PATH", tmp_path / "ops.db")
    con = fleet.connect()
    add(con, "A", home="Sector 4")
    con.close()
    body = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050],
            "primary_fleet": [{"type": "e_loader_3w", "count": 2}], "secondary_fleet": []}
    r = TestClient(app).post("/api/pilots/hsr/v2/plan/jobs", json=body)
    assert r.status_code == 422 and "available: 1" in r.json()["detail"]


@pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")
def test_suggest_fleet_stays_within_the_inventory_and_says_what_is_missing():
    from backend.routing import twotier as T
    out = T.suggest_fleet(T.PlanInput(pilot="hsr", sector="Sector 4", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                                      primary_fleet=[{"type": "e_loader_3w", "count": 1}], secondary_fleet=[], time_limit_s=5),
                          2, caps={"e_loader_3w": 2})
    assert sum(r["count"] for r in out["primary_fleet"]) <= 2
    assert not out["fits"]
    short = next(a for a in out["advice"] if a["code"] == "fleet_short")
    assert short["needs"][0]["type"] == "e_loader_3w" and short["needs"][0]["more"] > 0
