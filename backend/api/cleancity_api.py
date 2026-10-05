"""Clean City API: street inventory and cleaning plans, public bins, GVP clearing tasks, workforce."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import cleancity as CC
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors
from backend.survey import gvp

router = APIRouter()


def _run(fn, *args):
    con = CC.connect()
    try:
        return fn(con, *args)
    except CC.CleanCityError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


@router.get("/api/cleancity/config")
def cleancity_config():
    return {"classes": CC.CLASSES, "bin_statuses": CC.BIN_STATUSES, "bin_streams": CC.BIN_STREAMS, "conditions": CC.CONDITIONS,
            "planners": CC.PLANNERS, "bin_reporters": CC.BIN_REPORTERS, "standards": CC.standards(), "rules": CC.rules()}


@router.get("/api/pilots/{pilot_key}/cleancity/streets")
def list_streets(pilot_key: str, sector: str | None = None):
    return {"streets": _run(CC.streets, _pilot(pilot_key), sector)}


@router.patch("/api/pilots/{pilot_key}/cleancity/streets/{seg_id}")
def plan_street(pilot_key: str, seg_id: str, body: dict[str, Any], request: Request):
    return _run(CC.plan_street, _pilot(pilot_key), seg_id, body, _actor(request))


@router.get("/api/pilots/{pilot_key}/cleancity/bins")
def list_bins(pilot_key: str, sector: str | None = None):
    return {"bins": _run(CC.bins, _pilot(pilot_key), sector)}


@router.get("/api/pilots/{pilot_key}/cleancity/bins/recommended")
def recommended_bins(pilot_key: str, sector: str | None = None):
    rec = _run(CC.recommend_bins, _pilot(pilot_key), sector)
    return {"recommended": rec, "count": len(rec)}


@router.post("/api/pilots/{pilot_key}/cleancity/bins")
def add_bin(pilot_key: str, body: dict[str, Any], request: Request):
    try:
        lon, lat = float(body["lon"]), float(body["lat"])
    except (KeyError, TypeError, ValueError) as err:
        raise HTTPException(422, "lon and lat are required") from err
    return _run(CC.add_bin, _pilot(pilot_key), lon, lat, body, _actor(request))


@router.patch("/api/pilots/{pilot_key}/cleancity/bins/{bid}")
def update_bin(pilot_key: str, bid: str, body: dict[str, Any], request: Request):
    return _run(CC.update_bin, _pilot(pilot_key), bid, body, _actor(request))


@router.get("/api/pilots/{pilot_key}/cleancity/tasks")
def clearing_tasks(pilot_key: str, sector: str | None = None):
    """GVP clearing (Clean City): verified GVPs with the equipment the team needs, most severe first."""
    equip = CC.standards()["gvp_equipment"]
    return {"tasks": [{**t, "required_equipment": equip[t["severity"]]} for t in gvp.cleaning_tasks(_pilot(pilot_key), sector)]}


@router.get("/api/pilots/{pilot_key}/cleancity/workload")
def workload(pilot_key: str, sector: str | None = None):
    pilot = _pilot(pilot_key)
    names = [sector] if sector else list(pilot_sectors(pilot)["name"])
    return {"sectors": [_run(CC.workload, pilot, s) for s in names]}
