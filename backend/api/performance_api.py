"""Performance API: service required vs delivered, route accuracy, GVP response, and planning alerts."""

from __future__ import annotations

from datetime import date as Date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend import demand as D
from backend import performance as P
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _run(fn, *args, **kwargs):
    con = P.connect()
    try:
        return fn(con, *args, **kwargs)
    except (P.PerformanceError, D.DemandError) as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


def _period(pilot: str, start: str | None, end: str | None, sector: str | None) -> tuple[str, str, str | None]:
    if sector and sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    try:
        end = D.parse_date(end)
        start = D.parse_date(start) if start else (Date.fromisoformat(end) - timedelta(days=6)).isoformat()
    except D.DemandError as err:
        raise HTTPException(err.status, str(err)) from err
    return start, end, sector


@router.get("/api/pilots/{pilot_key}/performance")
def performance(pilot_key: str, start: str | None = None, end: str | None = None, sector: str | None = None):
    """Required vs delivered by sector and kind of work (default: the last 7 days), route accuracy and GVP response."""
    pilot = _pilot(pilot_key)
    a, b, s = _period(pilot, start, end, sector)
    try:
        gvps = P.gvp_response(pilot, a, b, s)
    except P.PerformanceError as err:
        raise HTTPException(err.status, str(err)) from err
    return {"service": _run(P.service, pilot, a, b, s), "routes": _run(P.route_accuracy, pilot, a, b, s), "gvps": gvps}


@router.get("/api/pilots/{pilot_key}/planning-alerts")
def list_alerts(pilot_key: str, status: str | None = "open", sector: str | None = None):
    pilot = _pilot(pilot_key)
    if status and status not in P.ALERT_STATUSES:
        raise HTTPException(422, f"status must be one of {P.ALERT_STATUSES}")
    return {"alerts": _run(P.alerts, pilot, status, sector)}


@router.post("/api/pilots/{pilot_key}/planning-alerts/scan")
def scan_alerts(pilot_key: str, request: Request, body: dict[str, Any] | None = None):
    pilot = _pilot(pilot_key)
    if _actor(request).get("role") not in P.PLANNERS:
        raise HTTPException(403, "Only a planner or an admin can scan for alerts.")
    days = int((body or {}).get("days", 14))
    if not 3 <= days <= 90:
        raise HTTPException(422, "days must be between 3 and 90.")
    return _run(P.scan, pilot, days)


@router.patch("/api/pilots/{pilot_key}/planning-alerts/{alert_id}")
def update_alert(pilot_key: str, alert_id: str, body: dict[str, Any], request: Request):
    return _run(P.set_alert, _pilot(pilot_key), alert_id, body.get("status"), _actor(request), body.get("note"))
