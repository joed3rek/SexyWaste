"""Garbage vulnerable points: mapping, observations, interventions, publishing and routes."""

import sqlite3

import numpy as np

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.config import CACHE_DIR
from backend.routing import points as P
from backend.survey import db, gvp, service

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "hsr.graphml").exists(), reason="HSR pilot data not cached")

SURVEYOR = {"name": "Asha", "role": "surveyor", "sectors": ["Sector 4"]}
SUPERVISOR = {"name": "Ravi", "role": "survey_supervisor", "sectors": ["Sector 4"]}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


PUBLIC = {"name": "Kavya", "role": "generator", "sectors": ["Sector 1"]}


def _kerb_points(sector):
    """Places on the road (street-run collection points sit on the kerb line), well apart."""
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


def report(where, **kw):
    args = {"streams": ["wet", "dry"], "quantity_kg": 60, "frequency": "daily", "sources": ["street_vendors"], **kw}
    return gvp.report("hsr", SURVEYOR, *where, args["streams"], args["quantity_kg"], args["frequency"], args["sources"],
                      landmark="Behind the bus stop")


def test_surveyor_maps_a_gvp_in_their_sector_with_a_first_observation(where):
    g = report(where)
    assert g["status"] == "active" and g["sector"] == "Sector 4" and g["on_routes"]
    assert g["kg_per_day"] == 60.0 and g["observations"] == 1
    with pytest.raises(service.SurveyError) as err:
        gvp.report("hsr", {**SURVEYOR, "sectors": ["Sector 1"]}, *where, ["dry"], 10, "daily")
    assert err.value.status == 403
    with pytest.raises(service.SurveyError):
        gvp.report("hsr", {"name": "P", "role": "planner", "sectors": ["Sector 4"]}, *where, ["dry"], 10, "daily")


def test_observation_values_are_checked(where):
    for bad in ({"streams": []}, {"quantity_kg": 0}, {"frequency": "hourly"}, {"streams": ["plastic"]}):
        with pytest.raises(service.SurveyError):
            report(where, **bad)


def test_accumulation_estimate_uses_the_build_up_frequency_from_norms(where):
    g = report(where, quantity_kg=70, frequency="weekly")
    assert g["kg_per_day"] == pytest.approx(70 * gvp.build_ups_per_day()["weekly"], abs=0.1)


def test_observations_are_added_beside_earlier_ones_and_never_changed(where):
    g = report(where)
    g = gvp.observe("hsr", g["id"], SURVEYOR, ["dry"], 20, "few_times_a_week")
    assert g["observations"] == 2 and g["quantity_kg"] == 20 and g["streams"] == ["dry"]
    assert [o["quantity_kg"] for o in g["observation_log"]] == [60, 20]
    con = db.connect()
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE gvp_observation SET quantity_kg = 1")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM gvp_observation")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM gvp")
    con.close()


def test_photo_once_by_the_observer(where, tmp_path):
    g = report(where)
    oid = g["observation_log"][0]["id"]
    with pytest.raises(service.SurveyError):
        gvp.add_photo("hsr", oid, SUPERVISOR, JPEG, photo_dir=tmp_path)
    gvp.add_photo("hsr", oid, SURVEYOR, JPEG, photo_dir=tmp_path)
    with pytest.raises(service.SurveyError) as err:
        gvp.add_photo("hsr", oid, SURVEYOR, JPEG, photo_dir=tmp_path)
    assert err.value.status == 409
    assert gvp.detail("hsr", g["id"])["observation_log"][0]["has_photo"]


def test_supervisor_records_interventions_status_and_route_flag(where):
    g = report(where)
    with pytest.raises(service.SurveyError):
        gvp.record_event("hsr", g["id"], SURVEYOR, "cleared")
    g = gvp.record_event("hsr", g["id"], SUPERVISOR, "bin_placed", note="Twin bin at the corner")
    assert g["interventions"] == 1 and g["on_routes"]
    g = gvp.record_event("hsr", g["id"], SUPERVISOR, "collect", "false")
    assert not g["on_routes"] and g["status"] == "active"
    g = gvp.record_event("hsr", g["id"], SUPERVISOR, "collect", "true")
    g = gvp.record_event("hsr", g["id"], SUPERVISOR, "status", "cleared")
    assert not g["on_routes"] and [e["kind"] for e in g["events"]] == ["bin_placed", "collect", "collect", "status"]
    with pytest.raises(service.SurveyError):
        gvp.record_event("hsr", g["id"], SUPERVISOR, "status", "gone")


def test_only_active_gvps_marked_for_collection_become_route_points(where):
    a = report(where)
    b = gvp.report("hsr", SURVEYOR, *_kerb_points("Sector 4")[1], ["dry"], 30, "daily")
    assert not b["merged"]
    gvp.record_event("hsr", b["id"], SUPERVISOR, "status", "closed")
    pts = gvp.collection_points("hsr", "Sector 4")
    assert [p["id"] for p in pts] == [f"GVP:{a['id']}"]
    assert pts[0]["kg"]["wet"] == 30 and pts[0]["kg"]["dry"] == 30 and pts[0]["is_gvp"]
    assert len(pts[0]["path_nodes"]) == 2  # the vehicle drives the road the GVP is on
    assert gvp.collection_points("hsr", "Sector 1") == []


def test_geojson_publishes_every_gvp_with_the_rule(where):
    report(where)
    fc = gvp.geojson("hsr")
    assert fc["rule"]["rule"] == "15(1)" and fc["rule"]["deadline"]
    assert len(fc["features"]) == 1 and fc["features"][0]["properties"]["kg_per_day"] == 60


def test_a_plan_collects_from_active_gvps(where):
    from backend.routing import twotier as T
    g = report(where, quantity_kg=80)
    r = T.plan(T.PlanInput(pilot="hsr", sector="Sector 4", depot=(77.6445, 12.9170), mrf=(77.6300, 12.9050),
                           primary_fleet=[{"type": "e_loader_3w", "count": 4}, {"type": "mini_tipper", "count": 2}],
                           secondary_fleet=[], time_limit_s=5, park={"enabled": True}))
    served = {pid.split("#")[0] for v in r["primary"]["vehicles"] for t in v["trips"] for pid in t["point_ids"]}
    assert f"GVP:{g['id']}" in served
    assert all(p != f"GVP:{g['id']}" for p in (c.get("from") for c in r["catchment"]))  # not sent to park composting


def test_gvp_api(where):
    client = TestClient(app)
    h = {"X-SWM-Role": "surveyor", "X-SWM-User": "Asha", "X-SWM-Sectors": "Sector 4"}
    body = {"lon": where[0], "lat": where[1], "streams": ["dry"], "quantity_kg": 25, "frequency": "daily", "landmark": "Corner"}
    assert client.post("/api/pilots/hsr/gvps", json=body).status_code == 401
    g = client.post("/api/pilots/hsr/gvps", json=body, headers=h).json()
    oid = g["observation_log"][0]["id"]
    r = client.post(f"/api/pilots/hsr/gvp-observations/{oid}/photo", content=JPEG, headers={**h, "Content-Type": "image/jpeg"})
    assert r.status_code == 200
    assert client.get("/api/pilots/hsr/gvps?sector=Sector 4").json()["gvps"][0]["landmark"] == "Corner"
    assert client.get("/api/pilots/hsr/gvps.geojson").json()["features"][0]["properties"]["kg_per_day"] == 25
    sup = {"X-SWM-Role": "survey_supervisor", "X-SWM-User": "Ravi", "X-SWM-Sectors": "Sector 4"}
    assert client.post(f"/api/pilots/hsr/gvps/{g['id']}/events", json={"kind": "signage"}, headers=sup).json()["interventions"] == 1
    assert "gvp" in client.get("/api/survey/config").json()
    pts = client.get("/api/pilots/hsr/v2/points?sector=Sector 4").json()["points"]
    assert any(p["id"] == f"GVP:{g['id']}" for p in pts)


def test_a_gvp_is_on_a_road(where):
    g = report(where)
    assert g["road_u"] and g["road_v"] and g["snap_m"] <= 1
    # A place in Sector 1 more than 30 m from any road (Sector 1 takes in open land).
    from backend.config import GVP_MAX_ROAD_DISTANCE_M
    from backend.survey.service import _sectors
    x0, y0, x1, y1 = _sectors("hsr").to_crs(4326).set_index("name").loc["Sector 1"].geometry.bounds
    off = next(((x, y) for x in np.linspace(x0, x1, 25) for y in np.linspace(y0, y1, 25)
                if service.point_sector("hsr", x, y) == "Sector 1" and gvp.snap_to_road("hsr", x, y)["snap_m"] > GVP_MAX_ROAD_DISTANCE_M), None)
    assert off, "no off-road place found in Sector 1"
    with pytest.raises(service.SurveyError, match="on a road"):
        gvp.report("hsr", PUBLIC, *off, ["dry"], 10, "daily")


def test_a_report_close_to_an_open_gvp_is_added_to_it(where):
    a = report(where)
    gvp.record_event("hsr", a["id"], SUPERVISOR, "status", "cleared")
    near = (where[0] + 0.00005, where[1])  # about 5 m along
    b = gvp.report("hsr", SURVEYOR, *near, ["dry"], 15, "weekly")
    assert b["merged"] and b["id"] == a["id"] and b["observations"] == 2
    assert b["status"] == "active" and b["events"][-1]["note"] == "Reported again"


def test_the_public_reports_anywhere_in_the_pilot_with_a_size(where):
    g = gvp.report("hsr", PUBLIC, *where, ["wet", "dry"], None, "few_times_a_week", size="medium")  # Sector 4, home is Sector 1
    assert g["reported_by_public"] and g["quantity_kg"] == gvp.size_kg()["medium"] and g["on_routes"]
    with pytest.raises(service.SurveyError):
        gvp.report("hsr", PUBLIC, *where, ["dry"], None, "daily", size="huge")
    with pytest.raises(service.SurveyError):
        gvp.record_event("hsr", g["id"], PUBLIC, "cleared")
