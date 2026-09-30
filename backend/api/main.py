"""FastAPI app: routing, pilot building intelligence and regulations APIs, plus the static frontend.

Run from the repository root:
    .venv\\Scripts\\python -m uvicorn backend.api.main:app --reload
then open http://127.0.0.1:8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import regulations
from backend.buildings import generators
from backend.buildings.layers import sectors as pilot_sectors
from backend.config import AREAS, FRONTEND_DIR, PILOTS, WASTE_STREAMS
from backend.routing.network import load_graph, network_geojson, node_lonlat
from backend.routing import points as points_v2
from backend.routing.planner import PlanRequest, plan_routes
from backend.routing.scenario import default_depot_and_facility, generate_points
from backend.survey import store as survey_store

app = FastAPI(title="SWM Urban Waste Intelligence")
app.add_middleware(GZipMiddleware, minimum_size=2000)


def _graph(area_key: str):
    if area_key not in AREAS:
        raise HTTPException(404, f"Unknown area '{area_key}'")
    return load_graph(area_key)


def _pilot(pilot_key: str) -> str:
    if pilot_key not in PILOTS:
        raise HTTPException(404, f"Unknown pilot '{pilot_key}'")
    return pilot_key


class Point(BaseModel):
    id: str
    lon: float
    lat: float
    demand_kg: dict[str, float]
    service_min: float = Field(ge=0)


class SolveRequest(BaseModel):
    depot: tuple[float, float]
    facility: tuple[float, float]
    points: list[Point] = Field(min_length=1)
    stream: str = "wet"
    num_vehicles: int = Field(3, ge=1, le=50)
    capacity_kg: float = Field(1500, gt=0)
    shift_min: float = Field(240, ge=30, le=720)
    time_limit_s: int = Field(3, ge=1, le=60)


# ---------- Routing ----------

@app.get("/api/areas")
def list_areas():
    out = []
    for a in AREAS.values():
        item = {"key": a.key, "name": a.name, "center": [a.lon, a.lat], "pilot": a.pilot, "sectors": []}
        if a.pilot:
            item["sectors"] = list(pilot_sectors(a.pilot)["name"])
        out.append(item)
    return out


@app.get("/api/areas/{area_key}/network")
def get_network(area_key: str):
    return network_geojson(_graph(area_key))


@app.get("/api/areas/{area_key}/scenario")
def get_scenario(area_key: str, n_points: int = 40, seed: int = 1, source: str = "synthetic", sector: str | None = None):
    G = _graph(area_key)
    depot, facility = default_depot_and_facility(G, area_key)
    base = {"depot": node_lonlat(G, depot), "facility": node_lonlat(G, facility)}
    if source == "buildings":
        pilot = AREAS[area_key].pilot
        if not pilot:
            raise HTTPException(422, "Building-based collection points exist only for pilot areas")
        points = generators.collection_points(pilot, survey_store.all_records(pilot), sector)
        return {
            **base,
            "points": points,
            "note": "Collection stops built from mapped buildings. Quantities are estimates (surveyed data where available).",
        }
    if not 1 <= n_points <= 200:
        raise HTTPException(422, "n_points must be between 1 and 200")
    return {
        **base,
        "points": generate_points(G, n_points, seed, exclude={depot, facility}),
        "note": "Synthetic points and demands. Replace with municipal collection-point data.",
    }


@app.post("/api/areas/{area_key}/solve")
def solve(area_key: str, req: SolveRequest):
    if req.stream not in WASTE_STREAMS:
        raise HTTPException(422, f"stream must be one of {WASTE_STREAMS}")
    G = _graph(area_key)
    return plan_routes(
        G,
        PlanRequest(
            depot=req.depot,
            facility=req.facility,
            points=[p.model_dump() for p in req.points],
            stream=req.stream,
            num_vehicles=req.num_vehicles,
            capacity_kg=req.capacity_kg,
            shift_min=req.shift_min,
            time_limit_s=req.time_limit_s,
        ),
    )


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
    return generators.summary(pilot, survey_store.all_records(pilot))


@app.get("/api/pilots/{pilot_key}/overrides")
def get_overrides(pilot_key: str):
    """Map properties of surveyed buildings, replacing the OSM-only values in the buildings layer."""
    pilot = _pilot(pilot_key)
    return {
        bid: generators.map_properties(generators.building_properties(pilot, bid, rec))
        for bid, rec in survey_store.all_records(pilot).items()
        if generators.has_building(pilot, bid)
    }


def _building_id(pilot: str, building_id: str) -> str:
    if not generators.has_building(pilot, building_id):
        raise HTTPException(404, f"Unknown building '{building_id}'")
    return building_id


@app.get("/api/pilots/{pilot_key}/building")
def get_building(pilot_key: str, id: str):
    """Full building card: OSM base, survey record and all computed fields."""
    pilot = _pilot(pilot_key)
    bid = _building_id(pilot, id)
    return generators.building_properties(pilot, bid, survey_store.get(pilot, bid))


class SurveyIn(BaseModel):
    use: str | None = None
    units: int | None = Field(None, ge=0)
    commercial_units: int | None = Field(None, ge=0)
    floors: float | None = Field(None, gt=0, le=100)
    water_lpd: float | None = Field(None, ge=0)
    water_source: str | None = None
    onsite_processing: str | None = None
    segregation_observed: str | None = None
    weighed_kg_day: float | None = Field(None, ge=0)
    surveyor: str | None = None
    notes: str | None = None


@app.put("/api/pilots/{pilot_key}/survey")
def put_survey(pilot_key: str, id: str, survey: SurveyIn):
    pilot = _pilot(pilot_key)
    bid = _building_id(pilot, id)
    try:
        record = survey_store.save(pilot, bid, survey.model_dump())
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    return generators.building_properties(pilot, bid, record)


@app.delete("/api/pilots/{pilot_key}/survey")
def delete_survey(pilot_key: str, id: str):
    pilot = _pilot(pilot_key)
    bid = _building_id(pilot, id)
    survey_store.delete(pilot, bid)
    return generators.building_properties(pilot, bid, None)


@app.get("/api/survey/options")
def survey_options():
    return {
        "use": survey_store.USES,
        "onsite_processing": survey_store.ONSITE_PROCESSING,
        "segregation_observed": survey_store.SEGREGATION,
        "water_source": survey_store.WATER_SOURCES,
    }


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
        pts = points_v2.collection_points(pilot, survey_store.all_records(pilot), sector, params)
        points_v2.vehicle_class(vehicle)
    except (ValueError, KeyError) as err:
        raise HTTPException(422, str(err)) from err
    for p in pts:
        p["service_min"] = points_v2.service_minutes(p, vehicle, [s for s in stream_list if not (s == "wet" and p.get("wet_excluded"))])
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


@app.get("/api/reference/{name}")
def get_reference(name: str):
    if name not in ("vehicles", "roads"):
        raise HTTPException(404, f"Unknown reference '{name}'")
    return points_v2.reference(name)


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


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
