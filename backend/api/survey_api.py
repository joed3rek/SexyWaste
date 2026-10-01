"""Round 1 survey API: visits, building fields, use mix, map problems, photos, spot-checks, history.

Every write needs a signed-in role and name (dummy login: headers X-SWM-Role, X-SWM-User and
X-SWM-Sectors) and records them. No endpoint edits or deletes a recorded field value.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend import auth
from backend.config import PILOTS
from backend.survey import db, service, uses
from backend.survey.service import SurveyError

router = APIRouter()


def _pilot(pilot_key: str) -> str:
    if pilot_key not in PILOTS:
        raise HTTPException(404, f"Unknown pilot '{pilot_key}'")
    return pilot_key


def _actor(request: Request) -> dict:
    """The signed-in person; every write needs one."""
    try:
        a = auth.actor(request.headers)
    except KeyError as err:
        raise HTTPException(400, str(err)) from err
    if not a["role"] or not a["name"]:
        raise HTTPException(401, "Sign in first: choose your role and enter your name.")
    return a


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SurveyError as err:
        raise HTTPException(err.status, str(err)) from err


# ---------- Configuration for the survey screens ----------

@router.get("/api/survey/config")
def survey_config():
    cfg = uses.config()
    return {
        "building_uses": {k: {"label": v["label"], "default_use_mix": v["default_use_mix"], "ask_society_name": v["ask_society_name"],
                              "collection": uses.collection(k), "bwg_entity": uses.bwg_entity(k)}
                          for k, v in cfg["building_uses"].items()},
        "uses": cfg["uses"],
        "osm_hint": {k: v for k, v in cfg["osm_hint"].items() if not k.startswith("_")},
        "outcomes": db.OUTCOMES, "ends_visit": db.ENDS_VISIT,
        "building_fields": {k: (list(v) if isinstance(v, tuple) else v) for k, v in db.BUILDING_FIELDS.items()},
        "geometry_flag_kinds": db.FLAG_KINDS,
        "sources": db.SOURCES,
    }


# ---------- Visits ----------

class VisitIn(BaseModel):
    building_id: str | None = None
    purpose: str = "survey"
    spot_check_of: str | None = None
    lon: float | None = None
    lat: float | None = None
    accuracy_m: float | None = Field(None, ge=0)


class VisitClose(BaseModel):
    outcome: str
    notes: str | None = None


@router.post("/api/pilots/{pilot_key}/visits")
def open_visit(pilot_key: str, body: VisitIn, request: Request):
    return _run(service.open_visit, _pilot(pilot_key), _actor(request), body.building_id, body.purpose,
                body.spot_check_of, body.lon, body.lat, body.accuracy_m)


@router.patch("/api/pilots/{pilot_key}/visits/{visit_id}")
def close_visit(pilot_key: str, visit_id: str, body: VisitClose, request: Request):
    _pilot(pilot_key)
    return _run(service.close_visit, visit_id, _actor(request), body.outcome, body.notes)


class FieldsIn(BaseModel):
    fields: dict[str, Any]


@router.post("/api/pilots/{pilot_key}/visits/{visit_id}/building-fields")
@router.patch("/api/pilots/{pilot_key}/visits/{visit_id}/building-fields")
def building_fields(pilot_key: str, visit_id: str, body: FieldsIn, request: Request):
    _pilot(pilot_key)
    return _run(service.set_building_fields, visit_id, _actor(request), body.fields)


class UseMixIn(BaseModel):
    use: str
    count: float | None = None
    occupants_total: float | None = None
    beds_total: float | None = None


class UseMixUpdate(BaseModel):
    count: float | None = None
    occupants_total: float | None = None
    beds_total: float | None = None
    remove: bool = False


@router.post("/api/pilots/{pilot_key}/visits/{visit_id}/use-mix")
def add_use_mix(pilot_key: str, visit_id: str, body: UseMixIn, request: Request):
    _pilot(pilot_key)
    values = body.model_dump(exclude={"use"})
    return _run(service.add_use_mix, visit_id, _actor(request), body.use, values)


@router.patch("/api/pilots/{pilot_key}/visits/{visit_id}/use-mix/{row_id}")
def update_use_mix(pilot_key: str, visit_id: str, row_id: str, body: UseMixUpdate, request: Request):
    _pilot(pilot_key)
    return _run(service.update_use_mix, visit_id, _actor(request), row_id, body.model_dump(exclude={"remove"}), body.remove)


# ---------- Map problems and photos ----------

class FlagIn(BaseModel):
    kind: str
    building_id: str | None = None
    lon: float | None = None
    lat: float | None = None
    related_building_id: str | None = None
    visit_id: str | None = None
    note: str | None = Field(None, max_length=500)


@router.post("/api/pilots/{pilot_key}/geometry-flags")
def add_flag(pilot_key: str, body: FlagIn, request: Request):
    return _run(service.add_geometry_flag, _pilot(pilot_key), _actor(request), body.kind, body.building_id, body.lon,
                body.lat, body.related_building_id, body.visit_id, body.note)


@router.patch("/api/pilots/{pilot_key}/geometry-flags/{flag_id}")
def resolve_flag(pilot_key: str, flag_id: str, request: Request):
    _pilot(pilot_key)
    _run(service.resolve_geometry_flag, flag_id, _actor(request))
    return {"status": "resolved"}


@router.post("/api/pilots/{pilot_key}/visits/{visit_id}/photo")
async def add_photo(pilot_key: str, visit_id: str, request: Request, lon: float | None = None, lat: float | None = None):
    """The frontage photo as the raw request body (image/jpeg, image/png or image/webp)."""
    _pilot(pilot_key)
    actor = _actor(request)
    data = await request.body()
    return _run(service.add_photo, visit_id, actor, data, lon, lat)


# ---------- Reading ----------

@router.get("/api/pilots/{pilot_key}/spot-checks")
def spot_checks(pilot_key: str, request: Request):
    return {"queue": _run(service.spot_check_sample, _pilot(pilot_key), _actor(request))}


@router.get("/api/pilots/{pilot_key}/history")
def history(pilot_key: str, entity_type: str, entity_id: str):
    _pilot(pilot_key)
    if entity_type not in db.ENTITY_TYPES:
        raise HTTPException(422, f"entity_type must be one of {db.ENTITY_TYPES}")
    return {"entity_type": entity_type, "entity_id": entity_id, "history": service.history(entity_type, entity_id)}


@router.get("/api/pilots/{pilot_key}/review-items")
def review_items(pilot_key: str, status: str = "open"):
    """Contradictions, far-off GPS fixes, spot-check mismatches and open map problems to review."""
    import json
    pilot = _pilot(pilot_key)
    con = db.connect()
    try:
        items = [{**dict(r), "detail": json.loads(r["detail"])} for r in con.execute(
            "SELECT * FROM review_item WHERE pilot = ? AND status = ? ORDER BY created_at DESC", (pilot, status))]
        flags = [dict(r) for r in con.execute("SELECT * FROM geometry_flag WHERE pilot = ? AND status = ? ORDER BY created_at DESC",
                                              (pilot, status))]
    finally:
        con.close()
    return {"review_items": items, "geometry_flags": flags}
