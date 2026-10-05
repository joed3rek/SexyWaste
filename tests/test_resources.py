"""Resource management: vehicles, staff and equipment; statuses; crews; caps on plans; the API."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import resources as R
from backend.api.main import app
from backend.config import CACHE_DIR

MANAGER = {"X-SWM-Role": "fleet_workforce_manager", "X-SWM-User": "Meena"}
PLANNER = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
ACTOR = {"name": "Meena", "role": "fleet_workforce_manager"}
HR = {"name": "Hema", "role": "hr_manager"}
HR_H = {"X-SWM-Role": "hr_manager", "X-SWM-User": "Hema"}
SECTORS = ["Sector 1", "Sector 4"]


@pytest.fixture()
def con():
    c = R.connect()
    yield c
    c.close()


def vehicle(con, reg, type_="e_loader_3w", home=None, status="available", **kw):
    return R.add(con, "vehicle", "hsr", SECTORS, {"type": type_, "registration": reg, "home_sector": home, "status": status, **kw}, ACTOR)


def worker(con, wid, role, home=None, status="available", **kw):
    return R.add(con, "staff", "hsr", SECTORS, {"worker_id": wid, "name": f"Worker {wid}", "role": role, "home_sector": home,
                                                "status": status, **kw}, HR)


# ---------- Inventories ----------

def test_only_the_manager_or_admin_changes_resources(con):
    with pytest.raises(R.ResourceError) as err:
        R.add(con, "vehicle", "hsr", SECTORS, {"type": "e_loader_3w", "registration": "X1"}, {"name": "A", "role": "planner"})
    assert err.value.status == 403
    v = R.add(con, "vehicle", "hsr", SECTORS, {"type": "e_loader_3w", "registration": "ka 01  ab 1"}, {"name": "R", "role": "admin"})
    assert v["registration"] == "KA 01 AB 1" and v["status"] == "available" and v["streams"] == ["wet", "dry", "sanitary", "special"]


def test_statuses_are_the_five_and_only_working_ones_are_plannable(con):
    assert R.STATUSES == ("available", "assigned", "in_use", "maintenance", "unavailable")
    for i, st in enumerate(R.STATUSES):
        v = vehicle(con, f"V{i}", status=st)
        assert v["plannable"] == (st in ("available", "assigned", "in_use"))
    with pytest.raises(R.ResourceError):
        vehicle(con, "V9", status="under_repair")


def test_vehicle_has_shift_gps_and_crew_defaulting_to_its_class(con):
    v = vehicle(con, "KA-1", type_="mini_tipper", shift="morning", gps=True)
    assert v["shift"] == "morning" and v["gps"] is True
    cls = R.vehicle_classes()["mini_tipper"]["crew"]
    assert v["crew"] == {"drivers": cls["drivers"], "collectors": cls["collectors"], "own": False}
    v = R.update(con, "vehicle", "hsr", SECTORS, v["id"], {"collectors_required": 3, "payload_kg": 800, "verify": True}, ACTOR)
    assert v["crew"]["collectors"] == 3 and v["crew"]["own"] and v["spec"]["payload_kg"] == {"value": 800.0, "own": True}
    assert v["verified_by"] == "Meena"
    with pytest.raises(R.ResourceError):
        R.update(con, "vehicle", "hsr", SECTORS, v["id"], {"shift": "midnight"}, ACTOR)


def test_staff_inventory(con):
    s = worker(con, "w-001", "sweeper", home="Sector 4", shift="morning", hours_per_day=7, skills=["first aid"], supervisor="Ravi")
    assert s["worker_id"] == "W-001" and s["role"] == "sweeper" and s["hours_per_day"] == 7 and s["skills"] == ["first aid"]
    with pytest.raises(R.ResourceError):
        worker(con, "W-001", "driver")  # same ID
    with pytest.raises(R.ResourceError):
        worker(con, "W-002", "pilot")
    with pytest.raises(R.ResourceError):
        worker(con, "W-003", "driver", hours_per_day=20)


def test_equipment_inventory(con):
    e = R.add(con, "equipment", "hsr", SECTORS, {"equipment_id": "ms-1", "type": "mechanical_sweeper", "capacity": 4, "capacity_unit": "km/h",
                                                 "condition": "good", "operator_role": "driver", "next_service": "2020-01-01",
                                                 "status": "maintenance"}, ACTOR)
    assert e["equipment_id"] == "MS-1" and e["service_due"] and not e["plannable"]
    with pytest.raises(R.ResourceError):
        R.add(con, "equipment", "hsr", SECTORS, {"equipment_id": "x", "type": "rocket"}, ACTOR)
    with pytest.raises(R.ResourceError):
        R.add(con, "equipment", "hsr", SECTORS, {"equipment_id": "y", "type": "handcart", "next_service": "soon"}, ACTOR)


def test_every_change_is_logged_and_the_log_is_append_only(con):
    v = vehicle(con, "KA-1")
    R.update(con, "vehicle", "hsr", SECTORS, v["id"], {"status": "maintenance"}, ACTOR)
    log = R.history(con, "vehicle", v["id"])
    assert len(log) == 2 and log[1]["change"] == {"status": ["available", "maintenance"]}
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE resource_log SET change = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM resource_log")


def test_the_first_fleet_table_is_carried_over(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE vehicle (id TEXT PRIMARY KEY, pilot TEXT NOT NULL, registration TEXT NOT NULL, type TEXT NOT NULL, home_sector TEXT,
          status TEXT NOT NULL CHECK (status IN ('available', 'under_repair', 'off_road', 'retired')), streams TEXT NOT NULL,
          payload_kg REAL, body_volume_m3 REAL, vehicle_width_m REAL, min_road_width_m REAL, compartments INTEGER, notes TEXT,
          verified_at TEXT, verified_by TEXT, created_at TEXT NOT NULL, created_by TEXT, updated_at TEXT NOT NULL, updated_by TEXT);
        CREATE TABLE vehicle_log (id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL REFERENCES vehicle(id), at TEXT NOT NULL,
          user_name TEXT, user_role TEXT, change TEXT NOT NULL);
        INSERT INTO vehicle VALUES ('a', 'hsr', 'KA1', 'e_loader_3w', NULL, 'under_repair', '["dry"]', NULL, NULL, NULL, NULL, NULL, NULL,
          NULL, NULL, 't', 'M', 't', 'M');
        INSERT INTO vehicle VALUES ('b', 'hsr', 'KA2', 'e_loader_3w', NULL, 'retired', '["dry"]', NULL, NULL, NULL, NULL, NULL, NULL,
          NULL, NULL, 't', 'M', 't', 'M');
        INSERT INTO vehicle_log (vehicle_id, at, change) VALUES ('a', 't', '{}');""")
    old.commit()
    old.close()
    con = R.connect(path)
    vs = {v["registration"]: v for v in R.items(con, "vehicle", "hsr")}
    assert vs["KA1"]["status"] == "maintenance" and vs["KA2"]["status"] == "unavailable" and "Retired" in vs["KA2"]["notes"]
    assert len(R.history(con, "vehicle", "a")) == 1
    con.close()


# ---------- Planning ----------

def test_available_counts_the_sector_and_the_shared_pool(con):
    av = R.available(con, "hsr", "Sector 1")
    assert av["inventory"] == {"vehicle": False, "staff": False, "equipment": False} and av["vehicles"] == {}
    vehicle(con, "A", home="Sector 1")
    vehicle(con, "B", home="Sector 4")
    vehicle(con, "C")  # shared pool
    vehicle(con, "D", home="Sector 1", status="maintenance")
    vehicle(con, "E", home="Sector 1", status="in_use", gps=True)
    worker(con, "W1", "driver", home="Sector 1")
    worker(con, "W2", "sweeper", home="Sector 1", hours_per_day=6)
    av = R.available(con, "hsr", "Sector 1")
    assert av["vehicles"] == {"e_loader_3w": 3} and av["vehicles_without_gps"] == 2
    assert av["staff"] == {"driver": {"count": 1, "hours": 8}, "sweeper": {"count": 1, "hours": 6}}


def test_a_plan_cannot_use_more_vehicles_or_crew_than_exist(con):
    R.check_plan(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 40}])  # empty inventories: not capped
    vehicle(con, "A", home="Sector 1")
    vehicle(con, "B")
    R.check_plan(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 2}])  # no staff entered: crew not capped
    with pytest.raises(R.ResourceError, match="available: 2"):
        R.check_plan(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 3}])
    worker(con, "D1", "driver")
    worker(con, "C1", "waste_collector")
    with pytest.raises(R.ResourceError, match="2 drivers \\(available: 1\\)"):
        R.check_plan(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 2}])
    R.check_plan(con, "hsr", "Sector 1", [{"type": "e_loader_3w", "count": 1}])


def test_resources_api(tmp_path):
    client = TestClient(app)
    body = {"type": "e_loader_3w", "registration": "KA1", "home_sector": "Sector 4", "gps": True}
    assert client.post("/api/pilots/hsr/resources/vehicle", json=body).status_code == 401
    assert client.post("/api/pilots/hsr/resources/vehicle", json=body, headers=PLANNER).status_code == 403
    v = client.post("/api/pilots/hsr/resources/vehicle", json=body, headers=MANAGER).json()
    assert client.patch(f"/api/pilots/hsr/resources/vehicle/{v['id']}", json={"status": "unavailable"}, headers=MANAGER).json()["status"] == "unavailable"
    assert client.post("/api/pilots/hsr/resources/staff", json={"worker_id": "W1", "name": "Lakshmi", "role": "sweeper"}, headers=MANAGER).status_code == 403
    w = client.post("/api/pilots/hsr/resources/staff", json={"worker_id": "W1", "name": "Lakshmi", "role": "sweeper"}, headers=HR_H).json()
    assert w["role"] == "sweeper"
    av = client.get("/api/pilots/hsr/resources/available?sector=Sector 4").json()
    assert av["vehicles"] == {} and av["staff"]["sweeper"]["count"] == 1 and av["gps_rule"]["rule"] == "8(h)(ix)" and av["gps_rule"]["required"]
    assert len(client.get(f"/api/pilots/hsr/resources/vehicle/{v['id']}/history").json()["history"]) == 2
    assert client.get("/api/pilots/hsr/resources/robots").status_code == 404
    cfg = client.get("/api/resources/config").json()
    assert "sweeper" in cfg["staff_roles"] and cfg["vehicle_classes"][0]["crew"]["drivers"] == 1


@pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")
def test_plan_endpoint_refuses_more_than_the_inventory():
    con = R.connect()
    vehicle(con, "A", home="Sector 4")
    con.close()
    body = {"sector": "Sector 4", "depot": [77.6445, 12.9170], "mrf": [77.6300, 12.9050],
            "primary_fleet": [{"type": "e_loader_3w", "count": 2}], "secondary_fleet": []}
    r = TestClient(app).post("/api/pilots/hsr/v2/plan/jobs", json=body)
    assert r.status_code == 422 and "available: 1" in r.json()["detail"]


@pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")
def test_suggest_fleet_stays_within_vehicles_and_crew():
    from backend.routing import twotier as T
    inp = T.PlanInput(pilot="hsr", sector="Sector 4", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                      primary_fleet=[{"type": "e_loader_3w", "count": 1}], secondary_fleet=[], time_limit_s=5)
    out = T.suggest_fleet(inp, 2, caps={"e_loader_3w": 4},
                          crew={"drivers": 2, "collectors": 5, "per_vehicle": {"e_loader_3w": {"drivers": 1, "collectors": 1}}})
    assert sum(r["count"] for r in out["primary_fleet"]) <= 2 and not out["fits"]
    short = next(a for a in out["advice"] if a["code"] == "crew_short")
    assert short["drivers"] > 0


def test_each_inventory_has_its_own_manager(con):
    with pytest.raises(R.ResourceError) as err:
        R.add(con, "staff", "hsr", SECTORS, {"worker_id": "W9", "name": "X", "role": "sweeper"}, ACTOR)
    assert err.value.status == 403
    with pytest.raises(R.ResourceError):
        R.add(con, "vehicle", "hsr", SECTORS, {"type": "e_loader_3w", "registration": "V9"}, HR)
    R.add(con, "equipment", "hsr", SECTORS, {"equipment_id": "H1", "type": "handcart"}, ACTOR)
    R.add(con, "staff", "hsr", SECTORS, {"worker_id": "W9", "name": "X", "role": "sweeper"}, {"name": "A", "role": "admin"})
