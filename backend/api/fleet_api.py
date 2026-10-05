"""Fleet inventory API. Anyone signed in can read it; the fleet and workforce manager or an admin
can change it. Changes are logged with the name and role given at sign-in (dummy login)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend import fleet
from backend.api.survey_api import _actor, _pilot
from backend.buildings.layers import sectors as pilot_sectors

router = APIRouter()


def _sectors(pilot: str) -> list[str]:
    return list(pilot_sectors(pilot)["name"])


def _run(fn, *args):
    con = fleet.connect()
    try:
        return fn(con, *args)
    except fleet.FleetError as err:
        raise HTTPException(err.status, str(err)) from err
    finally:
        con.close()


class VehicleIn(BaseModel):
    model_config = {"extra": "forbid"}
    type: str | None = None
    registration: str | None = None
    home_sector: str | None = None
    status: str | None = None
    streams: list[str] | None = None
    payload_kg: float | None = None
    body_volume_m3: float | None = None
    vehicle_width_m: float | None = None
    min_road_width_m: float | None = None
    compartments: int | None = None
    notes: str | None = None
    verify: bool | None = None


@router.get("/api/pilots/{pilot_key}/fleet")
def list_fleet(pilot_key: str, sector: str | None = None):
    pilot = _pilot(pilot_key)
    return {"statuses": fleet.STATUSES, "vehicles": _run(fleet.vehicles, pilot, sector)}


@router.get("/api/pilots/{pilot_key}/fleet/available")
def fleet_available(pilot_key: str, sector: str):
    return _run(fleet.available, _pilot(pilot_key), sector)


@router.post("/api/pilots/{pilot_key}/fleet")
def add_vehicle(pilot_key: str, body: VehicleIn, request: Request):
    pilot = _pilot(pilot_key)
    return _run(fleet.add_vehicle, pilot, _sectors(pilot), body.model_dump(exclude_unset=True), _actor(request))


@router.patch("/api/pilots/{pilot_key}/fleet/{vehicle_id}")
def update_vehicle(pilot_key: str, vehicle_id: str, body: VehicleIn, request: Request):
    pilot = _pilot(pilot_key)
    return _run(fleet.update_vehicle, pilot, _sectors(pilot), vehicle_id, body.model_dump(exclude_unset=True), _actor(request))


@router.get("/api/pilots/{pilot_key}/fleet/{vehicle_id}/history")
def vehicle_history(pilot_key: str, vehicle_id: str):
    pilot = _pilot(pilot_key)
    _run(fleet.get_vehicle, pilot, vehicle_id)
    return {"history": _run(fleet.history, vehicle_id)}
