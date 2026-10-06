"""Collection Intelligence Agent API: its status, runs, recommendations, approval and manual triggers."""

from __future__ import annotations

import threading
from datetime import date as Date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import agent as A
from backend import demand as D
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = A.connect()
    try:
        return fn(con, *args, **kwargs)
    except (A.AgentError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _planner(request: Request) -> dict:
    actor = _actor(request)
    if actor.get("role") not in A.APPROVERS:
        raise HTTPException(403, "Only a planner or an admin can ask the agent to plan or review.")
    return actor


def _work_in_background(pilot: str) -> None:
    def go():
        con = A.connect()
        try:
            A.work(con, pilot)
        finally:
            con.close()
    threading.Thread(target=go, name="cityloom-agent-manual", daemon=True).start()


@router.get("/api/pilots/{pilot_key}/agent")
def agent_status(pilot_key: str):
    return _run(A.status, _pilot(pilot_key))


@router.get("/api/pilots/{pilot_key}/agent/runs")
def agent_runs(pilot_key: str, date: str | None = None, status: str | None = None):
    return {"runs": _run(A.runs, _pilot(pilot_key), D.parse_date(date) if date else None, status)}


@router.get("/api/pilots/{pilot_key}/agent/runs/{run_id}")
def agent_run(pilot_key: str, run_id: str):
    return _run(A.get, _pilot(pilot_key), run_id)


@router.post("/api/pilots/{pilot_key}/agent/plan")
def agent_plan(pilot_key: str, request: Request, body: dict[str, Any] | None = None):
    """Ask the agent to plan a day (default tomorrow) for some or all sectors. Runs in the background."""
    pilot, body = _pilot(pilot_key), body or {}
    _planner(request)
    names = list(pilot_sectors(pilot)["name"])
    sectors = body.get("sectors") or names
    if not set(sectors) <= set(names):
        raise HTTPException(404, "Unknown sector.")
    try:
        day = D.parse_date(body.get("date") or (Date.today() + timedelta(days=1)).isoformat())
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    queued = _run(lambda con: sum(A.enqueue(con, pilot, "plan", s, day, "manual", "Asked from the Agent page") for s in sectors))
    _work_in_background(pilot)
    return {"date": day, "queued": queued}


@router.post("/api/pilots/{pilot_key}/agent/review")
def agent_review(pilot_key: str, request: Request, body: dict[str, Any] | None = None):
    pilot = _pilot(pilot_key)
    _planner(request)
    try:
        day = D.parse_date((body or {}).get("date"))
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    queued = _run(A.enqueue, pilot, "review", None, day, "manual", "Asked from the Agent page")
    _work_in_background(pilot)
    return {"date": day, "queued": int(queued)}


@router.post("/api/pilots/{pilot_key}/agent/runs/{run_id}/approve")
def agent_approve(pilot_key: str, run_id: str, request: Request, body: dict[str, Any] | None = None):
    body = body or {}
    return _run(A.decide, _pilot(pilot_key), run_id, _actor(request), True, body.get("plan_id"),
                bool(body.get("accept_exceptions")), body.get("note"))


@router.post("/api/pilots/{pilot_key}/agent/runs/{run_id}/reject")
def agent_reject(pilot_key: str, run_id: str, request: Request, body: dict[str, Any] | None = None):
    return _run(A.decide, _pilot(pilot_key), run_id, _actor(request), False, note=(body or {}).get("note"))
