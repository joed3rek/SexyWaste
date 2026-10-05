"""Clean City: street inventory and classes, overrides, public bins and their demands, GVP clearing, workforce."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import cleancity as CC
from backend import resources as R
from backend.api.main import app
from backend.config import CACHE_DIR
from backend.survey import db

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

PLANNER = {"name": "Arun", "role": "planner"}
COLLECTOR = {"name": "Lakshmi", "role": "collector"}


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")  # no GVPs from the real survey
    c = CC.connect()
    yield c
    c.close()


def a_street(con, cls="residential", sector="Sector 4"):
    return next(s for s in CC.streets(con, "hsr", sector) if s["class"] == cls and s["length_m"] > 60)


def test_every_street_has_a_class_frequency_and_a_reason(con):
    st = CC.streets(con, "hsr", "Sector 4")
    assert st and all(s["class"] in CC.CLASSES and s["cleanings_per_week"] > 0 and s["why"] for s in st)
    assert all(s["sector"] == "Sector 4" and s["length_m"] > 0 and len(s["coords"]) >= 2 for s in st)
    by = {s["suggested_class"] for s in CC.street_inventory("hsr")}
    assert {"primary", "residential", "low_intensity"} <= by
    main = [s for s in CC.street_inventory("hsr") if s["road_class"] in ("trunk", "primary", "secondary")]
    assert all(s["suggested_class"] == "primary" for s in main)


def test_planner_overrides_class_frequency_team_and_it_is_logged(con):
    s = a_street(con)
    with pytest.raises(CC.CleanCityError):
        CC.plan_street(con, "hsr", s["seg_id"], {"class": "market"}, COLLECTOR)
    s2 = CC.plan_street(con, "hsr", s["seg_id"], {"class": "market", "team": "Team A"}, PLANNER)
    assert s2["class"] == "market" and s2["class_overridden"] and s2["cleanings_per_week"] == CC.standards()["classes"]["market"]["cleanings_per_week"]
    s3 = CC.plan_street(con, "hsr", s["seg_id"], {"cleanings_per_week": 21}, PLANNER)
    assert s3["cleanings_per_week"] == 21 and s3["frequency_overridden"] and s3["team"] == "Team A"
    s4 = CC.plan_street(con, "hsr", s["seg_id"], {"class": None}, PLANNER)
    assert s4["class"] == s["suggested_class"]
    assert con.execute("SELECT COUNT(*) FROM cleancity_log WHERE ref = ?", (s["seg_id"],)).fetchone()[0] == 3
    with pytest.raises(CC.CleanCityError):
        CC.plan_street(con, "hsr", s["seg_id"], {"class": "highway"}, PLANNER)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM cleancity_log")


def test_recommended_bins_follow_spacing_and_skip_existing_ones(con):
    s = a_street(con, "primary")
    spacing = CC.standards()["classes"]["primary"]["bin_spacing_m"]
    on_street = [b for b in CC.recommend_bins(con, "hsr", "Sector 4") if b["seg_id"] == s["seg_id"]]
    assert len(on_street) == max(1, int(s["length_m"] // spacing))
    assert not [b for b in CC.recommend_bins(con, "hsr", "Sector 4") if b["class"] == "low_intensity"]
    first = on_street[0]
    CC.add_bin(con, "hsr", first["lon"], first["lat"], {}, PLANNER)
    after = [b for b in CC.recommend_bins(con, "hsr", "Sector 4") if b["seg_id"] == s["seg_id"]]
    assert len(after) == len(on_street) - 1


def test_bins_on_the_street_with_status_service_and_demand(con):
    s = a_street(con, "primary")
    lon, lat = s["coords"][0]
    b = CC.add_bin(con, "hsr", lon, lat, {"capacity_l": 240, "stream": "twin"}, PLANNER)
    assert b["bin_code"].startswith("BIN-") and b["seg_id"] and b["status"] == "normal" and b["service_due"]  # never serviced
    with pytest.raises(CC.CleanCityError):
        CC.add_bin(con, "hsr", lon, lat, {}, COLLECTOR)
    b = CC.update_bin(con, "hsr", b["id"], {"serviced": True}, COLLECTOR)
    assert not b["needs_collection"] and b["last_serviced"]
    assert CC.bin_demands(con, "hsr", "Sector 4") == []
    b = CC.update_bin(con, "hsr", b["id"], {"status": "overflowing"}, COLLECTOR)
    d = CC.bin_demands(con, "hsr", "Sector 4")
    assert len(d) == 1 and d[0]["is_bin"] and d[0]["demand"] == "public_bin" and d[0]["kg"]["wet"] > 0 and d[0]["kg"]["dry"] > 0
    with pytest.raises(CC.CleanCityError):
        CC.update_bin(con, "hsr", b["id"], {"capacity_l": 500}, COLLECTOR)  # details are the planner's
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM public_bin")


def test_workload_against_available_cleaning_staff(con):
    w = CC.workload(con, "hsr", "Sector 4")
    assert w["required_hours"] > 0 and w["parts"]["street_sweeping"] > 0 and not w["staff_known"]
    for i in range(3):
        R.add(con, "staff", "hsr", ["Sector 4"], {"worker_id": f"S{i}", "name": f"S {i}", "role": "sweeper", "home_sector": "Sector 4"},
              {"name": "H", "role": "hr_manager"})
    w2 = CC.workload(con, "hsr", "Sector 4")
    assert w2["available_hours"] == 24 and w2["gap_hours"] == round(24 - w2["required_hours"], 1)
    s = a_street(con)
    CC.plan_street(con, "hsr", s["seg_id"], {"cleanings_per_week": 42}, PLANNER)
    assert CC.workload(con, "hsr", "Sector 4")["parts"]["street_sweeping"] > w2["parts"]["street_sweeping"]


def test_a_plan_collects_bins_that_need_emptying(con):
    from backend.routing import twotier as T
    s = a_street(con, "primary")
    b = CC.add_bin(con, "hsr", *s["coords"][0], {"capacity_l": 240}, PLANNER)
    CC.update_bin(con, "hsr", b["id"], {"status": "full"}, COLLECTOR)
    r = T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                           primary_fleet=[{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}],
                           secondary_fleet=[], time_limit_s=5))
    served = {pid.split("#")[0] for v in r["primary"]["vehicles"] for t in v["trips"] for pid in t["point_ids"]}
    assert f"BIN:{b['id']}" in served


def test_cleancity_api(con):
    client = TestClient(app)
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    st = client.get("/api/pilots/hsr/cleancity/streets?sector=Sector 4").json()["streets"]
    seg = next(s for s in st if s["class"] == "residential")
    assert client.patch(f"/api/pilots/hsr/cleancity/streets/{seg['seg_id']}", json={"class": "commercial"}, headers=h).json()["class"] == "commercial"
    b = client.post("/api/pilots/hsr/cleancity/bins", json={"lon": seg["coords"][0][0], "lat": seg["coords"][0][1]}, headers=h).json()
    assert client.patch(f"/api/pilots/hsr/cleancity/bins/{b['id']}", json={"status": "full"}, headers=h).json()["needs_collection"]
    assert client.get("/api/pilots/hsr/cleancity/bins/recommended?sector=Sector 4").json()["count"] > 0
    assert client.get("/api/pilots/hsr/cleancity/workload?sector=Sector 4").json()["sectors"][0]["required_hours"] > 0
    assert client.get("/api/pilots/hsr/cleancity/tasks").json()["tasks"] == []
    cfg = client.get("/api/cleancity/config").json()
    assert cfg["rules"][0]["rule"] == "39(16)" and "market" in cfg["classes"]
    pts = client.get("/api/pilots/hsr/v2/points?sector=Sector 4").json()["points"]
    assert any(p["id"] == f"BIN:{b['id']}" for p in pts)
