"""Collection cycle API: the planner's schedules of when each stream is collected from whom."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import cycle as C
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _sectors(pilot: str) -> list[str]:
    return list(pilot_sectors(pilot)["name"])


def _run(fn, *args):
    con = C.connect()
    try:
        return fn(con, *args)
    except C.CycleError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


@router.get("/api/cycle/config")
def cycle_config():
    return {"streams": C.STREAMS, "days": C.DAYS, "generators": C.GENERATORS, "methods": C.METHODS, "editors": C.EDITORS,
            "rules": C.rules(), "template": C.template()}


@router.get("/api/pilots/{pilot_key}/cycle")
def get_cycle(pilot_key: str):
    pilot = _pilot(pilot_key)
    sectors = _sectors(pilot)

    def read(con):
        return {"entries": C.entries(con, pilot), "checks": C.checks(con, pilot, sectors),
                "week": {s: C.week(con, pilot, s) for s in sectors}}
    return _run(read)


@router.get("/api/pilots/{pilot_key}/cycle/day")
def cycle_day(pilot_key: str, sector: str, day: str):
    return _run(C.day_plan, _pilot(pilot_key), sector, day)


@router.post("/api/pilots/{pilot_key}/cycle")
def add_entry(pilot_key: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    return _run(C.add, pilot, _sectors(pilot), body, _actor(request))


@router.post("/api/pilots/{pilot_key}/cycle/template")
def load_template(pilot_key: str, request: Request):
    pilot = _pilot(pilot_key)
    return {"entries": _run(C.load_template, pilot, _sectors(pilot), _actor(request))}


@router.patch("/api/pilots/{pilot_key}/cycle/{sid}")
def update_entry(pilot_key: str, sid: str, body: dict[str, Any], request: Request):
    pilot = _pilot(pilot_key)
    return _run(C.update, pilot, _sectors(pilot), sid, body, _actor(request))


@router.get("/api/pilots/{pilot_key}/cycle/{sid}/history")
def entry_history(pilot_key: str, sid: str):
    pilot = _pilot(pilot_key)
    _run(C.get, pilot, sid)
    return {"history": _run(C.history, sid)}
