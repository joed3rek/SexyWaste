"""Facilities API: depots, truck yards, transfer stations, MRFs and other processing places."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import facilities as F
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _sector(pilot: str, sector: str | None) -> str | None:
    if sector and sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    return sector


def _run(fn, *args):
    con = F.connect()
    try:
        return fn(con, *args)
    except F.FacilityError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


@router.get("/api/facilities/config")
def facilities_config():
    return {"kinds": F.KINDS, "labels": F.LABEL, "statuses": F.STATUSES, "editors": F.EDITORS, "streams": F.STREAMS}


@router.get("/api/pilots/{pilot_key}/facilities")
def list_facilities(pilot_key: str, kind: str | None = None, sector: str | None = None, include_closed: bool = False):
    pilot = _pilot(pilot_key)
    return {"facilities": _run(F.items, pilot, kind, _sector(pilot, sector), include_closed)}


@router.post("/api/pilots/{pilot_key}/facilities")
def add_facility(pilot_key: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    _sector(pilot, body.get("sector"))
    return _run(F.add, pilot, body, _actor(request))


@router.patch("/api/pilots/{pilot_key}/facilities/{fid}")
def update_facility(pilot_key: str, fid: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    _sector(pilot, body.get("sector"))
    return _run(F.update, pilot, fid, body, _actor(request))


@router.get("/api/pilots/{pilot_key}/facilities/{fid}/history")
def facility_history(pilot_key: str, fid: str):
    pilot = _pilot(pilot_key)
    _run(F.get, pilot, fid)
    return {"history": _run(F.history, fid)}


@router.get("/api/pilots/{pilot_key}/plan-places")
def get_plan_places(pilot_key: str, sector: str):
    """The places a sector's route plan uses: depot, transfer stations, MRF and truck yard."""
    pilot = _pilot(pilot_key)
    return _run(F.plan_places, pilot, _sector(pilot, sector))


@router.put("/api/pilots/{pilot_key}/plan-places")
def put_plan_places(pilot_key: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    sector = _sector(pilot, body.get("sector"))
    if not sector:
        raise HTTPException(422, "Name the sector these places are for.")
    return _run(F.save_plan_places, pilot, sector, body, _actor(request))
