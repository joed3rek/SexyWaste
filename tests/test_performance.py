"""Performance: required vs delivered, unrecorded work kept apart, reasons, route accuracy, GVP response and
recurrence, and planning alerts from repeated failure."""

import pytest
from fastapi.testclient import TestClient

from backend import demand as D
from backend import operations as O
from backend import performance as P
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db, gvp

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
SURVEYOR = {"name": "Asha", "role": "surveyor", "sectors": ["Sector 4"]}
SUPERVISOR = {"name": "Ravi", "role": "survey_supervisor", "sectors": ["Sector 4"]}
DAY1, DAY2 = "2026-09-29", "2026-09-30"  # in the past, so unrecorded work is unrecorded, not pending


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    c = P.connect()
    yield c
    c.close()


def streets(con, day):
    D.generate(con, "hsr", day, ["Sector 4"])
    return [d for d in D.demands(con, "hsr", day, "Sector 4", "cleaning") if d["source_type"] == "street"]


def test_required_vs_delivered_keeps_unrecorded_work_apart(con):
    s = streets(con, DAY1)
    a, b, c = s[0], s[1], s[2]
    O.record_demand(con, "hsr", a["id"], {"outcome": "done"}, PLANNER)
    O.record_demand(con, "hsr", b["id"], {"outcome": "partial", "actual_m": b["length_m"] / 2, "reason": "worker_absent"}, PLANNER)
    O.record_demand(con, "hsr", c["id"], {"outcome": "missed", "reason": "access_blocked"}, PLANNER)
    r = P.service(con, "hsr", DAY1, DAY1, "Sector 4")
    row = next(x for x in r["rows"] if x["work"] == "cleaning")
    assert row["unit"] == "m" and row["required"] == pytest.approx(sum(d["length_m"] for d in s), abs=0.5)
    delivered = a["length_m"] + b["length_m"] / 2
    assert row["delivered"] == pytest.approx(delivered, abs=0.2)
    assert row["missed"] == pytest.approx(b["length_m"] / 2 + c["length_m"], abs=0.2)
    assert row["unrecorded"] == pytest.approx(row["required"] - delivered - row["missed"], abs=0.5)
    assert row["service_level_pct"] == pytest.approx(100 * delivered / (delivered + row["missed"]), abs=0.1)
    assert row["reasons"] == {"worker_absent": 1, "access_blocked": 1}
    assert row["not_planned"] == len(s) - 3  # nothing was planned; the three recorded ones moved to done/missed


def test_repeated_misses_become_a_planning_alert(con, monkeypatch):
    monkeypatch.setattr(D, "today", lambda: DAY2)
    for day in (DAY1, DAY2):
        d = streets(con, day)[0]
        O.record_demand(con, "hsr", d["id"], {"outcome": "missed", "reason": "access_blocked"}, PLANNER)
    r = P.scan(con, "hsr", 7)
    assert r["new"]["repeated_miss"] == 1
    [a] = [x for x in P.alerts(con, "hsr") if x["kind"] == "repeated_miss"]
    assert a["facts"]["misses"] == 2 and a["causes"] and "missed 2 times" in a["message"]
    assert P.scan(con, "hsr", 7)["new"]["repeated_miss"] == 0  # no duplicate while it is open
    P.set_alert(con, "hsr", a["id"], "acknowledged", PLANNER)
    assert not [x for x in P.alerts(con, "hsr") if x["kind"] == "repeated_miss"]
    with pytest.raises(P.PerformanceError):
        P.set_alert(con, "hsr", a["id"], "resolved", SURVEYOR)


def test_a_gvp_that_keeps_coming_back_is_a_planning_signal(con):
    from tests.test_gvp import _kerb_points
    where = _kerb_points("Sector 4")[0]
    g = gvp.report("hsr", SURVEYOR, *where, ["wet"], 50, "daily", ["street_vendors"], severity="medium")
    for _ in range(2):
        gvp.act("hsr", g["id"], SUPERVISOR, "verify")
        gvp.act("hsr", g["id"], SUPERVISOR, "assign", value="Team A")
        gvp.act("hsr", g["id"], SUPERVISOR, "clear", kg=30)
        gvp.act("hsr", g["id"], {"name": "L", "role": "collector"}, "collected")
        gvp.report("hsr", SURVEYOR, *where, ["wet"], 50, "daily", ["street_vendors"], severity="medium")  # back again
    resp = P.gvp_response("hsr", "2026-01-01", "2099-12-31", "Sector 4")
    assert resp["cleared"] == 2 and resp["on_time"] == 2 and resp["recurring"][0]["recurrences"] == 2
    assert P.scan(con, "hsr")["new"]["recurring_gvp"] == 1
    a = next(x for x in P.alerts(con, "hsr") if x["kind"] == "recurring_gvp")
    assert a["ref"] == g["id"] and "Too few public bins" in a["causes"]


def test_performance_api(con):
    client = TestClient(app)
    r = client.get("/api/pilots/hsr/performance?sector=Sector 4").json()
    assert {"service", "routes", "gvps"} <= set(r) and r["service"]["days"] == 7
    assert client.get("/api/pilots/hsr/performance?start=2026-10-10&end=2026-10-01").status_code == 422
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    assert client.post("/api/pilots/hsr/planning-alerts/scan", json={"days": 14}, headers=h).status_code == 200
    assert client.get("/api/pilots/hsr/planning-alerts").json()["alerts"] == []
