"""FastAPI app: pilot building intelligence, Route Builder and regulations APIs, plus the static frontend.

Run from the repository root:
    .venv\\Scripts\\python -m uvicorn backend.api.main:app --reload
then open http://127.0.0.1:8000
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import auth, cycle, demand, plans, regulations, resources
from backend.buildings import generators
from backend.buildings.layers import sectors as pilot_sectors
from backend.config import FRONTEND_DIR, PILOTS, WASTE_STREAMS
from backend.routing import points as points_v2
from backend.routing import parks, twotier
from backend.routing.network import node_lonlat
from backend.api.cleancity_api import router as cleancity_router
from backend.api.cycle_api import router as cycle_router
from backend.api.demand_api import router as demand_router
from backend.api.facilities_api import router as facilities_router
from backend.api.plans_api import router as plans_router
from backend.api.workplan_api import router as workplan_router
from backend.api.operations_api import router as operations_router
from backend.api.performance_api import router as performance_router
from backend.api.resources_api import router as resources_router
from backend.api.survey_api import router as survey_router
from backend.survey import service as survey_service
from backend.survey import state as survey_state

@asynccontextmanager
async def lifespan(_app: FastAPI):
    survey_service.purge_old_photos()  # photo retention (backend/config.py)
    yield


app = FastAPI(title="SWM Urban Waste Intelligence", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2000)
app.include_router(survey_router)
app.include_router(resources_router)
app.include_router(cycle_router)
app.include_router(cleancity_router)
app.include_router(demand_router)
app.include_router(facilities_router)
app.include_router(plans_router)
app.include_router(workplan_router)
app.include_router(operations_router)
app.include_router(performance_router)


@app.get("/api/landing/photos")
def landing_photos():
    """Cover photos for the opening page: the images in frontend/img/hero/ (licensed photos only)."""
    folder = FRONTEND_DIR / "img" / "hero"
    files = sorted(f.name for f in folder.glob("*") if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) if folder.exists() else []
    return {"photos": [f"img/hero/{name}" for name in files]}


def _pilot(pilot_key: str) -> str:
    if pilot_key not in PILOTS:
        raise HTTPException(404, f"Unknown pilot '{pilot_key}'")
    return pilot_key


# ---------- Roles and the acting user (dummy login) ----------

@app.get("/api/roles")
def list_roles():
    return auth.registry()


@app.get("/api/auth/me")
def who_am_i(request: Request):
    """Echo the role and name the browser sent. Login is a dummy: nothing is verified."""
    try:
        return auth.actor(request.headers)
    except KeyError as err:
        raise HTTPException(400, str(err)) from err


# ---------- Pilot building intelligence ----------

@app.get("/api/pilots")
def list_pilots():
    return [{"key": p.key, "name": p.name} for p in PILOTS.values()]


@app.get("/api/pilots/{pilot_key}/sectors")
def get_sectors(pilot_key: str):
    return generators.sectors_geojson(_pilot(pilot_key))


@app.get("/api/pilots/{pilot_key}/landuse")
def get_landuse(pilot_key: str):
    return generators.landuse_geojson(_pilot(pilot_key))


@app.get("/api/pilots/{pilot_key}/buildings")
def get_buildings(pilot_key: str):
    return Response(generators.buildings_geojson_text(_pilot(pilot_key)), media_type="application/json")


@app.get("/api/pilots/{pilot_key}/summary")
def get_summary(pilot_key: str):
    pilot = _pilot(pilot_key)
    out = generators.summary(pilot, survey_state.building_states(pilot))
    sectors = {bid: r["sector"] for bid, r in generators._base_records_cached(pilot).items()}
    out["survey"] = survey_service.survey_summary(pilot, sectors)
    return out


@app.get("/api/pilots/{pilot_key}/overrides")
def get_overrides(pilot_key: str):
    """Map properties of surveyed buildings, replacing the OSM-only values in the buildings layer."""
    pilot = _pilot(pilot_key)
    return {
        bid: generators.map_properties(generators.building_properties(pilot, bid, st))
        for bid, st in survey_state.building_states(pilot).items()
        if generators.has_building(pilot, bid)
    }


def _building_id(pilot: str, building_id: str) -> str:
    if not generators.has_building(pilot, building_id):
        raise HTTPException(404, f"Unknown building '{building_id}'")
    return building_id


@app.get("/api/pilots/{pilot_key}/building")
def get_building(pilot_key: str, id: str):
    """Full building card: OSM base, computed fields, and the survey detail (each field's current
    value and source, use mix, visits, open map problems, contradictions)."""
    pilot = _pilot(pilot_key)
    bid = _building_id(pilot, id)
    props = generators.building_properties(pilot, bid, survey_state.building_state(pilot, bid))
    return {**props, "survey_detail": survey_service.building_detail(pilot, bid)}


# ---------- Route Builder v2: collection points ----------

@app.get("/api/pilots/{pilot_key}/v2/points")
def get_points_v2(pilot_key: str, sector: str | None = None,
                  max_run_m: float = 150, max_buildings: int = 40, min_buildings: int = 3,
                  merge_radius_m: float = 120, vehicle: str = "e_loader_3w", streams: str = "wet,dry"):
    pilot = _pilot(pilot_key)
    params = {"max_run_m": max_run_m, "max_buildings": max_buildings, "min_buildings": min_buildings,
              "merge_radius_m": merge_radius_m}
    stream_list = [x for x in streams.split(",") if x]
    if not set(stream_list) <= set(WASTE_STREAMS):
        raise HTTPException(422, f"streams must be from {WASTE_STREAMS}")
    try:
        if sector:  # the sector's what-if collection demands, at their places (backend.demand)
            con = demand.connect()
            try:
                pts = demand.plan_points(con, pilot, sector, None, list(WASTE_STREAMS), keep_empty=True, params=params)
            finally:
                con.close()
            for p in pts:
                p.pop("load", None), p.pop("load_kg", None)
        else:
            pts = points_v2.collection_points(pilot, survey_state.building_states(pilot), sector, params)
        points_v2.vehicle_class(vehicle)
    except (ValueError, KeyError) as err:
        raise HTTPException(422, str(err)) from err
    G = twotier._graph(pilot)[0]
    for p in pts:
        p["service_min"] = points_v2.service_minutes(p, vehicle, [s for s in stream_list if not (s == "wet" and p.get("wet_excluded"))])
        # The stretch of street the vehicle drives to collect from this point, for display.
        p["street"] = [node_lonlat(G, n) for n in p.get("path_nodes", []) if n in G.nodes]
    return {"method": "street_runs", "params": params, "vehicle": vehicle, "streams": stream_list,
            "summary": points_v2.summarise(pts), "points": pts}


@app.get("/api/pilots/{pilot_key}/v2/blocks")
def get_blocks_v2(pilot_key: str):
    return Response(points_v2.blocks(_pilot(pilot_key)).to_json(drop_id=True), media_type="application/json")


@app.get("/api/pilots/{pilot_key}/v2/segments")
def get_segments_v2(pilot_key: str):
    _, seg = points_v2.street_graph(_pilot(pilot_key))
    out = seg.drop(columns=["u", "v", "key"]).to_crs(4326)
    return Response(out.to_json(drop_id=True), media_type="application/json")


@app.get("/api/pilots/{pilot_key}/v2/parks")
def get_parks_v2(pilot_key: str, sector: str, method: str | None = None, share_mode: str = "tiers"):
    """Public parks in a sector with the composting site each could host."""
    pilot = _pilot(pilot_key)
    if sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    if share_mode not in ("tiers", "average"):
        raise HTTPException(422, "share_mode must be 'tiers' or 'average'")
    try:
        return {"sites": parks.sites(pilot, sector, method, share_mode), "reference": parks.reference(),
                "site_cap_kg": parks.site_cap_kg()[0], "cap_rule": parks.site_cap_kg()[1]}
    except ValueError as err:
        raise HTTPException(422, str(err)) from err


@app.get("/api/reference/{name}")
def get_reference(name: str):
    if name not in ("vehicles", "roads", "composting"):
        raise HTTPException(404, f"Unknown reference '{name}'")
    return points_v2.reference(name)


class FleetRow(BaseModel):
    type: str
    count: int = Field(ge=0, le=200)


class PlanV2In(BaseModel):
    sector: str
    depot: tuple[float, float]
    mrf: tuple[float, float]
    primary_fleet: list[FleetRow] = Field(min_length=1)
    secondary_fleet: list[FleetRow] = []
    stations: list[tuple[float, float]] | None = None
    truck_depot: tuple[float, float] | None = None
    radius_m: float = Field(500, ge=100, le=3000)
    streams: list[str] = list(WASTE_STREAMS)
    shift_h: float = Field(8, ge=1, le=16)
    unload_min: float = Field(10, ge=0, le=120)
    time_limit_s: int = Field(30, ge=5, le=300)
    park: dict | None = None
    day: str | None = None  # mon..sun: plan the next such date (kept for older callers; prefer `date`)
    date: str | None = None  # YYYY-MM-DD: plan that date's collection demands


@app.get("/api/pilots/{pilot_key}/v2/stations")
def get_stations_v2(pilot_key: str, sector: str, radius_m: float = 500, trucks: str = ""):
    """Suggested transfer stations. `trucks` lists truck types in use, comma separated; a station
    holds at least two loads of the largest one."""
    pilot = _pilot(pilot_key)
    if sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{sector}'")
    fleet = [{"type": t, "count": 1} for t in trucks.split(",") if t]
    try:
        stations = twotier.suggest_stations(pilot, sector, radius_m, secondary_fleet=fleet)
        cap, truck = twotier.station_capacity_kg(fleet, list(WASTE_STREAMS))
    except KeyError as err:
        raise HTTPException(422, str(err)) from err
    return {"capacity_kg": round(cap), "capacity_basis": f"2 loads of {truck}",
            "stations": [{k: v for k, v in s.items() if k != "node"} for s in stations]}


def _fleet_available(pilot: str, sector: str) -> dict:
    con = resources.connect()
    try:
        return resources.available(con, pilot, sector)
    finally:
        con.close()


def _plan_input(pilot_key: str, body: PlanV2In, check_fleet: bool = True) -> twotier.PlanInput:
    pilot = _pilot(pilot_key)
    if body.sector not in set(pilot_sectors(pilot)["name"]):
        raise HTTPException(404, f"Unknown sector '{body.sector}'")
    if not set(body.streams) <= set(WASTE_STREAMS) or not body.streams:
        raise HTTPException(422, f"streams must be from {WASTE_STREAMS}")
    if check_fleet:  # a plan may not use more vehicles, drivers or collectors than the sector has
        con = resources.connect()
        try:
            resources.check_plan(con, pilot, body.sector, [r.model_dump() for r in body.primary_fleet + body.secondary_fleet])
        except resources.ResourceError as err:
            raise HTTPException(err.status, str(err)) from err
        finally:
            con.close()
    day_plan, shift_h, plan_date = None, body.shift_h, None
    if body.day or body.date:
        try:
            plan_date = demand.parse_date(body.date) if body.date else demand.next_date_for(body.day)
        except demand.DemandError as err:
            raise HTTPException(err.status, str(err)) from err
        con = cycle.connect()
        try:
            day_plan = cycle.day_plan(con, pilot, body.sector, demand.weekday(plan_date))
        except cycle.CycleError as err:
            raise HTTPException(err.status, str(err)) from err
        finally:
            con.close()
        if day_plan["scheduled"]:
            if not day_plan["window"]:
                raise HTTPException(422, f"Nothing is collected in {body.sector} on {body.day.title()} in the collection cycle.")
            shift_h = day_plan["window"]["hours"]  # the routes must fit the day's collection window
    return twotier.PlanInput(
        pilot=pilot, sector=body.sector, depot=body.depot, mrf=body.mrf, cycle=day_plan, date=plan_date,
        primary_fleet=[r.model_dump() for r in body.primary_fleet],
        secondary_fleet=[r.model_dump() for r in body.secondary_fleet],
        stations=body.stations or None, truck_depot=body.truck_depot, radius_m=body.radius_m,
        streams=body.streams, shift_h=shift_h, unload_min=body.unload_min, time_limit_s=body.time_limit_s,
        park=body.park)


def _with_basis(result: dict, available: dict) -> dict:
    """Say whether the plan's vehicles and crews were checked against the inventory or are hypothetical,
    and how many drivers and collectors the plan's vehicles need."""
    inv = available["inventory"]
    result["fleet_basis"] = "inventory" if inv["vehicle"] else "hypothetical"
    result["crew_basis"] = "inventory" if inv["staff"] else "hypothetical"
    vehicles = [{"type": v["type"], "count": 1} for v in result["primary"]["vehicles"] + result["secondary"]["trucks"]]
    result["crew"] = {"needed": resources.crew_needed(vehicles, available["crew_per_vehicle"]),
                      "available": {"drivers": available["staff"].get("driver", {}).get("count", 0),
                                    "collectors": available["staff"].get("waste_collector", {}).get("count", 0)}}
    return result


def _who(request: Request) -> dict:
    """The signed-in person if any (plans run without sign-in are recorded as anonymous)."""
    try:
        return auth.actor(request.headers)
    except KeyError:
        return {"name": None, "role": None}


def _record(inp: twotier.PlanInput, body: PlanV2In, result: dict, actor: dict) -> dict:
    """Keep the optimiser's result as a draft route plan, with its feasibility and exceptions."""
    con = plans.connect()
    try:
        result["plan"] = plans.save(con, inp.pilot, inp.sector, inp.date,
                                    body.model_dump(exclude={"park"}) | {"date": inp.date, "shift_h": inp.shift_h}, result, actor)
        result["plan"]["date"] = inp.date
    finally:
        con.close()
    return result


@app.post("/api/pilots/{pilot_key}/v2/plan")
def post_plan_v2(pilot_key: str, body: PlanV2In, request: Request):
    inp = _plan_input(pilot_key, body)
    try:
        return _record(inp, body, _with_basis(twotier.plan(inp), _fleet_available(inp.pilot, inp.sector)), _who(request))
    except (ValueError, KeyError) as err:
        raise HTTPException(422, str(err)) from err


# Background jobs so the page can show progress while the optimiser runs.
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


class FleetIn(PlanV2In):
    target_h: float = Field(5, ge=1, le=16)


@app.post("/api/pilots/{pilot_key}/v2/plan/jobs")
def start_plan_job(pilot_key: str, body: PlanV2In, request: Request):
    inp = _plan_input(pilot_key, body)
    av, who = _fleet_available(inp.pilot, inp.sector), _who(request)
    return _start_job(lambda report: _record(inp, body, _with_basis(twotier.plan(inp, report), av), who), body.time_limit_s + 10)


@app.post("/api/pilots/{pilot_key}/v2/fleet/jobs")
def start_fleet_job(pilot_key: str, body: FleetIn, request: Request):
    """Suggest the fleet that finishes within target_h hours (runs the optimiser several times)."""
    inp = _plan_input(pilot_key, body, check_fleet=False)  # the suggestion itself stays within the fleet
    av = _fleet_available(inp.pilot, inp.sector)
    caps = av["vehicles"] if av["inventory"]["vehicle"] else None
    crew = ({"per_vehicle": av["crew_per_vehicle"], "drivers": av["staff"].get("driver", {}).get("count", 0),
             "collectors": av["staff"].get("waste_collector", {}).get("count", 0)} if av["inventory"]["staff"] else None)

    who = _who(request)

    def run(report):
        out = twotier.suggest_fleet(inp, body.target_h, report, caps=caps, crew=crew)
        _record(inp, body, _with_basis(out["result"], av), who)
        out["fleet_basis"] = out["result"]["fleet_basis"]
        return out
    return _start_job(run, 4 * (body.time_limit_s + 10))


def _start_job(fn, expected_s: float) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"status": "running", "stage": "Starting", "progress": 0.0, "started": time.time(), "result": None, "error": None}
    with _JOBS_LOCK:
        for k in [k for k, j in _JOBS.items() if time.time() - j["started"] > 3600]:
            _JOBS.pop(k)
        _JOBS[job_id] = job

    def report(stage: str, frac: float):
        job["stage"], job["progress"] = stage, max(job["progress"], min(1.0, frac))

    def work():
        try:
            job["result"] = fn(report)
            job["status"] = "done"
        except Exception as err:  # reported to the page, not raised
            job["error"], job["status"] = str(err), "error"

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": job_id, "expected_s": expected_s}


@app.get("/api/pilots/{pilot_key}/v2/plan/jobs/{job_id}")
def get_plan_job(pilot_key: str, job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown or expired job")
    elapsed = time.time() - job["started"]
    frac = job["progress"]
    eta = max(0.0, elapsed / frac - elapsed) if frac > 0.12 else None
    out = {"status": job["status"], "stage": job["stage"], "progress": round(frac, 3), "elapsed_s": round(elapsed, 1),
           "eta_s": None if eta is None else round(eta)}
    if job["status"] == "done":
        out["result"] = job["result"]
    if job["status"] == "error":
        out["error"] = job["error"]
    return out


# ---------- Regulations library ----------

@app.get("/api/regulations")
def list_regulations():
    return regulations.index()


@app.get("/api/regulations/{doc_id}")
def get_regulation(doc_id: str):
    try:
        return regulations.load(doc_id)
    except KeyError as err:
        raise HTTPException(404, str(err)) from err


class FrontendFiles(StaticFiles):
    """The frontend, revalidated on every load so a changed page, script or stylesheet shows at once."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", FrontendFiles(directory=FRONTEND_DIR, html=True), name="frontend")
