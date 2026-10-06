"""Command centre: today's demands made on first read, failures counted from their owners, and per-sector reasons."""

import pytest
from fastapi.testclient import TestClient

from backend import command as CMD
from backend import demand as D
from backend import operations as O
from backend import performance as P
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
DAY = "2026-10-06"


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    c = P.connect()
    yield c
    c.close()


def test_first_read_makes_todays_demands_and_says_why(con):
    o = CMD.overview(con, "hsr", ["Sector 4"], DAY)
    s = o["sectors"][0]
    assert s["collection_demands"] > 0 and s["sweeping_km"] > 0 and s["status"]["open"] > 0
    assert "No adopted route plan for today." in s["why"] and o["today"]["sectors_without_route_plan"] == ["Sector 4"]
    n = con.execute("SELECT COUNT(*) FROM service_demand").fetchone()[0]
    CMD.overview(con, "hsr", ["Sector 4"], DAY)
    assert con.execute("SELECT COUNT(*) FROM service_demand").fetchone()[0] == n  # not made twice


def test_service_level_and_sectors_below_target(con, monkeypatch):
    monkeypatch.setattr(D, "today", lambda: DAY)
    CMD.overview(con, "hsr", ["Sector 4"], DAY)
    streets = [d for d in D.demands(con, "hsr", DAY, "Sector 4", "cleaning") if d["source_type"] == "street"]
    O.record_demand(con, "hsr", streets[0]["id"], {"outcome": "done"}, PLANNER)
    O.record_demand(con, "hsr", streets[1]["id"], {"outcome": "missed", "reason": "worker_absent"}, PLANNER)
    o = CMD.overview(con, "hsr", ["Sector 4"], DAY)
    lv = o["city"]["service_level"]["cleaning"]
    expect = 100 * streets[0]["length_m"] / (streets[0]["length_m"] + streets[1]["length_m"])
    assert lv["pct"] == pytest.approx(expect, abs=0.1) and o["failures"]["sectors_below_target"] == ["Sector 4"]
    assert o["today"]["missed"] == 1 and o["sectors"][0]["status"]["done"] == 1


def test_command_api(con):
    r = TestClient(app).get(f"/api/pilots/hsr/command?date={DAY}")
    assert r.status_code == 200
    c = r.json()
    assert len(c["sectors"]) == 7 and {"city", "failures", "today", "top_alerts"} <= set(c)
