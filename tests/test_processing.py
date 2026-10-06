"""Processing: facility intake (append-only), recovery and utilisation, observed dry fractions, capacity alerts,
and the planning-alert table migration."""

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import facilities as F
from backend import performance as PERF
from backend import processing as PR
from backend.api.main import app

PLANNER = {"name": "Arun", "role": "planner"}
OPERATOR = {"name": "Mani", "role": "facility_operator"}
D1, D2 = "2026-09-29", "2026-09-30"


@pytest.fixture()
def con():
    c = PR.connect()
    yield c
    c.close()


def mrf(con, cap=10):
    return F.add(con, "hsr", {"kind": "mrf", "lon": 77.63, "lat": 12.905, "capacity_t_day": cap}, PLANNER)


def test_intake_recovery_and_utilisation(con):
    m = mrf(con)
    PR.record(con, "hsr", m["id"], {"date": D1, "stream": "dry", "received_kg": 8000, "recovered": {"paper": 2000, "plastic": 1500},
                                    "rejects_kg": 1000, "disposal_kg": 1000}, OPERATOR)
    PR.record(con, "hsr", m["id"], {"date": D2, "stream": "dry", "received_kg": 12000, "recovered": {"paper": 3000}}, OPERATOR)
    s = next(f for f in PR.summary(con, "hsr", D1, D2)["facilities"] if f["facility_id"] == m["id"])
    assert s["received_kg"] == 20000 and s["days_recorded"] == 2 and s["utilisation_pct"] == 100.0
    assert s["days_over_capacity"] == [D2] and s["peak_t_day"] == 12.0
    assert s["recovery_pct"] == pytest.approx(32.5) and s["recovered_kg"]["paper"] == 5000
    assert s["dry_fractions_observed"]["paper"] == 0.25 and s["rejects_kg"] == 1000 and s["disposal_kg"] == 1000


def test_a_correction_replaces_and_nothing_is_edited(con):
    m = mrf(con)
    PR.record(con, "hsr", m["id"], {"date": D1, "stream": "dry", "received_kg": 5000}, OPERATOR)
    PR.record(con, "hsr", m["id"], {"date": D1, "stream": "dry", "received_kg": 6000}, OPERATOR)
    [r] = PR.intakes(con, "hsr", D1, D1, m["id"])
    assert r["received_kg"] == 6000
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM facility_intake")


def test_intake_is_checked(con):
    m = mrf(con)
    depot = F.add(con, "hsr", {"kind": "depot", "lon": 77.64, "lat": 12.91}, PLANNER)
    bad = [({"date": D1, "stream": "dry", "received_kg": 100, "recovered": {"paper": 200}}, m),
           ({"date": D1, "stream": "ash", "received_kg": 100}, m), ({"date": "yesterday", "stream": "dry", "received_kg": 1}, m),
           ({"date": D1, "stream": "dry", "received_kg": 100, "recovered": {"gold": 1}}, m),
           ({"date": D1, "stream": "dry", "received_kg": 100}, depot)]
    for data, fac in bad:
        with pytest.raises(PR.ProcessingError):
            PR.record(con, "hsr", fac["id"], data, OPERATOR)
    with pytest.raises(PR.ProcessingError) as e:
        PR.record(con, "hsr", m["id"], {"date": D1, "stream": "dry", "received_kg": 1}, {"name": "A", "role": "surveyor"})
    assert e.value.status == 403


def test_a_facility_over_capacity_becomes_a_planning_alert(con):
    m = mrf(con, cap=5)
    for i in range(3):
        day = (date.today() - timedelta(days=i)).isoformat()
        PR.record(con, "hsr", m["id"], {"date": day, "stream": "dry", "received_kg": 7000}, OPERATOR)
    assert PR.scan(con, "hsr") == 1 and PR.scan(con, "hsr") == 0
    [a] = [x for x in PERF.alerts(con, "hsr") if x["kind"] == "facility_over_capacity"]
    assert a["ref"] == m["id"] and len(a["facts"]["days"]) == 3


def test_an_old_alert_table_is_migrated_with_its_rows(tmp_path):
    path = tmp_path / "ops.db"
    c = sqlite3.connect(path)
    c.executescript(PERF._SCHEMA.replace(", 'facility_over_capacity'", ""))
    c.execute("INSERT INTO planning_alert (id, pilot, kind, ref, message, created_at, updated_at) VALUES ('a1', 'hsr', 'repeated_miss', 'x', 'm', 't', 't')")
    c.commit()
    c.close()
    con = PERF.connect(path)
    assert [a["id"] for a in PERF.alerts(con, "hsr")] == ["a1"]
    con.execute("INSERT INTO planning_alert (id, pilot, kind, ref, message, created_at, updated_at) VALUES ('a2', 'hsr', 'facility_over_capacity', 'y', 'm', 't', 't')")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM planning_alert")
    con.close()


def test_processing_api(con):
    m = mrf(con)
    client = TestClient(app)
    h = {"X-SWM-Role": "planner", "X-SWM-User": "Arun"}
    assert client.post(f"/api/pilots/hsr/facilities/{m['id']}/intake", json={"date": D1, "stream": "wet", "received_kg": 900}, headers=h).status_code == 200
    s = client.get(f"/api/pilots/hsr/processing?start={D1}&end={D2}").json()
    assert any(f["facility_id"] == m["id"] and f["received_kg"] == 900 for f in s["facilities"]) and "paper" in s["dry_fractions_assumed"]
    assert "compost" in client.get("/api/processing/config").json()["materials"]
