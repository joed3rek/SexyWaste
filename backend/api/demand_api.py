"""Service planning API: service requirements (read from the cycle, streets, bins and GVPs) and the
stored service demands they produce for a day."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _sectors(pilot: str, sector: str | None = None) -> list[str]:
    names = list(pilot_sectors(pilot)["name"])
    if sector and sector not in names:
        raise HTTPException(404, f"Unknown sector '{sector}'")
    return [sector] if sector else names


def _run(fn, *args, **kwargs):
    con = D.connect()
    try:
        return fn(con, *args, **kwargs)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _kind(kind: str | None) -> str | None:
    if kind and kind not in D.KINDS:
        raise HTTPException(422, f"kind must be one of {D.KINDS}")
    return kind


@router.get("/api/demand/config")
def demand_config():
    return {"kinds": D.KINDS, "sources": D.SOURCES, "statuses": D.STATUSES, "planners": D.PLANNERS, "today": D.today()}


@router.get("/api/pilots/{pilot_key}/service-requirements")
def get_requirements(pilot_key: str, sector: str | None = None, kind: str | None = None):
    pilot = _pilot(pilot_key)
    return {"requirements": _run(D.requirements, pilot, _sectors(pilot, sector), _kind(kind))}


@router.get("/api/pilots/{pilot_key}/service-demands")
def get_demands(pilot_key: str, date: str | None = None, sector: str | None = None, kind: str | None = None,
                status: str | None = None):
    """A day's demands (default today), plus GVP demands still waiting from earlier days."""
    pilot = _pilot(pilot_key)
    _sectors(pilot, sector)
    try:
        day = D.parse_date(date)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    statuses = tuple(status.split(",")) if status else D.ACTIVE
    if not set(statuses) <= set(D.STATUSES):
        raise HTTPException(422, f"status must be from {D.STATUSES}")
    rows = _run(D.demands, pilot, day, sector, _kind(kind), statuses)
    return {"date": day, "weekday": D.weekday(day), "summary": D.summary(rows), "demands": rows}


@router.post("/api/pilots/{pilot_key}/service-demands/generate")
def generate_demands(pilot_key: str, request: Request, body: dict[str, Any] | None = None):
    """Make or refresh the demands for a day (planner or admin). Safe to run again."""
    pilot = _pilot(pilot_key)
    actor = _actor(request)
    if actor.get("role") not in D.PLANNERS:
        raise HTTPException(403, "Only a planner or an admin can generate service demands.")
    body = body or {}
    try:
        day = D.parse_date(body.get("date"))
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    return _run(D.generate, pilot, day, _sectors(pilot, body.get("sector")), actor)


@router.get("/api/pilots/{pilot_key}/service-demands/{demand_id}")
def get_demand(pilot_key: str, demand_id: str):
    return _run(D.get, _pilot(pilot_key), demand_id)
