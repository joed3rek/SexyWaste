"""Role registry and the dummy-login actor."""

import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.api.main import app

EXPECTED = {
    "planning": {"surveyor", "survey_supervisor", "planner", "fleet_workforce_manager"},
    "operations": {"driver", "collector", "operations_supervisor", "collection_contractor"},
    "processing": {"facility_operator", "recycler"},
    "governance": {"ward_officer", "trainer"},
    "public": {"generator"},
    "system": {"admin"},
}


def test_every_role_is_registered_in_its_module():
    by_module = {}
    for r in auth.roles():
        by_module.setdefault(r["module"], set()).add(r["key"])
    assert by_module == EXPECTED


def test_roles_have_valid_login_jurisdiction_and_text():
    reg = auth.registry()
    for r in reg["roles"]:
        assert r["login"] in reg["login_methods"]
        assert r["jurisdiction"] in ("city", "ward", "sector")
        assert r["label"] and r["description"]


def test_only_this_tasks_roles_are_built_and_they_have_a_home():
    built = {r["key"] for r in auth.roles() if r["built"]}
    assert built == {"surveyor", "survey_supervisor", "admin"}
    assert all(r.get("home") for r in auth.roles() if r["built"])


def test_actor_reads_role_and_name_and_is_never_verified():
    a = auth.actor({"x-swm-role": "surveyor", "x-swm-user": "Asha%20K", "x-swm-sectors": "Sector%203,Sector%204"})
    assert a == {"name": "Asha K", "role": "surveyor", "sectors": ["Sector 3", "Sector 4"], "verified": False}
    assert auth.actor({}) == {"name": None, "role": None, "sectors": [], "verified": False}
    with pytest.raises(KeyError):
        auth.actor({"x-swm-role": "mayor"})


def test_api_lists_roles_and_echoes_the_actor():
    client = TestClient(app)
    assert len(client.get("/api/roles").json()["roles"]) == 14
    me = client.get("/api/auth/me", headers={"X-SWM-Role": "admin", "X-SWM-User": "Ravi"}).json()
    assert me == {"name": "Ravi", "role": "admin", "sectors": [], "verified": False}
    assert client.get("/api/auth/me", headers={"X-SWM-Role": "mayor"}).status_code == 400
