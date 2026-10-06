"""Operations API: record what actually happened to demands and routes; read the day's records."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend import operations as O
from backend import plans as PL
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = O.connect()
    try:
        return fn(con, *args, **kwargs)
    except (O.OperationError, D.DemandError, PL.PlanError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _args(pilot: str, sector: str | None, date: str | None) -> tuple[str | None, str]:
    if sector and sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    try:
        return sector, D.parse_date(date)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err


@router.get("/api/operations/config")
def operations_config():
    return {"outcomes": O.OUTCOMES, "reasons": O.REASONS, "bases": O.BASES, "recorders": O.RECORDERS}


@router.get("/api/pilots/{pilot_key}/operations/day")
def day_sheet(pilot_key: str, sector: str, date: str | None = None):
    """The adopted routes and cleaning demands of a day, with what has been recorded so far."""
    pilot = _pilot(pilot_key)
    sector, day = _args(pilot, sector, date)
    return _run(O.day_sheet, pilot, day, sector)


@router.get("/api/pilots/{pilot_key}/operations")
def list_operations(pilot_key: str, date: str | None = None, sector: str | None = None, subject: str | None = None,
                    all_records: bool = False):
    pilot = _pilot(pilot_key)
    sector, day = _args(pilot, sector, date)
    return {"records": _run(O.records, pilot, day, sector, subject, not all_records)}


@router.post("/api/pilots/{pilot_key}/operations/demands/{demand_id}")
def record_demand(pilot_key: str, demand_id: str, body: dict[str, Any], request: Request):
    return _run(O.record_demand, _pilot(pilot_key), demand_id, body, _actor(request))


@router.post("/api/pilots/{pilot_key}/operations/routes/{route_id}")
def record_route(pilot_key: str, route_id: str, body: dict[str, Any], request: Request):
    return _run(O.record_route, _pilot(pilot_key), route_id, body, _actor(request))
