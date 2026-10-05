"""Work plans API: a day's cleaning demands given to cleaning staff and equipment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend import workplan as W
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = W.connect()
    try:
        return fn(con, *args, **kwargs)
    except (W.WorkPlanError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _args(pilot: str, sector: str | None, date: str | None) -> tuple[str | None, str | None]:
    if sector and sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    try:
        return sector, (D.parse_date(date) if date else None)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err


@router.post("/api/pilots/{pilot_key}/work-plans")
def make_work_plan(pilot_key: str, body: dict[str, Any], request: Request):
    """Build and keep a draft work plan for a sector and date (planner or admin)."""
    pilot = _pilot(pilot_key)
    actor = _actor(request)
    if actor.get("role") not in W.PLANNERS:
        raise HTTPException(403, "Only a planner or an admin can make a work plan.")
    sector, day = _args(pilot, body.get("sector"), body.get("date"))
    if not sector:
        raise HTTPException(422, "Name the sector.")
    return _run(lambda con: W.save(con, pilot, W.build(con, pilot, sector, day or D.today()), actor))


@router.get("/api/pilots/{pilot_key}/work-plans")
def list_work_plans(pilot_key: str, sector: str | None = None, date: str | None = None):
    pilot = _pilot(pilot_key)
    return {"plans": _run(W.plans, pilot, *_args(pilot, sector, date))}


@router.get("/api/pilots/{pilot_key}/work-plans/{plan_id}")
def get_work_plan(pilot_key: str, plan_id: str):
    return _run(W.get, _pilot(pilot_key), plan_id)


@router.post("/api/pilots/{pilot_key}/work-plans/{plan_id}/adopt")
def adopt_work_plan(pilot_key: str, plan_id: str, request: Request, body: dict[str, Any] | None = None):
    return _run(W.adopt, _pilot(pilot_key), plan_id, _actor(request), bool((body or {}).get("accept_exceptions")))
