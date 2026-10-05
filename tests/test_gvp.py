"""Garbage mapping: GVP reports on the street network, severity, lifecycle, cleaning tasks,
collection demands for the route builder, and publishing."""

import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.config import CACHE_DIR, GVP_MAX_PHOTOS, GVP_MAX_ROAD_DISTANCE_M
from backend.routing import points as P
from backend.survey import db, gvp, service

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

SURVEYOR = {"name": "Asha", "role": "surveyor", "sectors": ["Sector 4"]}
SUPERVISOR = {"name": "Ravi", "role": "survey_supervisor", "sectors": ["Sector 4"]}
PUBLIC = {"name": "Kavya", "role": "generator", "sectors": ["Sector 1"]}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def _kerb_points(sector):
    """Places on the street (street-run collection points sit on the kerb line), well apart."""
    out = []
    for p in P.collection_points("hsr", {}, sector):
        if not p.get("is_bwg") and all(gvp._metres((p["lon"], p["lat"]), q) > 200 for q in out):
            out.append((p["lon"], p["lat"]))
    return out


@pytest.fixture()
def where(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    p = _kerb_points("Sector 4")[0]
    assert service.point_sector("hsr", *p) == "Sector 4"
    return p


def report(where, actor=SURVEYOR, **kw):
    a = {"streams": ["wet", "dry"], "quantity_kg": 60, "frequency": "daily", "sources": ["street_vendors"], "severity": "high", **kw}
    return gvp.report("hsr", actor, *where, a["streams"], a["quantity_kg"], a["frequency"], a["sources"],
                      landmark="Behind the bus stop", severity=a["severity"])


def move(g, action, actor=SUPERVISOR, **kw):
    return gvp.act("hsr", g["id"], actor, action, **kw)


# ---------- Reporting ----------

def test_a_report_maps_a_gvp_on_its_street_segment(where):
    g = report(where)
    assert g["status"] == "reported" and g["severity"] == "high" and g["created_source"] == "surveyor"
    assert g["seg_id"] and g["seg_id"].count("-") == 2 and g["road_name"] and g["sector"] == "Sector 4"
    assert g["snap_m"] <= 1 and g["reports"] == 1 and g["report_id"]


def test_a_pin_with_no_street_close_by_is_refused(where):
    from backend.survey.service import _sectors
    x0, y0, x1, y1 = _sectors("hsr").to_crs(4326).set_index("name").loc["Sector 1"].geometry.bounds
    off = next(((x, y) for x in np.linspace(x0, x1, 25) for y in np.linspace(y0, y1, 25)
                if service.point_sector("hsr", x, y) == "Sector 1" and gvp.snap_to_road("hsr", x, y)["snap_m"] > GVP_MAX_ROAD_DISTANCE_M), None)
    assert off, "no off-street place found in Sector 1"
    with pytest.raises(service.SurveyError, match="on a street"):
        gvp.report("hsr", PUBLIC, *off, ["dry"], 10, "daily")


def test_report_values_and_sector_rules_are_checked(where):
    for bad in ({"streams": []}, {"quantity_kg": 0}, {"frequency": "hourly"}, {"streams": ["plastic"]}, {"severity": "urgent"}):
        with pytest.raises(service.SurveyError):
            report(where, **bad)
    with pytest.raises(service.SurveyError) as err:
        report(where, actor={**SURVEYOR, "sectors": ["Sector 1"]})
    assert err.value.status == 403


def test_the_public_reports_anywhere_with_a_size_and_is_a_citizen_source(where):
    g = gvp.report("hsr", PUBLIC, *where, ["wet", "dry"], None, "few_times_a_week", size="medium", severity="low")
    assert g["created_source"] == "citizen" and g["quantity_kg"] == gvp.size_kg()["medium"] and g["status"] == "reported"
    assert gvp.source_of("collector") == "worker" and gvp.source_of("ward_officer") == "other"


def test_a_supervisors_own_report_is_verified_at_once(where):
    g = report(where, actor=SUPERVISOR)
    assert g["status"] == "verified" and g["created_source"] == "supervisor" and g["respond_by"]


def test_a_report_close_by_is_added_to_the_gvp_already_mapped(where):
    a = report(where)
    b = gvp.report("hsr", PUBLIC, where[0] + 0.00005, where[1], ["dry"], None, "weekly", size="small", severity="low")
    assert b["merged"] and b["id"] == a["id"] and b["reports"] == 2 and set(b["report_sources"]) == {"surveyor", "citizen"}
    assert b["severity"] == "high"  # the GVP's severity is set by a supervisor, not by later reports


def test_photos_per_report_with_time_place_and_reporter(where, tmp_path):
    g = report(where)
    rid = g["report_id"]
    with pytest.raises(service.SurveyError):
        gvp.add_photo("hsr", rid, SUPERVISOR, JPEG, photo_dir=tmp_path)
    for _ in range(GVP_MAX_PHOTOS):
        gvp.add_photo("hsr", rid, SURVEYOR, JPEG, lon=where[0], lat=where[1], photo_dir=tmp_path)
    with pytest.raises(service.SurveyError) as err:
        gvp.add_photo("hsr", rid, SURVEYOR, JPEG, photo_dir=tmp_path)
    assert err.value.status == 409
    d = gvp.detail("hsr", g["id"])
    photos = d["report_log"][0]["photos"]
    assert len(photos) == GVP_MAX_PHOTOS and photos[0]["user_name"] == "Asha" and photos[0]["taken_at"] and photos[0]["lon"]
    assert gvp.photo_file("hsr", photos[0]["id"]).exists()


def test_reports_events_and_demands_are_never_rewritten(where, tmp_path):
    g = report(where, actor=SUPERVISOR)  # one report, one event
    gvp.add_photo("hsr", g["report_id"], SUPERVISOR, JPEG, photo_dir=tmp_path)
    g = move(move(g, "assign", value="Team A"), "clear")  # one demand
    con = db.connect()
    for sql in ("UPDATE gvp_observation SET quantity_kg = 1", "DELETE FROM gvp_observation", "DELETE FROM gvp",
                "DELETE FROM gvp_event", "DELETE FROM gvp_demand", "DELETE FROM gvp_photo"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()


# ---------- Lifecycle ----------

def test_full_lifecycle_and_hand_offs(where):
    g = report(where)
    with pytest.raises(service.SurveyError):
        move(g, "verify", actor=SURVEYOR)
    with pytest.raises(service.SurveyError) as err:
        move(g, "start")  # not assigned yet
    assert err.value.status == 409
    g = move(g, "verify", value="critical")
    assert g["status"] == "verified" and g["severity"] == "critical" and g["respond_by"]
    assert [t["id"] for t in gvp.cleaning_tasks("hsr")] == [g["id"]]  # GVP -> Clean City
    assert gvp.collection_points("hsr", "Sector 4") == []  # not yet: nothing is piled up for pickup
    g = move(g, "assign", value="Sector 4 sweeping team")
    assert g["status"] == "assigned" and g["assigned_to"] == "Sector 4 sweeping team"
    g = move(g, "start")
    assert g["status"] == "cleaning"
    g = move(g, "clear", kg=80)
    assert g["status"] == "cleared" and g["pickup"]["kg"] == 80 and gvp.cleaning_tasks("hsr") == []
    pts = gvp.collection_points("hsr", "Sector 4")  # GVP -> route builder
    assert len(pts) == 1 and pts[0]["total_kg"] == 80 and pts[0]["demand"] == "gvp_pickup" and len(pts[0]["path_nodes"]) == 2
    g = move(g, "collected")
    assert g["status"] == "monitoring" and g["pickup"] is None and gvp.collection_points("hsr", "Sector 4") == []
    with pytest.raises(service.SurveyError):
        move(g, "collected")


def test_waste_back_at_a_monitored_gvp_marks_it_recurred(where):
    g = report(where, actor=SUPERVISOR)
    for action, kw in (("assign", {"value": "Team A"}), ("clear", {}), ("collected", {})):
        g = move(g, action, **kw)
    assert g["status"] == "monitoring"
    g = gvp.report("hsr", PUBLIC, *where, ["dry"], None, "daily", size="large", severity="medium")
    assert g["merged"] and g["status"] == "recurred" and g["recurrences"] == 1
    assert [t["id"] for t in gvp.cleaning_tasks("hsr")] == [g["id"]]
    g = move(g, "assign", value="Team B")
    assert g["status"] == "assigned"


def test_rejected_reports_are_not_tasks_or_published(where):
    g = gvp.report("hsr", PUBLIC, *where, ["dry"], None, "daily", size="small", severity="low")
    g = move(g, "reject", note="Private compound, not a public space")
    assert g["status"] == "rejected" and gvp.cleaning_tasks("hsr") == [] and gvp.geojson("hsr")["features"] == []
    with pytest.raises(service.SurveyError):
        move(g, "assign", value="Team A")


def test_cleaning_tasks_are_ordered_by_severity(where):
    places = _kerb_points("Sector 4")
    low = gvp.report("hsr", SUPERVISOR, *places[1], ["dry"], 10, "daily", severity="low")
    crit = gvp.report("hsr", SUPERVISOR, *places[2], ["wet"], 300, "daily", severity="critical")
    tasks = gvp.cleaning_tasks("hsr")
    assert [t["id"] for t in tasks] == [crit["id"], low["id"]]
    assert tasks[0]["priority"] == 4 and tasks[0]["respond_by"] < tasks[1]["respond_by"]


def test_interventions_and_severity_changes_are_logged(where):
    g = report(where)
    g = move(g, "bin_placed", note="Twin bin at the corner")
    g = move(g, "severity", value="medium")
    assert g["interventions"] == 1 and g["severity"] == "medium"
    assert [e["kind"] for e in g["events"]] == ["bin_placed", "severity"]


# ---------- Publishing and routes ----------

def test_geojson_publishes_with_the_rule(where):
    report(where)
    fc = gvp.geojson("hsr")
    assert fc["rule"]["rule"] == "15(1)" and fc["rule"]["deadline"]
    props = fc["features"][0]["properties"]
    assert props["seg_id"] and props["severity"] == "high" and props["status"] == "reported"


def test_a_plan_collects_cleared_gvp_waste(where):
    from backend.routing import twotier as T
    g = report(where, actor=SUPERVISOR)
    g = move(g, "assign", value="Team A")
    g = move(g, "clear", kg=120)
    r = T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                           primary_fleet=[{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}],
                           secondary_fleet=[], time_limit_s=5, park={"enabled": True}))
    served = {pid.split("#")[0] for v in r["primary"]["vehicles"] for t in v["trips"] for pid in t["point_ids"]}
    assert f"GVP:{g['id']}" in served


def test_gvp_api(where):
    client = TestClient(app)
    h = {"X-SWM-Role": "surveyor", "X-SWM-User": "Asha", "X-SWM-Sectors": "Sector 4"}
    sup = {"X-SWM-Role": "survey_supervisor", "X-SWM-User": "Ravi", "X-SWM-Sectors": "Sector 4"}
    body = {"lon": where[0], "lat": where[1], "streams": ["dry"], "quantity_kg": 25, "frequency": "daily", "severity": "medium"}
    assert client.post("/api/pilots/hsr/gvps", json=body).status_code == 401
    g = client.post("/api/pilots/hsr/gvps", json=body, headers=h).json()
    r = client.post(f"/api/pilots/hsr/gvp-reports/{g['report_id']}/photos", content=JPEG, headers={**h, "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    assert client.get(f"/api/pilots/hsr/gvp-photos/{r.json()['id']}").content == JPEG
    assert client.post(f"/api/pilots/hsr/gvps/{g['id']}/actions", json={"action": "verify"}, headers=h).status_code == 403
    assert client.post(f"/api/pilots/hsr/gvps/{g['id']}/actions", json={"action": "verify", "value": "high"}, headers=sup).json()["status"] == "verified"
    assert client.get("/api/pilots/hsr/gvps/tasks?sector=Sector 4").json()["tasks"][0]["severity"] == "high"
    assert client.get("/api/pilots/hsr/gvps.geojson").json()["features"][0]["properties"]["status"] == "verified"
    cfg = client.get("/api/survey/config").json()["gvp"]
    assert cfg["severities"] == ["low", "medium", "high", "critical"] and "monitoring" in cfg["statuses"]
