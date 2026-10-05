"""Work planner: cleaning demands given to cleaning staff and equipment, shortages reported, adoption."""

import pytest
from fastapi.testclient import TestClient

from backend import demand as D
from backend import resources as R
from backend import workplan as W
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db, gvp

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
HR = {"name": "Hema", "role": "hr_manager"}
FLEET = {"name": "Meena", "role": "fleet_workforce_manager"}
TUE = "2026-10-06"


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    c = W.connect()
    yield c
    c.close()


def staff(con, n, role="sweeper", hours=8):
    for i in range(n):
        R.add(con, "staff", "hsr", ["Sector 4"], {"worker_id": f"{role[:2]}{i}", "name": f"{role} {i}", "role": role,
                                                 "home_sector": "Sector 4", "hours_per_day": hours}, HR)


def test_without_staff_the_plan_uses_hypothetical_workers_and_says_so(con):
    p = W.build(con, "hsr", "Sector 4", TUE)
    assert p["hypothetical"] and p["feasibility"] == "feasible_with_warnings"
    assert {e["code"] for e in p["exceptions"]} == {"hypothetical_workforce"} and not p["unassigned"]
    assert p["summary"]["assigned_hours"] == pytest.approx(p["summary"]["required_hours"], abs=0.2)
    assert all(w["minutes"] <= 480.01 for w in p["workers"])


def test_every_metre_is_given_to_someone_or_reported_short(con):
    staff(con, 2)
    p = W.build(con, "hsr", "Sector 4", TUE)
    assert p["feasibility"] == "infeasible" and p["exceptions"][0]["code"] == "workforce_short"
    given = sum(w["metres"] for w in p["workers"]) + sum(u["metres"] or 0 for u in p["unassigned"])
    assert given == pytest.approx(p["summary"]["street_m"], rel=1e-3)
    assert all(w["minutes"] <= 480.01 for w in p["workers"]) and len(p["workers"]) == 2
    short = p["exceptions"][0]
    assert short["hours"] == pytest.approx(p["summary"]["required_hours"] - 16, abs=0.2) and short["actions"]


def test_a_verified_gvp_goes_to_a_response_worker_with_its_equipment(con):
    from tests.test_gvp import _kerb_points
    staff(con, 40)
    staff(con, 1, "gvp_response_worker")
    R.add(con, "equipment", "hsr", ["Sector 4"], {"equipment_id": "L1", "type": "loader", "home_sector": "Sector 4"}, FLEET)
    g = gvp.report("hsr", {"name": "Asha", "role": "surveyor", "sectors": ["Sector 4"]}, *_kerb_points("Sector 4")[0],
                   ["dry"], 80, "daily", ["street_vendors"], severity="high")
    gvp.act("hsr", g["id"], {"name": "Ravi", "role": "survey_supervisor", "sectors": ["Sector 4"]}, "verify")
    p = W.build(con, "hsr", "Sector 4", TUE)
    job = next(j for w in p["workers"] for j in w["jobs"] if j["kind"] == "gvp")
    who = next(w for w in p["workers"] if job in w["jobs"])
    assert "gvp response worker" in who["label"] and job["minutes"] == 240
    assert any(e for e in job["equipment"] if not e.startswith("missing:"))  # the loader
    assert "missing:tricycle" in job["equipment"]
    assert "equipment_short" in {e["code"] for e in p["exceptions"]} and p["feasibility"] == "feasible_with_warnings"


def test_adopting_plans_the_cleaning_demands(con):
    staff(con, 2)
    p = W.save(con, "hsr", W.build(con, "hsr", "Sector 4", TUE), PLANNER)
    with pytest.raises(W.WorkPlanError):
        W.adopt(con, "hsr", p["id"], PLANNER)  # infeasible: needs accept_exceptions
    a = W.adopt(con, "hsr", p["id"], PLANNER, accept_exceptions=True)
    assigned = {j["demand_id"] for w in a["workers"] for j in w["jobs"]}
    for d in D.demands(con, "hsr", TUE, "Sector 4", "cleaning", D.STATUSES):
        assert d["status"] == ("planned" if d["id"] in assigned else "open")
    q = W.save(con, "hsr", W.build(con, "hsr", "Sector 4", TUE), PLANNER)
    W.adopt(con, "hsr", q["id"], PLANNER, accept_exceptions=True)
    assert W.get(con, "hsr", p["id"])["status"] == "superseded"


def test_work_plans_api(con):
    client = TestClient(app)
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    r = client.post("/api/pilots/hsr/work-plans", json={"sector": "Sector 4", "date": TUE}, headers=h)
    assert r.status_code == 200 and r.json()["workers"]
    assert client.get(f"/api/pilots/hsr/work-plans?sector=Sector 4&date={TUE}").json()["plans"][0]["id"] == r.json()["id"]
    a = client.post(f"/api/pilots/hsr/work-plans/{r.json()['id']}/adopt", json={}, headers=h)
    assert a.status_code == 200 and a.json()["status"] == "adopted"
    assert client.post("/api/pilots/hsr/work-plans", json={"sector": "Sector 4"},
                       headers={"X-SWM-Role": "surveyor", "X-SWM-User": "A", "X-SWM-Sectors": "Sector 4"}).status_code == 403
