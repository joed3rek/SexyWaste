"""Route plans API: saved optimiser runs, their routes and stops, feasibility, and adoption."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend import plans as PL
from backend.api.survey_api import _actor, _pilot

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = PL.connect()
    try:
        return fn(con, *args, **kwargs)
    except (PL.PlanError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _day(value: str | None) -> str:
    try:
        return D.parse_date(value)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err


@router.get("/api/pilots/{pilot_key}/route-plans")
def list_plans(pilot_key: str, sector: str | None = None, date: str | None = None):
    return {"plans": _run(PL.plans, _pilot(pilot_key), sector, _day(date) if date else None)}


@router.get("/api/pilots/{pilot_key}/route-plans/{plan_id}")
def get_plan(pilot_key: str, plan_id: str):
    return _run(PL.get, _pilot(pilot_key), plan_id)


@router.post("/api/pilots/{pilot_key}/route-plans/{plan_id}/adopt")
def adopt_plan(pilot_key: str, plan_id: str, request: Request, body: dict[str, Any] | None = None):
    return _run(PL.adopt, _pilot(pilot_key), plan_id, _actor(request), bool((body or {}).get("accept_exceptions")))


@router.get("/api/pilots/{pilot_key}/routes")
def day_routes(pilot_key: str, date: str | None = None, sector: str | None = None):
    """The adopted routes of a day, and each vehicle's routes (derived from them)."""
    pilot, day = _pilot(pilot_key), _day(date)
    return {"date": day, "plans": _run(PL.adopted, pilot, day, sector), "by_vehicle": _run(PL.vehicle_routes, pilot, day)}
