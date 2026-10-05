"""Clean City (data/ops.db): keeping streets and public spaces clean. A people-and-equipment system,
separate from the vehicle routes.

Street inventory. Every street segment of the pilot (from the street graph) with its sector, length,
width and road class, and what is along it: buildings, commercial frontage, hospitals, markets. From
these each street gets a suggested cleaning class (primary/busy, commercial, market, residential,
low-intensity) with a cleaning frequency and a public-bin spacing from reference/clean_city.json.
The planner can override the class, frequency, cleaning team and equipment of any street.

Public bins. Recommended places follow the class's spacing (about 100 m on normal streets, 30 m on
busy and market streets), skipping places that already have a bin; they are suggestions, not
automatic placement. Bins that exist are recorded on the street with capacity, stream, status and
service frequency. A bin that is full or overflowing, or whose service is due, is a collection
demand for the route builder: Clean City says which bins need emptying, the route builder decides
which vehicle collects them and in what order.

Workforce. Required cleaning worker-hours a day (street length × frequency ÷ productivity, plus bin
servicing and the GVP clearing backlog) against the hours of available sweepers, cleaning workers
and GVP response workers (backend.resources).

SWM Rules 2026: street sweeping waste collected separately and regularly (r. 39(16)); waste from
public places collected on a schedule (r. 39(13)). Frequencies, spacing and productivity are
planning standards and estimates, not rules. Changes are logged in cleancity_log (append-only).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd

from backend import resources
from backend.buildings import layers
from backend.buildings.layers import METRIC_CRS
from backend.regulations import stream_keys, swm
from backend.routing import points as P

CLASSES = ("primary", "commercial", "market", "residential", "low_intensity")
BIN_STATUSES = ("normal", "near_capacity", "full", "overflowing", "damaged", "missing")
BIN_STREAMS = ("twin", "dry", "wet")  # twin: separate wet and dry compartments
CONDITIONS = ("good", "fair", "poor")
PLANNERS = ("planner", "admin")
BIN_REPORTERS = ("planner", "admin", "operations_supervisor", "collector")
STREAMS = stream_keys()

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS street_plan (
    pilot TEXT NOT NULL,
    seg_id TEXT NOT NULL,
    class TEXT CHECK (class IS NULL OR class IN {CLASSES}),
    cleanings_per_week REAL,
    team TEXT, equipment TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT,
    PRIMARY KEY (pilot, seg_id)
);
CREATE TABLE IF NOT EXISTS public_bin (
    id TEXT PRIMARY KEY,
    pilot TEXT NOT NULL,
    bin_code TEXT NOT NULL,
    lon REAL NOT NULL, lat REAL NOT NULL,
    seg_id TEXT NOT NULL, road_u INTEGER, road_v INTEGER, road_name TEXT,
    sector TEXT,
    capacity_l REAL NOT NULL,
    stream TEXT NOT NULL CHECK (stream IN {BIN_STREAMS}),
    condition TEXT CHECK (condition IS NULL OR condition IN {CONDITIONS}),
    status TEXT NOT NULL DEFAULT 'normal' CHECK (status IN {BIN_STATUSES}),
    services_per_day REAL,                    -- NULL: the street class's
    team TEXT,
    last_serviced TEXT,
    created_at TEXT NOT NULL, created_by TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT,
    UNIQUE (pilot, bin_code)
);
CREATE TABLE IF NOT EXISTS cleancity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                       -- street or bin
    ref TEXT NOT NULL,
    at TEXT NOT NULL,
    user_name TEXT, user_role TEXT,
    change TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cleancity_log_no_update BEFORE UPDATE ON cleancity_log
BEGIN SELECT RAISE(ABORT, 'cleancity_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cleancity_log_no_delete BEFORE DELETE ON cleancity_log
BEGIN SELECT RAISE(ABORT, 'cleancity_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS public_bin_no_delete BEFORE DELETE ON public_bin
BEGIN SELECT RAISE(ABORT, 'bins are never deleted: mark them missing'); END;
"""


class CleanCityError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = resources.connect(db_path)  # the same operations database, with the resource tables in place
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def standards() -> dict:
    return P.reference("clean_city")


def rules() -> list[dict]:
    ct = swm()["collection_and_transport"]
    return [{"rule": ct[k]["rule"], "text": ct[k]["text"]} for k in ("street_sweeping", "public_places_schedule")]


def _log(con, kind: str, ref: str, change: dict, actor: dict) -> None:
    con.execute("INSERT INTO cleancity_log (kind, ref, at, user_name, user_role, change) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, ref, _now(), actor.get("name"), actor.get("role"), json.dumps(change, default=str)))


# ---------- Street inventory ----------

_INV_CACHE: dict[tuple, tuple[str, list[dict]]] = {}
_INV_LOCK = threading.Lock()


def _suggest(road_class: str, b: Counter, n: int, length_m: float, dead_end: bool, std: dict) -> tuple[str, str]:
    c = std["classification"]
    if b and sum(b[k] for k in c["market_categories"]):
        return "market", "market buildings along it"
    if road_class in c["primary_road_classes"]:
        return "primary", f"{road_class} road"
    if sum(b[k] for k in c["busy_frontage_categories"]):
        return "primary", "hospital or clinic along it"
    com = sum(b[k] for k in c["commercial_categories"])
    if com >= c["commercial_min_buildings"] or (n and com / n >= c["commercial_share"] and com >= 2):
        return "commercial", f"{com} of {n} buildings commercial"
    if dead_end:
        return "low_intensity", "dead end"
    if road_class in c["low_intensity_road_classes"]:
        return "low_intensity", f"{road_class.replace('_', ' ')}"
    if n / max(length_m, 1) * 100 < c["low_intensity_max_buildings_per_100m"]:
        return "low_intensity", f"{n} buildings in {length_m:.0f} m"
    return "residential", f"{n} buildings, mostly homes"


def street_inventory(pilot: str) -> list[dict]:
    """Every street segment inside the pilot's sectors, with what is along it and a suggested class.
    Cached until the survey changes."""
    from backend.survey import state
    surveys = state.building_states(pilot)
    fp = P.survey_fingerprint(surveys)
    with _INV_LOCK:
        hit = _INV_CACHE.get((pilot,))
        if hit and hit[0] == fp:
            return hit[1]
    Gu, seg = P.street_graph(pilot)
    std = standards()
    sectors = layers.sectors(pilot).to_crs(METRIC_CRS)[["name", "geometry"]]
    mids = gpd.GeoDataFrame(seg[["seg_id"]], geometry=seg.geometry.interpolate(0.5, normalized=True), crs=METRIC_CRS)
    in_sector = gpd.sjoin(mids, sectors, how="inner", predicate="within").drop_duplicates("seg_id").set_index("seg_id")["name"]
    t = P.generator_table(pilot, surveys)
    along = {sid: Counter(g["category"]) for sid, g in t.groupby("seg_id")}
    deg = dict(Gu.degree())
    seg_wgs = seg.to_crs(4326)
    out = []
    for r, g in zip(seg.itertuples(), seg_wgs.geometry):
        if r.seg_id not in in_sector.index:
            continue
        b = along.get(r.seg_id, Counter())
        n = sum(b.values())
        dead_end = deg.get(r.u, 2) == 1 or deg.get(r.v, 2) == 1
        cls, why = _suggest(r.road_class, b, n, float(r.length_m), dead_end, std)
        com = sum(b[k] for k in std["classification"]["commercial_categories"])
        out.append({"seg_id": r.seg_id, "name": P._street_name(r.name), "sector": in_sector[r.seg_id], "road_class": r.road_class,
                    "length_m": round(float(r.length_m), 1), "width_m": round(float(r.width_m), 1), "u": int(r.u), "v": int(r.v),
                    "buildings": n, "commercial": com, "healthcare": b["healthcare"], "dead_end": dead_end,
                    "commercial_intensity": round(com / max(r.length_m, 1) * 100, 2),  # commercial buildings per 100 m
                    "building_density": round(n / max(r.length_m, 1) * 100, 2),     # buildings per 100 m (pedestrian proxy)
                    "suggested_class": cls, "why": why,
                    "coords": [[round(x, 6), round(y, 6)] for x, y in g.coords]})
    with _INV_LOCK:
        _INV_CACHE[(pilot,)] = (fp, out)
    return out


def streets(con, pilot: str, sector: str | None = None) -> list[dict]:
    """The street inventory with the planner's overrides: class, frequency, team and equipment in force."""
    std = standards()["classes"]
    plans = {r["seg_id"]: dict(r) for r in con.execute("SELECT * FROM street_plan WHERE pilot = ?", (pilot,))}
    out = []
    for s in street_inventory(pilot):
        if sector and s["sector"] != sector:
            continue
        p = plans.get(s["seg_id"], {})
        cls = p.get("class") or s["suggested_class"]
        out.append({**s, "class": cls, "class_overridden": bool(p.get("class")),
                    "cleanings_per_week": p.get("cleanings_per_week") or std[cls]["cleanings_per_week"],
                    "frequency_overridden": p.get("cleanings_per_week") is not None,
                    "team": p.get("team"), "equipment": p.get("equipment")})
    return out


def plan_street(con, pilot: str, seg_id: str, data: dict, actor: dict) -> dict:
    """Override a street's class, cleaning frequency, team or equipment (None clears an override)."""
    if actor.get("role") not in PLANNERS:
        raise CleanCityError("Only a planner or an admin can change street cleaning plans.", 403)
    if seg_id not in {s["seg_id"] for s in street_inventory(pilot)}:
        raise CleanCityError("Unknown street segment.", 404)
    old = con.execute("SELECT * FROM street_plan WHERE pilot = ? AND seg_id = ?", (pilot, seg_id)).fetchone()
    new = dict(old) if old else {"class": None, "cleanings_per_week": None, "team": None, "equipment": None}
    if "class" in data:
        if data["class"] not in (None, "", *CLASSES):
            raise CleanCityError(f"class must be one of {CLASSES}")
        new["class"] = data["class"] or None
    if "cleanings_per_week" in data:
        v = data["cleanings_per_week"]
        if v not in (None, ""):
            v = float(v)
            if not 0 < v <= 42:
                raise CleanCityError("cleanings_per_week must be between 0 and 42 (six a day).")
        new["cleanings_per_week"] = v if v not in (None, "") else None
    for f in ("team", "equipment"):
        if f in data:
            new[f] = (data[f] or "").strip() or None
    change = {k: [old[k] if old else None, new[k]] for k in ("class", "cleanings_per_week", "team", "equipment")
              if (old[k] if old else None) != new[k]}
    if change:
        con.execute("INSERT OR REPLACE INTO street_plan (pilot, seg_id, class, cleanings_per_week, team, equipment, updated_at, updated_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (pilot, seg_id, new["class"], new["cleanings_per_week"], new["team"], new["equipment"], _now(), actor.get("name")))
        _log(con, "street", seg_id, change, actor)
        con.commit()
    return next(s for s in streets(con, pilot) if s["seg_id"] == seg_id)


# ---------- Public bins ----------

def _bin_row(r, classes: dict, std: dict) -> dict:
    d = dict(r)
    per_day = d["services_per_day"] if d["services_per_day"] is not None else std["classes"][classes.get(d["seg_id"], "residential")]["bin_services_per_day"]
    d["street_class"] = classes.get(d["seg_id"])
    d["services_per_day_in_force"] = per_day
    due = False
    if per_day and d["status"] not in ("damaged", "missing"):
        if not d["last_serviced"]:
            due = True
        else:
            due = datetime.now(timezone.utc) - datetime.fromisoformat(d["last_serviced"]) >= timedelta(days=1 / per_day)
    d["service_due"] = due
    d["needs_collection"] = d["status"] in ("full", "overflowing") or due  # near capacity is a warning only
    return d


def bins(con, pilot: str, sector: str | None = None) -> list[dict]:
    std = standards()
    classes = {s["seg_id"]: s["class"] for s in streets(con, pilot)}
    q, args = "SELECT * FROM public_bin WHERE pilot = ?", [pilot]
    if sector:
        q += " AND sector = ?"
        args.append(sector)
    return [_bin_row(r, classes, std) for r in con.execute(q + " ORDER BY bin_code", args)]


def add_bin(con, pilot: str, lon: float, lat: float, data: dict, actor: dict) -> dict:
    """Record a bin that exists, placed on the nearest street."""
    from backend.survey import gvp
    from backend.survey.service import point_sector
    if actor.get("role") not in PLANNERS:
        raise CleanCityError("Only a planner or an admin can add public bins.", 403)
    road = gvp.snap_to_road(pilot, lon, lat)
    if road["snap_m"] > 30:
        raise CleanCityError(f"A public bin stands on a street; the nearest street is {road['snap_m']:.0f} m away.")
    sector = point_sector(pilot, road["lon"], road["lat"])
    if not sector:
        raise CleanCityError("This place is outside the pilot area.")
    code = " ".join(str(data.get("bin_code") or "").split()).upper()
    if not code:
        n = con.execute("SELECT COUNT(*) FROM public_bin WHERE pilot = ?", (pilot,)).fetchone()[0] + 1
        code = f"BIN-{n:04d}"
    stream = data.get("stream") or "twin"
    if stream not in BIN_STREAMS:
        raise CleanCityError(f"stream must be one of {BIN_STREAMS}")
    cap = float(data.get("capacity_l") or standards()["bins"]["default_capacity_l"])
    if not 0 < cap <= 5000:
        raise CleanCityError("capacity_l must be between 0 and 5000 litres")
    bid, now = uuid.uuid4().hex, _now()
    try:
        con.execute("INSERT INTO public_bin (id, pilot, bin_code, lon, lat, seg_id, road_u, road_v, road_name, sector, capacity_l, stream,"
                    " condition, team, created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (bid, pilot, code, road["lon"], road["lat"], road["seg_id"], road["road_u"], road["road_v"], road["road_name"], sector,
                     cap, stream, data.get("condition") or "good", (data.get("team") or "").strip() or None, now, actor.get("name"), now, actor.get("name")))
    except sqlite3.IntegrityError as err:
        raise CleanCityError(f"{code} is already registered.", 409) from err
    _log(con, "bin", bid, {"created": [None, {"bin_code": code, "seg_id": road["seg_id"]}]}, actor)
    con.commit()
    return next(b for b in bins(con, pilot) if b["id"] == bid)


def update_bin(con, pilot: str, bid: str, data: dict, actor: dict) -> dict:
    """Report a bin's state (status, condition), mark it serviced, or (planner) change its details."""
    role = actor.get("role")
    if role not in BIN_REPORTERS:
        raise CleanCityError("You cannot update public bins with your role.", 403)
    old = con.execute("SELECT * FROM public_bin WHERE id = ? AND pilot = ?", (bid, pilot)).fetchone()
    if old is None:
        raise CleanCityError("Unknown bin.", 404)
    sets = {}
    if "status" in data:
        if data["status"] not in BIN_STATUSES:
            raise CleanCityError(f"status must be one of {BIN_STATUSES}")
        sets["status"] = data["status"]
    if "condition" in data:
        if data["condition"] not in (None, *CONDITIONS):
            raise CleanCityError(f"condition must be one of {CONDITIONS}")
        sets["condition"] = data["condition"]
    if data.get("serviced"):
        sets["last_serviced"] = _now()
        sets.setdefault("status", "normal")
    planner_fields = {"capacity_l", "stream", "services_per_day", "team"} & set(data)
    if planner_fields and role not in PLANNERS:
        raise CleanCityError("Only a planner or an admin can change a bin's capacity, stream, frequency or team.", 403)
    if "capacity_l" in data:
        sets["capacity_l"] = float(data["capacity_l"])
    if "stream" in data:
        if data["stream"] not in BIN_STREAMS:
            raise CleanCityError(f"stream must be one of {BIN_STREAMS}")
        sets["stream"] = data["stream"]
    if "services_per_day" in data:
        v = data["services_per_day"]
        sets["services_per_day"] = None if v in (None, "") else float(v)
    if "team" in data:
        sets["team"] = (data["team"] or "").strip() or None
    change = {k: [old[k], v] for k, v in sets.items() if old[k] != v}
    if change:
        sets.update(updated_at=_now(), updated_by=actor.get("name"))
        con.execute(f"UPDATE public_bin SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", [*sets.values(), bid])
        _log(con, "bin", bid, change, actor)
        con.commit()
    return next(b for b in bins(con, pilot) if b["id"] == bid)


def recommend_bins(con, pilot: str, sector: str | None = None) -> list[dict]:
    """Suggested bin places by the street class's spacing, skipping places near a bin that exists.
    A planning standard to check against footfall, space and access on the ground."""
    std = standards()
    gap = std["bins"]["min_gap_to_existing_m"]
    existing = [P._TO_METRIC.transform(b["lon"], b["lat"]) for b in bins(con, pilot) if b["status"] != "missing"]
    _, seg = P.street_graph(pilot)
    geom = dict(zip(seg["seg_id"], seg.geometry))
    out = []
    for s in streets(con, pilot, sector):
        spacing = std["classes"][s["class"]]["bin_spacing_m"]
        if not spacing or s["length_m"] < spacing / 2:
            continue
        n = max(1, int(s["length_m"] // spacing))
        line = geom[s["seg_id"]]
        for i in range(n):
            pt = line.interpolate((i + 0.5) * line.length / n)
            if any(((pt.x - x) ** 2 + (pt.y - y) ** 2) ** 0.5 < gap for x, y in existing):
                continue
            lon, lat = P._TO_WGS84.transform(pt.x, pt.y)
            out.append({"seg_id": s["seg_id"], "sector": s["sector"], "class": s["class"], "street": s["name"],
                        "lon": round(lon, 6), "lat": round(lat, 6)})
    return out


def bin_demands(con, pilot: str, sector: str) -> list[dict]:
    """Bins that need emptying, as route collection points (kept apart by is_bin). Estimates."""
    std = standards()
    dens = P.reference("vehicles")["stream_densities_kg_m3"]
    out = []
    for b in bins(con, pilot, sector):
        if not b["needs_collection"]:
            continue
        fill = std["bins"]["fill_when"][b["status"] if b["status"] in ("full", "overflowing", "near_capacity") else "due"]
        split = {"twin": {"wet": 0.5, "dry": 0.5}, "dry": {"dry": 1.0}, "wet": {"wet": 1.0}}[b["stream"]]
        kg = {s: round(b["capacity_l"] / 1000 * fill * split.get(s, 0) * float(dens[s]["value"]), 2) for s in STREAMS}
        out.append({
            "id": f"BIN:{b['id']}", "use": "public_bin", "is_bin": True, "demand": "public_bin", "sector": sector,
            "label": f"Public bin {b['bin_code']}" + (f", {b['road_name']}" if b["road_name"] else "") + f" ({b['status'].replace('_', ' ')}{', due' if b['service_due'] else ''})",
            "buildings": 0, "building_ids": [], "building_kg": [[kg[s] for s in STREAMS]],
            "kg": kg, "total_kg": round(sum(kg.values()), 1), "home_composted_kg": 0.0, "span_m": 0.0, "min_width_m": None,
            "lon": b["lon"], "lat": b["lat"], "path_nodes": [b["road_u"], b["road_v"]],
        })
    return out


# ---------- Workforce ----------

def workload(con, pilot: str, sector: str) -> dict:
    """Required cleaning worker-hours a day in a sector against the hours available."""
    from backend.survey import gvp
    std = standards()
    prod = std["productivity"]
    st = streets(con, pilot, sector)
    sweeping = sum(s["length_m"] * s["cleanings_per_week"] / 7 for s in st) / prod["street_m_per_worker_hour"]
    by_class = {c: {"streets": 0, "km": 0.0, "hours": 0.0} for c in CLASSES}
    for s in st:
        b = by_class[s["class"]]
        b["streets"] += 1
        b["km"] += s["length_m"] / 1000
        b["hours"] += s["length_m"] * s["cleanings_per_week"] / 7 / prod["street_m_per_worker_hour"]
    bs = [b for b in bins(con, pilot, sector) if b["status"] != "missing"]
    bin_hours = sum((b["services_per_day_in_force"] or 0) * prod["bin_service_minutes"] / 60 for b in bs)
    tasks = gvp.cleaning_tasks(pilot, sector)
    gvp_hours = sum(prod["gvp_clearing_worker_hours"][t["severity"]] for t in tasks)
    av = resources.available(con, pilot, sector)
    roles = std["cleaning_roles"]
    have = {r: av["staff"].get(r, {"count": 0, "hours": 0.0}) for r in roles}
    available_h = sum(h["hours"] for h in have.values())
    required_h = sweeping + bin_hours + gvp_hours
    return {
        "sector": sector,
        "required_hours": round(required_h, 1),
        "parts": {"street_sweeping": round(sweeping, 1), "public_bins": round(bin_hours, 1), "gvp_clearing_backlog": round(gvp_hours, 1)},
        "by_class": {c: {k: round(v, 1) for k, v in b.items()} for c, b in by_class.items()},
        "available_hours": round(available_h, 1), "available_staff": have, "staff_known": av["inventory"]["staff"],
        "gap_hours": round(available_h - required_h, 1),
        "workers_needed_at_8h": round(required_h / 8, 1),
        "not_counted": ["market cleaning cycles beyond the street frequency", "public spaces other than streets"],
    }
