"""Round 1 survey API: visits, building fields, use mix, map problems, photos, spot-checks, history.

Every write needs a signed-in role and name (dummy login: headers X-SWM-Role, X-SWM-User and
X-SWM-Sectors) and records them. No endpoint edits or deletes a recorded field value.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend import auth
from backend.config import GVP_MAX_PHOTOS, GVP_RESPONSE_HOURS, PILOTS
from backend.survey import db, gvp, service, uses
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
        "gvp": {"statuses": db.GVP_STATUSES, "severities": db.GVP_SEVERITIES, "report_sources": db.GVP_REPORT_SOURCES,
                "frequencies": db.GVP_FREQUENCIES, "sources": db.GVP_SOURCES, "interventions": db.GVP_INTERVENTIONS,
                "sizes": list(gvp.size_kg()), "max_photos": GVP_MAX_PHOTOS, "response_hours": GVP_RESPONSE_HOURS,
                "managers": gvp.MANAGERS, "cleaners": gvp.CLEANERS, "rule": gvp.rule()},
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
    from backend.buildings import generators
    pilot = _pilot(pilot_key)
    queue = _run(service.spot_check_sample, pilot, _actor(request))
    base = generators._base_records_cached(pilot)
    for q in queue:
        b = base.get(q["building_id"], {})
        q["building_label"] = b.get("name") or b.get("address")
    return {"queue": queue}


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


@router.patch("/api/pilots/{pilot_key}/review-items/{item_id}")
def resolve_review_item(pilot_key: str, item_id: str, request: Request):
    _pilot(pilot_key)
    _run(service.resolve_review_item, item_id, _actor(request))
    return {"status": "resolved"}


@router.get("/api/pilots/{pilot_key}/surveyor/home")
def surveyor_home(pilot_key: str, request: Request):
    """Today's count for the signed-in person and progress in their sectors."""
    from backend.buildings import generators
    pilot = _pilot(pilot_key)
    actor = _actor(request)
    sectors = {bid: r["sector"] for bid, r in generators._base_records_cached(pilot).items()}
    summary = service.survey_summary(pilot, sectors)
    mine = set(actor["sectors"])
    return {"today": service.today_count(pilot, actor["name"]),
            "sectors": [r for r in summary["coverage_by_sector"] if r["sector"] in mine]}


# ---------- Sector assignments ----------

class AssignmentIn(BaseModel):
    surveyor_name: str = Field(min_length=1, max_length=80)
    sector: str


@router.get("/api/pilots/{pilot_key}/assignments")
def list_assignments(pilot_key: str, surveyor: str | None = None):
    return {"assignments": service.assignments(_pilot(pilot_key), surveyor)}


@router.post("/api/pilots/{pilot_key}/assignments")
def add_assignment(pilot_key: str, body: AssignmentIn, request: Request):
    return _run(service.assign_sector, _pilot(pilot_key), _actor(request), body.surveyor_name, body.sector)


@router.delete("/api/pilots/{pilot_key}/assignments/{assignment_id}")
def end_assignment(pilot_key: str, assignment_id: str, request: Request):
    """Ends an assignment (the row is kept with ended_at)."""
    _pilot(pilot_key)
    _run(service.end_assignment, assignment_id, _actor(request))
    return {"status": "ended"}


# ---------- Garbage mapping: garbage vulnerable points ----------

class GvpIn(BaseModel):
    lon: float
    lat: float
    streams: list[str]
    quantity_kg: float | None = None
    size: str | None = None  # the public's choice instead of kg: small, medium or large
    frequency: str
    severity: str = "medium"
    sources: list[str] = []
    landmark: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=500)


class GvpActionIn(BaseModel):
    action: str
    value: str | None = Field(None, max_length=120)
    kg: float | None = None
    note: str | None = Field(None, max_length=500)


@router.get("/api/pilots/{pilot_key}/gvps")
def list_gvps(pilot_key: str, sector: str | None = None):
    return {"gvps": _run(gvp.list_gvps, _pilot(pilot_key), sector), "rule": gvp.rule()}


@router.get("/api/pilots/{pilot_key}/gvps.geojson")
def gvps_geojson(pilot_key: str):
    """For publishing on the portal and the local body website (SWM Rules 2026, r. 15(1))."""
    return _run(gvp.geojson, _pilot(pilot_key))


@router.get("/api/pilots/{pilot_key}/gvps/tasks")
def gvp_cleaning_tasks(pilot_key: str, sector: str | None = None):
    """GVP -> Clean City: verified GVPs to clear, highest priority first."""
    return {"tasks": _run(gvp.cleaning_tasks, _pilot(pilot_key), sector)}


@router.get("/api/pilots/{pilot_key}/gvps/{gvp_id}")
def gvp_detail(pilot_key: str, gvp_id: str):
    return _run(gvp.detail, _pilot(pilot_key), gvp_id)


@router.post("/api/pilots/{pilot_key}/gvps")
def report_gvp(pilot_key: str, body: GvpIn, request: Request):
    """Report waste at a place: a new GVP, or a new report of one already mapped close by."""
    return _run(gvp.report, _pilot(pilot_key), _actor(request), body.lon, body.lat, body.streams, body.quantity_kg,
                body.frequency, body.sources, body.landmark, body.note, body.size, body.severity)


@router.post("/api/pilots/{pilot_key}/gvps/{gvp_id}/actions")
def gvp_action(pilot_key: str, gvp_id: str, body: GvpActionIn, request: Request):
    return _run(gvp.act, _pilot(pilot_key), gvp_id, _actor(request), body.action, body.value, body.kg, body.note)


@router.post("/api/pilots/{pilot_key}/gvp-reports/{report_id}/photos")
async def gvp_photo(pilot_key: str, report_id: str, request: Request, lon: float | None = None, lat: float | None = None):
    """A photo as the raw request body (image/jpeg, image/png or image/webp)."""
    actor = _actor(request)
    data = await request.body()
    return _run(gvp.add_photo, _pilot(pilot_key), report_id, actor, data, lon, lat)


@router.get("/api/pilots/{pilot_key}/gvp-photos/{photo_id}")
def gvp_photo_file(pilot_key: str, photo_id: str):
    path = _run(gvp.photo_file, _pilot(pilot_key), photo_id)
    data = path.read_bytes()
    kind = "image/png" if data[1:4] == b"PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return Response(content=data, media_type=kind)
