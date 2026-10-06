"""Collection Intelligence Agent: capacity bound, checks, scenarios, evaluation, recommendation, approval,
review with evidence-based causes, patterns, schedule and replanning conditions."""

import copy
from datetime import date, datetime, timedelta

import pytest

from backend import agent as A
from backend import cycle as C
from backend import facilities as F
from backend import operations as O
from backend import plans as PL
from backend import resources as R
from backend.config import CACHE_DIR
from backend.survey import db

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
FLEET = {"name": "Meena", "role": "fleet_workforce_manager"}
TUE, SUN = "2026-10-06", "2026-10-11"
REAL_CONFIG = A.config()


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    cfg = copy.deepcopy(REAL_CONFIG)
    cfg["planning"]["solver_time_limit_s"] = 3
    monkeypatch.setattr(A, "config", lambda: cfg)
    c = A.connect()
    C.load_template(c, "hsr", [], PLANNER)
    yield c
    c.close()


def places(con, sector="Sector 4"):
    F.save_plan_places(con, "hsr", sector, {"depot": {"lon": 77.6445, "lat": 12.9170}, "mrf": {"lon": 77.6300, "lat": 12.9050}}, PLANNER)


def vehicle(con, reg, type_="e_loader_3w", home="Sector 4"):
    return R.add(con, "vehicle", "hsr", ["Sector 1", "Sector 4"], {"type": type_, "registration": reg, "home_sector": home}, FLEET)


def test_the_capacity_bound_ignores_driving_so_it_can_only_overstate():
    b = A.capacity_bound([{"type": "e_loader_3w", "count": 2}], window_min=300, unload_min=10)
    # 550 kg at 20 kg/min = 27.5 min loading + 10 unloading = 37.5 min a trip: at most 8 trips in 5 h.
    assert b["by_type"][0]["max_trips"] == 8 and b["max_kg"] == 2 * 8 * 550


def test_without_a_depot_and_mrf_the_agent_says_it_lacks_data(con):
    r = A.plan_day(con, "hsr", "Sector 4", SUN)
    assert r["status"] == "insufficient_data" and r["plan_id"] is None
    assert any(c["name"] == "Depot and MRF" and c["status"] == "fail" for c in r["checks"])


def test_with_no_vehicles_entered_the_fleet_is_hypothetical_and_still_recommended(con):
    places(con)
    r = A.plan_day(con, "hsr", "Sector 4", SUN, "manual")
    assert r["status"] in ("recommended", "no_feasible_plan") and r["config_version"] == REAL_CONFIG["version"]
    assert next(c for c in r["checks"] if c["name"] == "Vehicles")["hypothetical"] is True
    assert {c["name"] for c in r["checks"]} >= {"Collection demand", "Collection window", "Capacity", "Crew", "MRF capacity", "Capacity from history"}
    assert next(c for c in r["checks"] if c["name"] == "Capacity from history")["status"] == "unknown"  # no history yet
    ran = [s for s in r["scenarios"] if s["plan_id"]]
    assert ran and all(s["metrics"]["coverage_pct"] is not None and s["score"] is not None for s in ran)
    rec = r["recommendation"]
    assert rec["plan_id"] == r["plan_id"] and rec["reasons"] and rec["assumptions"]
    plan = PL.get(con, "hsr", r["plan_id"])
    assert plan["status"] == "draft" and plan["inputs"]["agent_run"] == r["id"] and plan["created_by"] == "CityLoom agent"


def test_a_fleet_that_cannot_carry_the_demand_is_not_routed_and_more_capacity_is_tried(con):
    places(con)
    vehicle(con, "KA-1")
    r = A.plan_day(con, "hsr", "Sector 4", TUE)
    cap = next(c for c in r["checks"] if c["name"] == "Capacity")
    assert cap["status"] == "fail" and cap["demand_kg"] > cap["bound_kg"]
    first = r["scenarios"][0]
    assert first["key"] == "available" and first["plan_id"] is None and "Capacity bound" in first["not_run"]
    assert {s["key"] for s in r["scenarios"][1:]} == {"more_vehicles", "longer_window"}
    if r["recommendation"]["plan_id"]:
        assert r["recommendation"]["resources_needed"] or "window" in " ".join(r["recommendation"]["reasons"])
        assert any(a["label"] == "Fleet as available" for a in r["recommendation"]["alternatives"])


def test_approval_adopts_the_plan_and_records_who_and_when(con):
    places(con)
    r = A.plan_day(con, "hsr", "Sector 4", SUN)
    with pytest.raises(A.AgentError) as e:
        A.decide(con, "hsr", r["id"], {"name": "Asha", "role": "surveyor"}, True)
    assert e.value.status == 403
    chosen = r["plan_id"] or next(s["plan_id"] for s in r["scenarios"] if s["plan_id"])
    d = A.decide(con, "hsr", r["id"], PLANNER, True, plan_id=chosen, accept_exceptions=True, note="Looks right")
    assert d["status"] == "approved" and d["decided_by"] == "Arun" and d["decided_at"] and d["plan_id"] == chosen
    assert PL.get(con, "hsr", chosen)["status"] == "adopted"
    with pytest.raises(A.AgentError):
        A.decide(con, "hsr", r["id"], PLANNER, True)  # already decided
    again = A.plan_day(con, "hsr", "Sector 4", SUN)
    rej = A.decide(con, "hsr", again["id"], PLANNER, False, note="Not today")
    assert rej["status"] == "rejected" and rej["decision_note"] == "Not today"


def test_a_newer_recommendation_supersedes_the_pending_one(con):
    places(con)
    a = A.plan_day(con, "hsr", "Sector 4", SUN)
    b = A.plan_day(con, "hsr", "Sector 4", SUN)
    if a["status"] == "recommended":
        assert A.get(con, "hsr", a["id"])["status"] == "superseded" and b["status"] in ("recommended", "no_feasible_plan")


def test_the_review_states_causes_only_from_evidence(con):
    places(con)
    r = A.plan_day(con, "hsr", "Sector 4", SUN)
    chosen = r["plan_id"] or next(s["plan_id"] for s in r["scenarios"] if s["plan_id"])
    A.decide(con, "hsr", r["id"], PLANNER, True, plan_id=chosen, accept_exceptions=True)
    routes = [x for x in PL.get(con, "hsr", chosen)["routes"] if x["tier"] == "primary"]
    a = routes[0]
    O.record_route(con, "hsr", a["id"], {"outcome": "done", "actual_km": a["km"], "actual_min": a["minutes"] * 1.3, "actual_kg": a["kg"] * 1.3}, PLANNER)
    if len(routes) > 1:
        b = routes[1]
        O.record_route(con, "hsr", b["id"], {"outcome": "done", "actual_km": b["km"], "actual_min": b["minutes"] * 1.3, "actual_kg": b["kg"]}, PLANNER)
    rv = A.review_day(con, "hsr", SUN)
    assert rv["status"] == "reviewed" and rv["kind"] == "review"
    by = {x["route"]: x for x in rv["findings"]["routes"]}
    assert by[a["label"]]["cause"] == "more waste than expected" and "waste +30" in by[a["label"]]["evidence"]
    if len(routes) > 1:
        assert by[routes[1]["label"]]["cause"] == "UNKNOWN"
    assert len(rv["findings"]["unrecorded_routes"]) == len(PL.get(con, "hsr", chosen)["routes"]) - len(by)


def test_systematic_errors_become_proposals_with_their_evidence(con):
    for i in range(6):
        O._insert(con, "hsr", "route", f"r{i}", {"service_date": (date.today() - timedelta(days=i)).isoformat(), "weekday": "mon",
                                                 "sector": "Sector 4", "kind": "primary", "stream": None, "generator": None,
                                                 "vehicle_type": "e_loader_3w", "place": f"Loader {i}", "planned_kg": 1000, "planned_m": None,
                                                 "planned_km": 10, "planned_min": 200},
                  {"outcome": "done", "reason": None, "basis": "reported", "note": None, "actual_kg": 1000, "actual_m": None,
                   "actual_km": 10, "actual_min": 260}, PLANNER)
    con.commit()
    [p] = A.patterns(con, "hsr", A.config())
    assert p["measure"] == "time" and p["mean_error_pct"] == 30.0 and p["routes"] == 6 and "Back-test" in p["proposal"]
    assert A.observed_rate(con, "hsr", A.config())["kg_per_vehicle_hour"] == pytest.approx(1000 / (260 / 60), abs=0.1)


def test_the_schedule_says_what_is_due():
    cfg = copy.deepcopy(REAL_CONFIG)
    cfg["schedule"].update(enabled=True, plan_next_day_at="18:00", review_day_at="22:30", check_conditions_every_min=15)
    now = datetime(2026, 10, 6, 18, 5)
    assert A.due(now, {}, cfg) == ["plan_next_day", "conditions"]
    assert A.due(now, {"plan_next_day": "2026-10-06", "conditions": "2026-10-06T18:00:00"}, cfg) == []
    assert A.due(datetime(2026, 10, 6, 23, 0), {"plan_next_day": "2026-10-06", "conditions": "2026-10-06T22:00:00"}, cfg) == ["review_day", "conditions"]
    cfg["schedule"]["enabled"] = False
    assert A.due(now, {}, cfg) == []


def test_a_lost_vehicle_on_an_adopted_plan_triggers_one_replanning(con):
    places(con)
    v = vehicle(con, "KA-9")
    vehicle(con, "KA-T", "rear_loader_compactor", None)
    day = (date.today() + timedelta(days=1)).isoformat()
    r = A.plan_day(con, "hsr", "Sector 4", day)
    chosen = r["plan_id"] or next(s["plan_id"] for s in r["scenarios"] if s["plan_id"])
    A.decide(con, "hsr", r["id"], PLANNER, True, plan_id=chosen, accept_exceptions=True)
    assert not [c for c in A.conditions(con, "hsr", A.config()) if "Vehicle" in c["note"]]
    R.update(con, "vehicle", "hsr", ["Sector 1", "Sector 4"], v["id"], {"status": "maintenance"}, FLEET)
    [c] = [c for c in A.conditions(con, "hsr", A.config()) if "Vehicle" in c["note"]]
    assert c["sector"] == "Sector 4" and "KA-9" in c["note"]
    assert A.enqueue(con, "hsr", "plan", "Sector 4", day, "condition", c["note"]) is True
    assert A.enqueue(con, "hsr", "plan", "Sector 4", day, "condition", c["note"]) is False  # already waiting


def test_agent_api(con):
    from fastapi.testclient import TestClient
    from backend.api.main import app
    client = TestClient(app)
    s = client.get("/api/pilots/hsr/agent").json()
    assert s["config"]["version"] == REAL_CONFIG["version"] and "pending_approval" in s
    assert client.post("/api/pilots/hsr/agent/plan", json={"sectors": ["Sector 4"]},
                       headers={"X-SWM-Role": "surveyor", "X-SWM-User": "A", "X-SWM-Sectors": "Sector 4"}).status_code == 403
    assert client.get("/api/pilots/hsr/agent/runs/nope").status_code == 404


def test_a_scheduled_tick_plans_the_next_day_by_itself(con, tmp_path, monkeypatch):
    from backend import resources as RES
    places(con)
    now = datetime(2026, 10, 6, 18, 5)
    out = A.tick("hsr", ["Sector 4"], now=now, db_path=RES.DB_PATH)
    assert out["queued"] == 1 and len(out["ran"]) == 1  # the next day's plan (the conditions check found nothing)
    [r] = A.runs(con, "hsr", "2026-10-07")
    assert r["trigger"] == "schedule" and r["status"] in ("recommended", "no_feasible_plan")
    again = A.tick("hsr", ["Sector 4"], now=now.replace(minute=6), db_path=RES.DB_PATH)
    assert again == {"queued": 0, "ran": []}  # once a day
