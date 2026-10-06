"""Command centre API: one read of how the city is doing, what is failing, what needs attention today and why."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend import command as CMD
from backend import demand as D
from backend import performance as P
from backend.api.survey_api import _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


@router.get("/api/pilots/{pilot_key}/command")
def command_centre(pilot_key: str, date: str | None = None):
    pilot = _pilot(pilot_key)
    try:
        day = D.parse_date(date)
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    con = P.connect()
    try:
        return CMD.overview(con, pilot, list(pilot_sectors(pilot)["name"]), day)
    except (P.PerformanceError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()
