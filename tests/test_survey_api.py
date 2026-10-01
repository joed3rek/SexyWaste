"""Round 1 survey API, end to end, on a temporary survey database. Needs the cached HSR data."""

import pytest
from fastapi.testclient import TestClient

from backend.config import CACHE_DIR

pytestmark = pytest.mark.skipif(not (CACHE_DIR / "pilots" / "hsr" / "buildings.geojson").exists(),
                                reason="HSR pilot data not cached")

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api.main import app
    from backend.survey import db, service
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "survey.db")
    monkeypatch.setattr(service, "PHOTO_DIR", tmp_path / "photos")
    return TestClient(app)


@pytest.fixture(scope="module")
def buildings():
    from backend.buildings import generators
    recs = generators._base_records_cached("hsr")
    by_sector = {}
    for bid, r in recs.items():
        by_sector.setdefault(r["sector"], []).append(bid)
    return by_sector


def who(name, role, *sectors):
    return {"X-SWM-User": name, "X-SWM-Role": role, "X-SWM-Sectors": ",".join(s.replace(" ", "%20") for s in sectors)}


ASHA = who("Asha", "surveyor", "Sector 3")
RAVI = who("Ravi", "surveyor", "Sector 3")
MEENA = who("Meena", "survey_supervisor", "Sector 3")


def survey(client, bid, headers=ASHA, building_use="independent_house", mix=(("residential_dwelling", {"count": 1}),),
           outcome="completed"):
    v = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=headers).json()["visit"]
    client.post(f"/api/pilots/hsr/visits/{v['id']}/building-fields", json={"fields": {"building_use": building_use}}, headers=headers)
    for use, vals in mix:
        client.post(f"/api/pilots/hsr/visits/{v['id']}/use-mix", json={"use": use, **vals}, headers=headers)
    client.patch(f"/api/pilots/hsr/visits/{v['id']}", json={"outcome": outcome}, headers=headers)
    return v["id"]


# ---------- Sign-in and authorisation ----------

def test_writes_need_a_signed_in_person(client, buildings):
    bid = buildings["Sector 3"][0]
    assert client.post("/api/pilots/hsr/visits", json={"building_id": bid}).status_code == 401
    assert client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers={"X-SWM-Role": "surveyor"}).status_code == 401
    assert client.post("/api/pilots/hsr/geometry-flags", json={"kind": "wrong_shape", "building_id": bid}).status_code == 401


def test_surveyor_cannot_open_a_visit_outside_their_sectors(client, buildings):
    r = client.post("/api/pilots/hsr/visits", json={"building_id": buildings["Sector 1"][0]}, headers=ASHA)
    assert r.status_code == 403 and "Sector 1" in r.json()["detail"] and "not one of your sectors" in r.json()["detail"]


def test_requests_are_checked_against_the_active_role(client, buildings):
    bid = buildings["Sector 3"][1]
    as_surveyor = who("Kiran", "surveyor", "Sector 3")
    as_supervisor = who("Kiran", "survey_supervisor", "Sector 3")
    assert client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=as_surveyor).status_code == 200
    r = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=as_supervisor)
    assert r.status_code == 403 and "Only a surveyor" in r.json()["detail"]


def test_only_the_person_who_opened_a_visit_records_in_it(client, buildings):
    v = client.post("/api/pilots/hsr/visits", json={"building_id": buildings["Sector 3"][2]}, headers=ASHA).json()["visit"]
    r = client.post(f"/api/pilots/hsr/visits/{v['id']}/building-fields", json={"fields": {"floors": 2}}, headers=RAVI)
    assert r.status_code == 403


# ---------- Recording a building ----------

def test_full_quick_visit_records_values_with_source(client, buildings):
    bid = buildings["Sector 3"][3]
    v = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=ASHA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{v['id']}"
    r = client.post(url + "/building-fields", json={"fields": {"building_use": "mixed_use_shops_below", "floors": 3,
                                                               "society_name": "Lake View", "collection_arrangement": "municipal_door_to_door",
                                                               "segregation_reported": "wet_dry_only", "home_compost": "some"}}, headers=ASHA)
    assert r.status_code == 200
    for use, vals in (("residential_dwelling", {"count": 4, "occupants_total": 12}), ("pg_coliving", {"count": 1, "beds_total": 30}),
                      ("shop_retail", {"count": 2}), ("food_service", {"count": 1})):
        assert client.post(url + "/use-mix", json={"use": use, **vals}, headers=ASHA).status_code == 200
    closed = client.patch(url, json={"outcome": "completed"}, headers=ASHA).json()
    assert closed["warnings"] == [] and closed["visit"]["outcome"] == "completed"
    card = client.get(f"/api/pilots/hsr/building?id={bid}").json()
    assert card["building_use"] == "mixed_use_shops_below" and card["kg_day"] == pytest.approx(37.0, abs=0.01)
    f = card["survey_detail"]["fields"]
    assert f["floors"]["value"] == 3 and f["floors"]["source"] == "surveyed" and f["floors"]["recorded_by"] == "Asha"
    assert len(card["survey_detail"]["use_mix"]) == 4 and card["survey_detail"]["visits"][0]["user_role"] == "surveyor"


def test_contradiction_warns_saves_and_is_logged_for_the_supervisor(client, buildings):
    bid = buildings["Sector 3"][4]
    v = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=ASHA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{v['id']}"
    client.post(url + "/building-fields", json={"fields": {"building_use": "independent_house"}}, headers=ASHA)
    client.post(url + "/use-mix", json={"use": "residential_dwelling", "count": 1}, headers=ASHA)
    r = client.post(url + "/use-mix", json={"use": "pg_coliving", "count": 1, "beds_total": 30}, headers=ASHA)
    assert r.status_code == 200 and r.json()["warnings"][0]["code"] == "warn_over" and "30 beds" in r.json()["warnings"][0]["message"]
    client.patch(url, json={"outcome": "completed"}, headers=ASHA)
    items = client.get("/api/pilots/hsr/review-items").json()["review_items"]
    assert [i["kind"] for i in items if i["building_id"] == bid] == ["use_mix_contradiction"]


def test_bad_values_are_rejected(client, buildings):
    v = client.post("/api/pilots/hsr/visits", json={"building_id": buildings["Sector 3"][5]}, headers=ASHA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{v['id']}"
    assert client.post(url + "/building-fields", json={"fields": {"building_use": "spaceship"}}, headers=ASHA).status_code == 422
    assert client.post(url + "/building-fields", json={"fields": {"floors": -2}}, headers=ASHA).status_code == 422
    assert client.post(url + "/building-fields", json={"fields": {"water_lpd": 100}}, headers=ASHA).status_code == 422
    assert client.post(url + "/use-mix", json={"use": "shop_retail", "beds_total": 3}, headers=ASHA).status_code == 422
    client.patch(url, json={"outcome": "locked"}, headers=ASHA)
    assert client.post(url + "/building-fields", json={"fields": {"floors": 2}}, headers=ASHA).status_code == 409


def test_use_mix_row_is_removed_not_deleted(client, buildings):
    bid = buildings["Sector 3"][6]
    v = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=ASHA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{v['id']}"
    row = client.post(url + "/use-mix", json={"use": "office", "count": 2}, headers=ASHA).json()["id"]
    client.patch(url + f"/use-mix/{row}", json={"count": 3}, headers=ASHA)
    client.patch(url + f"/use-mix/{row}", json={"remove": True}, headers=ASHA)
    assert client.get(f"/api/pilots/hsr/building?id={bid}").json()["survey_detail"]["use_mix"] == []
    hist = client.get(f"/api/pilots/hsr/history?entity_type=use_mix&entity_id={row}").json()["history"]
    assert [h["value"] for h in hist] == [2, 3]


# ---------- Spot-checks ----------

def test_supervisor_cannot_spot_check_their_own_visit(client, buildings):
    vid = survey(client, buildings["Sector 3"][7], headers=who("Meena", "surveyor", "Sector 3"))
    r = client.post("/api/pilots/hsr/visits", json={"purpose": "spot_check", "spot_check_of": vid}, headers=MEENA)
    assert r.status_code == 403 and "own visit" in r.json()["detail"]


def test_surveyor_cannot_spot_check(client, buildings):
    vid = survey(client, buildings["Sector 3"][8])
    assert client.post("/api/pilots/hsr/visits", json={"purpose": "spot_check", "spot_check_of": vid}, headers=RAVI).status_code == 403


def test_spot_check_verifies_matches_keeps_history_and_reports_building_use_mismatch(client, buildings):
    bid = buildings["Sector 3"][9]
    vid = survey(client, bid, building_use="apartment_society", mix=(("residential_dwelling", {"count": 12}),))
    check = client.post("/api/pilots/hsr/visits", json={"purpose": "spot_check", "spot_check_of": vid}, headers=MEENA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{check['id']}"
    assert check["building_id"] == bid
    same = client.post(url + "/use-mix", json={"use": "residential_dwelling", "count": 12}, headers=MEENA).json()
    assert same["mismatches"] == []
    diff = client.post(url + "/building-fields", json={"fields": {"building_use": "pg_coliving_building"}}, headers=MEENA).json()
    assert diff["mismatches"]
    client.patch(url, json={"outcome": "completed"}, headers=MEENA)
    detail = client.get(f"/api/pilots/hsr/building?id={bid}").json()["survey_detail"]
    row = detail["use_mix"][0]
    assert row["fields"]["count"]["source"] == "verified"
    hist = client.get(f"/api/pilots/hsr/history?entity_type=use_mix&entity_id={row['id']}").json()["history"]
    assert [h["source"] for h in hist] == ["surveyed", "verified"], "the verified value keeps the surveyed one it confirms"
    kinds = [i["kind"] for i in client.get("/api/pilots/hsr/review-items").json()["review_items"]]
    assert "spot_check_building_use_mismatch" in kinds
    s = client.get("/api/pilots/hsr/summary").json()["survey"]["spot_checks_by_surveyor"]
    asha = next(x for x in s if x["surveyor"] == "Asha")
    assert asha["spot_checks"] == 1 and asha["building_use_mismatches"] == 1


def test_spot_check_queue_samples_each_surveyor_and_skips_own_visits(client, buildings):
    ids = buildings["Sector 3"][10:16]
    for bid in ids[:4]:
        survey(client, bid, headers=ASHA)
    survey(client, ids[4], headers=who("Meena", "surveyor", "Sector 3"))
    queue = client.get("/api/pilots/hsr/spot-checks", headers=MEENA).json()["queue"]
    assert {q["surveyor"] for q in queue} == {"Asha"}, "own visits are never in the queue"
    assert len(queue) == 1, "5% of 4 visits rounds up to one"
    assert client.get("/api/pilots/hsr/spot-checks", headers=MEENA).json()["queue"] == queue, "same sample all week"
    assert client.get("/api/pilots/hsr/spot-checks", headers=ASHA).status_code == 403


# ---------- Coverage ----------

def test_refused_and_locked_count_as_visited_separately_from_not_visited(client, buildings):
    ids = buildings["Sector 3"][20:23]
    survey(client, ids[0])
    for bid, outcome in ((ids[1], "refused"), (ids[2], "locked")):
        v = client.post("/api/pilots/hsr/visits", json={"building_id": bid}, headers=ASHA).json()["visit"]
        client.patch(f"/api/pilots/hsr/visits/{v['id']}", json={"outcome": outcome}, headers=ASHA)
    cov = client.get("/api/pilots/hsr/summary").json()["survey"]
    s3 = next(r for r in cov["coverage_by_sector"] if r["sector"] == "Sector 3")
    assert (s3["completed"], s3["refused"], s3["locked"]) == (1, 1, 1)
    assert s3["not_visited"] == s3["buildings"] - 3
    assert cov["coverage"]["visited"] == 3 and cov["building_use_counts"] == {"independent_house": 1}


# ---------- Map problems and photos ----------

def test_map_problems_including_a_missing_building(client, buildings):
    from backend.buildings import generators
    bid = buildings["Sector 3"][30]
    assert client.post("/api/pilots/hsr/geometry-flags", json={"kind": "should_be_split", "building_id": bid}, headers=ASHA).status_code == 200
    g = generators.base_table("hsr").set_index("id").loc[bid, "geometry"].representative_point()
    r = client.post("/api/pilots/hsr/geometry-flags", json={"kind": "missing_from_map", "lon": g.x, "lat": g.y}, headers=ASHA)
    assert r.status_code == 200 and r.json()["building_id"] is None
    assert client.post("/api/pilots/hsr/geometry-flags", json={"kind": "missing_from_map"}, headers=ASHA).status_code == 422
    flags = client.get("/api/pilots/hsr/review-items").json()["geometry_flags"]
    assert len(flags) == 2
    assert client.patch(f"/api/pilots/hsr/geometry-flags/{flags[0]['id']}", headers=ASHA).status_code == 403
    assert client.patch(f"/api/pilots/hsr/geometry-flags/{flags[0]['id']}", headers=MEENA).status_code == 200


def test_one_frontage_photo_per_visit_with_limits(client, buildings, monkeypatch):
    from backend.survey import service
    v = client.post("/api/pilots/hsr/visits", json={"building_id": buildings["Sector 3"][31]}, headers=ASHA).json()["visit"]
    url = f"/api/pilots/hsr/visits/{v['id']}/photo"
    assert client.post(url, content=b"not an image", headers=ASHA).status_code == 415
    monkeypatch.setattr(service, "PHOTO_MAX_BYTES", 50)
    assert client.post(url, content=JPEG, headers=ASHA).status_code == 413
    monkeypatch.setattr(service, "PHOTO_MAX_BYTES", 10_000)
    r = client.post(url, content=JPEG, headers=ASHA)
    assert r.status_code == 200 and len(r.json()["sha256"]) == 64
    assert client.post(url, content=JPEG, headers=ASHA).status_code == 409


def test_gps_far_from_the_footprint_warns_but_opens(client, buildings):
    bid = buildings["Sector 3"][32]
    from backend.buildings import generators
    g = generators.base_table("hsr").set_index("id").loc[bid, "geometry"].representative_point()
    r = client.post("/api/pilots/hsr/visits", json={"building_id": bid, "lon": g.x + 0.002, "lat": g.y}, headers=ASHA)
    assert r.status_code == 200 and "m from this building" in r.json()["warnings"][0]
    near = client.post("/api/pilots/hsr/visits", json={"building_id": bid, "lon": g.x, "lat": g.y}, headers=ASHA).json()
    assert near["warnings"] == []
