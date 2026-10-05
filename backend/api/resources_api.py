"""Resource management API: vehicles, staff and equipment. Anyone signed in can read; the fleet
manager changes vehicles and machinery, the human resource manager changes staff, an admin both. Changes are logged with the name and role given
at sign-in (dummy login)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import resources as R
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors
from backend.config import PILOTS
from backend.regulations import swm

router = APIRouter()


def _sectors(pilot: str) -> list[str]:
    return list(pilot_sectors(pilot)["name"])


def _run(fn, *args):
    con = R.connect()
    try:
        return fn(con, *args)
    except R.ResourceError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _kind(kind: str) -> str:
    if kind not in R.KINDS:
        raise HTTPException(404, f"Unknown resource kind '{kind}'")
    return kind


def gps_rule(pilot: str) -> dict:
    """SWM Rules 2026, r. 8(h)(ix): collection vehicles need tracking devices above a city population."""
    r = swm()["collection_and_transport"]["vehicle_tracking"]
    pop = PILOTS[pilot].city_population
    return {"rule": r["rule"], "text": r["text"], "population_threshold": r["population_threshold"],
            "city_population": pop, "required": pop > r["population_threshold"]}


@router.get("/api/resources/config")
def resources_config():
    return {"statuses": R.STATUSES, "plannable": R.PLANNABLE, "shifts": R.SHIFTS, "staff_roles": R.STAFF_ROLES,
            "equipment_types": R.EQUIPMENT_TYPES, "conditions": R.CONDITIONS, "editors": {k: list(v) for k, v in R.EDITORS.items()},
            "vehicle_classes": [{"key": k, "label": c["label"], "tier": c["tier"], "crew": c.get("crew"),
                                 "specs": {f: (c.get(src) or {}).get("value") for f, (src, _) in R.VEHICLE_SPECS.items()}}
                                for k, c in R.vehicle_classes().items()]}


@router.get("/api/pilots/{pilot_key}/resources/available")
def resources_available(pilot_key: str, sector: str):
    pilot = _pilot(pilot_key)
    return {**_run(R.available, pilot, sector), "gps_rule": gps_rule(pilot)}


@router.get("/api/pilots/{pilot_key}/resources/{kind}")
def list_resources(pilot_key: str, kind: str, sector: str | None = None):
    pilot = _pilot(pilot_key)
    out = {"items": _run(R.items, _kind(kind), pilot, sector)}
    if kind == "vehicle":
        out["gps_rule"] = gps_rule(pilot)
    return out


@router.post("/api/pilots/{pilot_key}/resources/{kind}")
def add_resource(pilot_key: str, kind: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    return _run(R.add, _kind(kind), pilot, _sectors(pilot), body, _actor(request))


@router.patch("/api/pilots/{pilot_key}/resources/{kind}/{rid}")
def update_resource(pilot_key: str, kind: str, rid: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    return _run(R.update, _kind(kind), pilot, _sectors(pilot), rid, body, _actor(request))


@router.get("/api/pilots/{pilot_key}/resources/{kind}/{rid}/history")
def resource_history(pilot_key: str, kind: str, rid: str):
    pilot = _pilot(pilot_key)
    _run(R.get, _kind(kind), pilot, rid)
    return {"history": _run(R.history, kind, rid)}
