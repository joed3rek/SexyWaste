"""Processing API: facility intake, recovery and disposal, against capacity and the expected deliveries."""

from __future__ import annotations

from datetime import date as Date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend import facilities as F
from backend import processing as PR
from backend.api.survey_api import _actor, _pilot

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = PR.connect()
    try:
        return fn(con, *args, **kwargs)
    except (PR.ProcessingError, F.FacilityError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


@router.get("/api/processing/config")
def processing_config():
    return {"materials": PR.MATERIALS, "streams": PR.STREAMS, "bases": PR.BASES, "recorders": PR.RECORDERS}


@router.get("/api/pilots/{pilot_key}/processing")
def processing_summary(pilot_key: str, start: str | None = None, end: str | None = None):
    pilot = _pilot(pilot_key)
    try:
        end = D.parse_date(end)
        start = D.parse_date(start) if start else (Date.fromisoformat(end) - timedelta(days=6)).isoformat()
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    return _run(PR.summary, pilot, start, end)


@router.get("/api/pilots/{pilot_key}/facilities/{facility_id}/intake")
def facility_intake(pilot_key: str, facility_id: str, start: str, end: str):
    return {"intake": _run(PR.intakes, _pilot(pilot_key), start, end, facility_id)}


@router.post("/api/pilots/{pilot_key}/facilities/{facility_id}/intake")
def record_intake(pilot_key: str, facility_id: str, body: dict[str, Any], request: Request):
    return _run(PR.record, _pilot(pilot_key), facility_id, body, _actor(request))
