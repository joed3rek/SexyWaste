"""Classifies mapped buildings into waste generator types and estimates their waste.

Terms, thresholds and obligations come from the regulations library (SWM Rules 2026).
Generation rates come from norms.json and are assumptions, reported as such.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd

from backend import regulations as regs
from backend.buildings import layers
from backend.buildings.layers import METRIC_CRS

NORMS = json.loads((Path(__file__).with_name("norms.json")).read_text(encoding="utf-8"))
STREAMS = regs.stream_keys()

CATEGORY_LABELS = {
    "residential_house": "Independent house",
    "residential_apartment": "Apartment / residential society",
    "mixed_use": "Mixed use (shops below, homes above)",
    "commercial_retail": "Shop / commercial establishment",
    "commercial_office": "Office / company",
    "food_service": "Restaurant / eatery / bakery",
    "hotel": "Hotel / hostel / guest house",
    "healthcare": "Hospital / clinic",
    "educational": "School / college",
    "religious": "Place of worship",
    "market": "Market",
    "industrial": "Industrial unit",
    "public_institutional": "Government / public building",
    "construction": "Under construction (C&D waste)",
    "structure": "Non-occupied structure",
    "unclassified": "Unclassified (assumed household)",
}

HOUSEHOLD = {"residential_house", "residential_apartment", "unclassified"}
NO_REGULAR_WASTE = {"structure", "construction"}

# OSM building=* values that already say what the building is.
BUILDING_TAG = {
    "house": "residential_house", "detached": "residential_house", "semidetached_house": "residential_house",
    "bungalow": "residential_house", "terrace": "residential_house", "residential": "residential_house",
    "hut": "residential_house", "apartments": "residential_apartment", "dormitory": "hotel",
    "commercial": "commercial_retail", "retail": "commercial_retail", "supermarket": "commercial_retail",
    "kiosk": "commercial_retail", "fuel_station": "commercial_retail", "office": "commercial_office",
    "hotel": "hotel", "hospital": "healthcare", "clinic": "healthcare",
    "school": "educational", "college": "educational", "university": "educational", "kindergarten": "educational",
    "temple": "religious", "church": "religious", "mosque": "religious", "religious": "religious",
    "industrial": "industrial", "warehouse": "industrial", "factory": "industrial",
    "public": "public_institutional", "government": "public_institutional", "civic": "public_institutional",
    "train_station": "public_institutional", "grandstand": "structure", "restaurant": "food_service",
    "construction": "construction", "roof": "structure", "garage": "structure", "garages": "structure",
    "shed": "structure", "carport": "structure", "service": "structure", "transformer_tower": "structure",
}

LANDUSE_CATEGORY = {
    "residential": "residential", "commercial": "commercial_retail", "retail": "commercial_retail",
    "industrial": "industrial", "religious": "religious", "education": "educational",
    "military": "public_institutional", "construction": "construction",
}

FOOD = {"restaurant", "cafe", "fast_food", "food_court", "bar", "pub", "ice_cream", "biergarten"}
HEALTH = {"hospital", "clinic", "doctors", "dentist", "nursing_home"}
EDU = {"school", "college", "university", "kindergarten"}
PUBLIC = {"townhall", "police", "fire_station", "courthouse", "community_centre", "library", "post_office"}
LODGING = {"hotel", "hostel", "guest_house", "apartment", "motel"}
ANCHORS = ["market", "hotel", "healthcare", "educational", "religious", "public_institutional"]
COMMERCE = ("food_service", "commercial_office", "commercial_retail", "clinic")
COMMERCIAL_TAGS = ("commercial_retail", "commercial_office")


def poi_category(row) -> str | None:
    """Waste generator category implied by one POI, or None if it does not generate building waste."""
    get = lambda k: _clean(row.get(k))  # noqa: E731
    amenity, shop, office = get("amenity"), get("shop"), get("office")
    tourism, health, craft = get("tourism"), get("healthcare"), get("craft")
    if amenity == "marketplace":
        return "market"
    if tourism in LODGING:
        return "hotel"
    if amenity in ("hospital", "nursing_home") or health == "hospital":
        return "healthcare"
    if amenity in HEALTH or (isinstance(health, str) and health != "pharmacy"):
        return "clinic"
    if amenity in EDU or office == "educational_institution":
        return "educational"
    if amenity == "place_of_worship":
        return "religious"
    if amenity in PUBLIC or office == "government":
        return "public_institutional"
    if amenity in FOOD or shop in ("bakery", "confectionery", "pastry", "deli"):
        return "food_service"
    if amenity == "bank" or isinstance(office, str):
        return "commercial_office"
    if isinstance(shop, str) and shop != "vacant" or isinstance(craft, str) or health == "pharmacy" or amenity == "pharmacy":
        return "commercial_retail"
    return None


def _clean(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v) else v


def _levels(raw) -> float | None:
    if raw is None:
        return None
    m = re.match(r"\s*(\d+(\.\d+)?)", str(raw))
    return float(m.group(1)) if m else None


def _classify(building_tag, lu, poi_cats: list[str], footprint: float):
    """Return (category, commercial_sub_use, basis, confidence)."""
    tag_cat = BUILDING_TAG.get(building_tag) if building_tag and building_tag != "yes" else None
    if tag_cat in NO_REGULAR_WASTE:
        return tag_cat, None, f"OSM building={building_tag}", "high"

    anchors = [c for c in poi_cats if c in ANCHORS]
    commerce = [c for c in poi_cats if c in COMMERCE]
    if commerce:
        # Most frequent commercial use; ties go to the building's own tag, then to eateries.
        main_commerce = max(set(commerce), key=lambda c: (commerce.count(c), c == tag_cat, c == "food_service"))
        main_commerce = "healthcare" if main_commerce == "clinic" else main_commerce
    else:
        main_commerce = None
    # An institutional anchor (hospital, school, hotel...) defines the building unless it is
    # outnumbered by shops and offices inside a building tagged as commercial.
    if anchors and not (tag_cat in COMMERCIAL_TAGS and len(commerce) > len(anchors)):
        top = min(anchors, key=ANCHORS.index)
        return top, None, f"{len(poi_cats)} mapped place(s) inside, incl. {top}", "high"

    base, basis, conf = None, None, None
    if tag_cat:
        base, basis, conf = tag_cat, f"OSM building={building_tag}", "high"
    elif lu in LANDUSE_CATEGORY:
        base, basis, conf = LANDUSE_CATEGORY[lu], f"inside landuse={lu}", "medium"
    elif lu in ("cemetery", "recreation_ground", "grass", "orchard", "plant_nursery") or lu in ("park", "garden", "playground", "pitch"):
        base, basis, conf = "structure", f"inside {lu}", "medium"

    if base == "residential":
        base = "residential_apartment" if footprint >= NORMS["apartment_footprint_m2_if_untagged"] else "residential_house"
        basis += f", footprint {footprint:.0f} m2"
    if base in ("residential_house", "residential_apartment") and main_commerce:
        return "mixed_use", main_commerce, f"{basis}; {len(commerce)} shop/eatery/office inside", conf
    if main_commerce and (base is None or base in COMMERCIAL_TAGS):
        return main_commerce, None, f"{len(commerce)} mapped place(s) inside", "high"
    if base:
        return base, None, basis, conf
    return "unclassified", None, "no tag, landuse or place found; assumed household", "low"


HH = NORMS["household"]
FRACTIONS = [k for k in NORMS["dry_waste_fractions"] if not k.startswith("_")]
COMMERCIAL_CATS = ("food_service", "commercial_retail", "commercial_office", "hotel", "market")
INSTITUTIONAL_CATS = ("educational", "healthcare", "public_institutional", "religious")

BWG_GROUP = {
    "residential_apartment": ("residential", "residential societies"),
    "hotel": ("commercial", "hotels / hostels"),
    "healthcare": ("commercial", "hospitals, nursing homes"),
    "educational": ("institutional", "educational institutions"),
    "commercial_office": ("institutional", "private companies"),
    "commercial_retail": ("commercial", "commercial establishments, malls"),
    "food_service": ("commercial", "commercial establishments"),
    "mixed_use": ("commercial", "commercial establishments"),
    "market": ("commercial", "wholesale markets"),
    "industrial": ("commercial", "industrial units"),
    "public_institutional": ("institutional", "government / community places"),
    "religious": ("institutional", "community places or like"),
}
_LONG_DECIMALS = re.compile(r"(\d+\.\d{6})\d+")
WATCH_FRACTION = 0.7  # flag for survey when an estimate is within 70% of a threshold

COMPLIANCE_LABELS = {
    "processing_at_source": "Processes wet waste at source",
    "ebwgr_certificate": "Holds EBWGR certificate",
    "non_compliant": "No processing and no certificate",
    "not_verified": "Not yet surveyed",
}


class _Tally:
    """Accumulates waste by stream from several typology components."""

    def __init__(self):
        self.kg = 0.0
        self.streams = {s: 0.0 for s in STREAMS}
        self.dwellings = 0.0
        self.persons = 0.0

    def add(self, kg: float, share_key: str):
        self.kg += kg
        for s in STREAMS:
            self.streams[s] += kg * NORMS["stream_shares"][share_key][s]

    def household(self, dwellings: float):
        persons = dwellings * HH["persons_per_dwelling"]
        self.dwellings += dwellings
        self.persons += persons
        self.add(persons * HH["kg_per_capita_per_day"], "household")

    def floor_rate(self, cat: str, area: float):
        self.add(area / 100 * NORMS["non_residential"][cat]["kg_per_100m2"], cat)

    def per_unit(self, cat: str, units: float):
        if cat in NORMS["per_unit"]:
            self.add(units * NORMS["per_unit"][cat], cat)
        else:
            self.add(units * NORMS["per_unit"]["commercial_retail"], "commercial_retail")


def _typology_estimate(cat, sub_use, floor_area, levels, footprint) -> _Tally:
    """Estimate from OSM geometry and the guessed typology (no survey)."""
    t = _Tally()
    if cat in NO_REGULAR_WASTE:
        return t
    if cat == "residential_apartment":
        t.household(max(1.0, round(floor_area / HH["m2_floor_area_per_apartment"])))
    elif cat in ("residential_house", "unclassified"):
        t.household(max(1.0, levels * HH["dwellings_per_floor_house"]))
    elif cat == "mixed_use":
        com = NORMS["mixed_use"]["commercial_floors"]
        t.floor_rate(sub_use, footprint * com)
        t.household(max(0.0, levels - com) * HH["dwellings_per_floor_house"])
    else:
        t.floor_rate(cat, floor_area)
    return t


def _surveyed_category(use, units, osm_cat, osm_sub):
    su = NORMS["surveyed_use"]
    if use == "residential":
        apt = (units or 1) > su["apartment_if_units_over"] or osm_cat == "residential_apartment"
        return ("residential_apartment" if apt else "residential_house"), None
    if use == "commercial":
        if osm_cat in COMMERCIAL_CATS:
            return osm_cat, None
        return (osm_sub if osm_sub in COMMERCIAL_CATS else su["commercial_default"]), None
    if use == "mixed":
        sub = osm_sub or (osm_cat if osm_cat in COMMERCIAL_CATS else su["commercial_default"])
        return "mixed_use", sub
    if use == "institutional":
        return (osm_cat if osm_cat in INSTITUTIONAL_CATS else su["institutional_default"]), None
    if use == "vacant":
        return "structure", None
    if use == "construction":
        return "construction", None
    return osm_cat, osm_sub


def _bwg(cat, floor_area, levels_source, water_lpd, water_source, kg_day, quantity_source) -> dict:
    group = BWG_GROUP.get(cat)
    if not group:
        return {"status": "not_applicable", "criteria_met": [], "group": None}
    checks = {
        "floor_area_m2": (floor_area, f"footprint x floors, floors {levels_source}"),
        "water_litres_per_day": (water_lpd, f"from {water_source}" if water_source else None),
        "waste_kg_per_day": (kg_day, quantity_source),
    }
    met, near = [], []
    for c in regs.bwg_criteria():
        value, basis = checks[c["key"]]
        if value is None:
            continue
        text = f"{c['text']}: {value:,.0f} ({basis})"
        if value >= c["value"]:
            met.append(text)
        elif value >= WATCH_FRACTION * c["value"]:
            near.append(text)
    status = "bwg" if met else "watch" if near else "no"
    return {"status": status, "criteria_met": met or near, "group": f"{group[0]}: {group[1]}"}


def _compliance(bwg_status: str, survey: dict | None) -> str | None:
    if bwg_status != "bwg":
        return None
    onsite = (survey or {}).get("onsite_processing")
    if onsite in ("compost", "biogas"):
        return "processing_at_source"
    if onsite == "certificate":
        return "ebwgr_certificate"
    return "non_compliant" if onsite == "none" else "not_verified"


def _obligations(cat, dwellings, floor_area, bwg_status) -> list[dict]:
    rules = regs.swm()["generator_types"]
    out = []
    if bwg_status == "bwg":
        out.append({"rule": regs.cite("6"), "text": "Register on the CPCB portal. Process wet waste at source (composting or biogas) or obtain EBWGR certificates. Hand over dry, sanitary and special care waste to authorised agencies."})
    large = rules["large_premises_segregation_duty"]
    if cat in ("residential_apartment", "hotel", "food_service", "market") or (
        cat in ("educational", "healthcare", "public_institutional", "commercial_office") and floor_area > large["area_threshold_m2"]
    ):
        out.append({"rule": regs.cite(large["rule"]), "text": "Segregate at source and process biodegradable waste on premises as far as possible, by 28 Jan 2027."})
    dev = rules["development_plan_space"]
    if dwellings > dev["dwelling_threshold"] or floor_area > dev["plot_area_threshold_m2"]:
        out.append({"rule": regs.cite(dev["rule"]), "text": "Needs demarcated space for segregation, storage and decentralised processing."})
    if cat == "construction":
        out.append({"rule": regs.cite("5(1)(d)"), "text": "C&D waste: store separately and dispose under the C&D Waste Management Rules, 2025."})
    if cat == "healthcare":
        out.append({"rule": regs.cite("5(1)(k)"), "text": "Bio-medical waste must not be mixed with solid waste."})
    return out


def _collection(cat: str, sub_use: str | None, bwg_status: str) -> dict:
    if cat in NO_REGULAR_WASTE:
        if cat == "construction":
            return {"mode": "none", "label": "C&D waste collection on request", "rule": regs.cite("5(1)(d)")}
        return {"mode": "none", "label": "No regular collection", "rule": None}
    if bwg_status == "bwg":
        return {"mode": "bwg", "label": "BWG: gate collection of dry, sanitary and special care waste; wet waste processed at source or covered by EBWGR", "rule": regs.cite("6(b)")}
    if cat == "market":
        return {"mode": "daily_bulk", "label": "Daily collection of market waste", "rule": regs.cite("39(19)")}
    if cat == "food_service" or sub_use == "food_service":
        return {"mode": "daily_commercial", "label": "Door-to-door, four streams; daily wet waste pickup recommended", "rule": regs.cite("39(4)")}
    return {"mode": "door_to_door", "label": "Door-to-door collection, four streams", "rule": regs.cite("39(4)")}


def _generator_type(cat: str) -> str:
    if cat in NO_REGULAR_WASTE:
        return "none"
    if cat == "mixed_use":
        return "mixed"
    return "household" if cat in HOUSEHOLD else "non_residential"


def compute(base: dict, survey: dict | None = None) -> dict:
    """All derived fields for one building, from its OSM base record and optional survey record."""
    s = survey or {}
    footprint = base["footprint_m2"]
    if s.get("floors"):
        levels, levels_source = float(s["floors"]), "surveyed"
    else:
        levels = base["osm_levels"] or NORMS["default_levels"][base["category"]]
        levels_source = "from OSM" if base["osm_levels"] else "assumed"
    floor_area = footprint * levels

    cat, sub = base["category"], base["sub_use"]
    if s.get("use"):
        cat, sub = _surveyed_category(s["use"], s.get("units"), cat, sub)

    units = s.get("units")
    if s.get("use") and units is not None and cat not in NO_REGULAR_WASTE:
        t = _Tally()
        if cat in ("residential_house", "residential_apartment"):
            t.household(units)
        elif cat == "mixed_use":
            com = s.get("commercial_units") or 0
            t.per_unit(sub, com)
            t.household(max(0, units - com))
        elif cat in INSTITUTIONAL_CATS:
            t.floor_rate(cat, floor_area)
        else:
            t.per_unit(cat, units)
        quantity_source = "estimate: surveyed units x typology"
    else:
        t = _typology_estimate(cat, sub, floor_area, levels, footprint)
        quantity_source = "estimate: OSM footprint x typology" if not s.get("use") else "estimate: surveyed use x typology"

    streams = dict(t.streams)
    kg_day = t.kg
    if s.get("weighed_kg_day") is not None:
        scale = s["weighed_kg_day"] / kg_day if kg_day else 0
        streams = {k: v * scale for k, v in streams.items()}
        kg_day = s["weighed_kg_day"]
        quantity_source = "weighed"

    fractions = {f: streams["dry"] * NORMS["dry_waste_fractions"][f] for f in FRACTIONS}
    water = s.get("water_lpd")
    water_source = {"bwssb": "BWSSB data", "surveyor": "surveyor"}.get(s.get("water_source") or "surveyor") if water is not None else None
    bwg = _bwg(cat, floor_area, levels_source, water, water_source, kg_day, quantity_source)
    compliance = _compliance(bwg["status"], survey)

    return {
        "category": cat,
        "category_label": CATEGORY_LABELS[cat],
        "sub_use": sub,
        "generator_type": _generator_type(cat),
        "levels": levels,
        "levels_source": levels_source,
        "floor_area_m2": round(floor_area),
        "dwellings": round(t.dwellings, 1),
        "persons": round(t.persons, 1),
        "kg_day": round(kg_day, 2),
        **{k: round(v, 2) for k, v in streams.items()},
        **{f"dry_{f}": round(v, 2) for f, v in fractions.items()},
        "quantity_source": quantity_source,
        "water_lpd": water,
        "water_source": water_source,
        "bwg_status": bwg["status"],
        "bwg_criteria": bwg["criteria_met"],
        "bwg_group": bwg["group"],
        "bwg_compliance": compliance,
        "obligations": _obligations(cat, t.dwellings, floor_area, bwg["status"]),
        "collection": _collection(cat, sub, bwg["status"]),
        "surveyed": bool(survey),
        "survey": survey,
    }


BASE_FIELDS = ["id", "sector", "house_number", "street", "name", "address", "category", "sub_use", "basis",
               "confidence", "osm_building_tag", "landuse", "places_inside", "footprint_m2", "osm_levels"]


@lru_cache(maxsize=4)
def base_table(pilot_key: str) -> gpd.GeoDataFrame:
    """One row per mapped building with its OSM-derived classification. No estimates."""
    b = layers.buildings(pilot_key).to_crs(METRIC_CRS)
    lu = layers.landuse(pilot_key).to_crs(METRIC_CRS)
    p = layers.pois(pilot_key).to_crs(METRIC_CRS)
    sec = layers.sectors(pilot_key).to_crs(METRIC_CRS)

    b["footprint_m2"] = b.geometry.area
    cent = gpd.GeoDataFrame(b[["osm_id"]], geometry=b.geometry.representative_point(), crs=METRIC_CRS)

    # Smallest land-use polygon containing each building.
    lu = lu.assign(lu_class=lu["landuse"].fillna(lu["leisure"]).fillna(lu["amenity"]), lu_area=lu.geometry.area)
    j = gpd.sjoin(cent, lu[["lu_class", "lu_area", "geometry"]], predicate="within", how="left")
    j = j.sort_values("lu_area").groupby(level=0).first()
    b["landuse_class"] = j["lu_class"].reindex(b.index)

    # Sector by containment; buildings straddling the boundary take the nearest sector.
    j = gpd.sjoin_nearest(cent, sec[["name", "geometry"]], how="left").groupby(level=0).first()
    b["sector"] = j["name"].reindex(b.index)

    # POIs located inside each building.
    p = p.assign(category=p.apply(poi_category, axis=1))
    p = p[p["category"].notna()].copy()
    p["geometry"] = p.geometry.representative_point()
    pj = gpd.sjoin(p[["category", "name", "amenity", "shop", "office", "tourism", "geometry"]], b[["geometry"]], predicate="within")
    poi_by_b = pj.groupby("index_right")

    rows = []
    for i, r in b.iterrows():
        cats, names = [], []
        if i in poi_by_b.groups:
            g = poi_by_b.get_group(i)
            cats = list(g["category"])
            for _, q in g.head(6).iterrows():
                kind = _clean(q["amenity"]) or _clean(q["shop"]) or _clean(q["office"]) or _clean(q["tourism"])
                names.append(f"{_clean(q['name']) or 'unnamed'} ({kind})")
        tag = _clean(r.get("building"))
        cat, sub, basis, conf = _classify(tag, _clean(r["landuse_class"]), cats, r["footprint_m2"])
        hn, street = _clean(r.get("addr:housenumber")), _clean(r.get("addr:street"))
        address = ", ".join(x for x in [f"#{hn}" if hn else None, street, _clean(r.get("addr:full"))] if x) or None
        rows.append({
            "id": r["osm_id"], "sector": _clean(r["sector"]), "house_number": hn, "street": street,
            "name": _clean(r.get("name")) or _clean(r.get("addr:housename")), "address": address,
            "category": cat, "sub_use": sub, "basis": basis, "confidence": conf,
            "osm_building_tag": tag, "landuse": _clean(r["landuse_class"]), "places_inside": names,
            "footprint_m2": round(r["footprint_m2"]), "osm_levels": _levels(_clean(r.get("building:levels"))),
        })
    out = gpd.GeoDataFrame(rows, geometry=b.geometry.values, crs=METRIC_CRS)
    return out.to_crs(4326)


def _base_records(pilot_key: str) -> dict[str, dict]:
    records = base_table(pilot_key)[BASE_FIELDS].to_dict("records")
    return {r["id"]: {k: (v if isinstance(v, list) else _clean(v)) for k, v in r.items()} for r in records}


@lru_cache(maxsize=4)
def _base_records_cached(pilot_key: str) -> dict[str, dict]:
    return _base_records(pilot_key)


def building_properties(pilot_key: str, building_id: str, survey: dict | None) -> dict:
    base = _base_records_cached(pilot_key)[building_id]
    return {**base, **compute(base, survey), "osm_category": base["category"], "osm_sub_use": base["sub_use"]}


def has_building(pilot_key: str, building_id: str) -> bool:
    return building_id in _base_records_cached(pilot_key)


MAP_FIELDS = ["id", "sector", "house_number", "name", "address", "category", "generator_type", "confidence",
              "bwg_status", "bwg_compliance", "surveyed", "levels", "kg_day", *STREAMS, *[f"dry_{f}" for f in FRACTIONS]]


def map_properties(props: dict) -> dict:
    """The subset of a building's properties the map needs for styling, filtering and search."""
    return {k: props[k] for k in MAP_FIELDS}


@lru_cache(maxsize=4)
def buildings_geojson_text(pilot_key: str) -> str:
    """Light GeoJSON of all buildings with OSM-only estimates. Survey overrides are sent separately."""
    t = base_table(pilot_key)
    props = [map_properties(building_properties(pilot_key, bid, None)) for bid in t["id"]]
    gdf = gpd.GeoDataFrame(props, geometry=t.geometry.values, crs=4326)
    text = gdf.to_json(drop_id=True)
    return _LONG_DECIMALS.sub(lambda m: m.group(1), text)


def all_properties(pilot_key: str, surveys: dict[str, dict]) -> list[dict]:
    return [building_properties(pilot_key, bid, surveys.get(bid)) for bid in _base_records_cached(pilot_key)]


def landuse_geojson(pilot_key: str) -> dict:
    lu = layers.landuse(pilot_key)
    lu = lu.assign(lu_class=lu["landuse"].fillna(lu["leisure"]).fillna(lu["amenity"]))
    return json.loads(lu[["osm_id", "lu_class", "name", "geometry"]].to_json(drop_id=True))


def sectors_geojson(pilot_key: str) -> dict:
    return json.loads(layers.sectors(pilot_key).to_json(drop_id=True))


def _py(v):
    """Convert numpy scalars inside nested structures to plain Python for JSON."""
    if isinstance(v, dict):
        return {k: _py(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_py(x) for x in v]
    return v.item() if hasattr(v, "item") and not isinstance(v, (str, bytes)) else v


def summary(pilot_key: str, surveys: dict[str, dict]) -> dict:
    t = pd.DataFrame(all_properties(pilot_key, surveys))
    area_km2 = layers.sectors(pilot_key).to_crs(METRIC_CRS).area.sum() / 1e6
    tpd = {s: round(t[s].sum() / 1000, 2) for s in STREAMS}
    dry_tpd = {f: round(t[f"dry_{f}"].sum() / 1000, 2) for f in FRACTIONS}
    dep = regs.swm()["waste_streams"]["streams"][3]["deposition_centre_norm"]
    fraction_labels = {f["key"]: f["label"] for f in regs.swm()["waste_streams"]["dry_waste_fractions"]["fractions"]}

    by_sector = []
    for name, g in t.groupby("sector"):
        by_sector.append({
            "sector": name, "buildings": len(g), "surveyed": int(g["surveyed"].sum()),
            "households_est": round(g["dwellings"].sum()),
            "tpd": round(g["kg_day"].sum() / 1000, 2),
            **{f"{s}_tpd": round(g[s].sum() / 1000, 2) for s in STREAMS},
            "bwg": int((g["bwg_status"] == "bwg").sum()),
        })
    bwgs = t[t["bwg_status"] == "bwg"].sort_values("kg_day", ascending=False)
    to_survey = t[(~t["surveyed"]) & t["bwg_status"].isin(["bwg", "watch"])].sort_values("kg_day", ascending=False)

    def item(r):
        return {"id": r["id"], "name": r["name"], "address": r["address"], "sector": r["sector"],
                "category_label": r["category_label"], "kg_day": r["kg_day"], "criteria": r["bwg_criteria"],
                "compliance": r["bwg_compliance"], "bwg_status": r["bwg_status"]}

    return _py({
        "pilot": pilot_key,
        "area_km2": round(area_km2, 2),
        "buildings": len(t),
        "surveyed": int(t["surveyed"].sum()),
        "with_house_number": int(t["house_number"].notna().sum()),
        "with_street": int(t["street"].notna().sum()),
        "with_levels": int(t["levels_source"].eq("from OSM").sum()),
        "category_counts": t["category"].value_counts().to_dict(),
        "category_labels": CATEGORY_LABELS,
        "generator_type_counts": t["generator_type"].value_counts().to_dict(),
        "confidence_counts": t["confidence"].value_counts().to_dict(),
        "bwg_counts": t["bwg_status"].value_counts().to_dict(),
        "compliance_counts": t["bwg_compliance"].value_counts().to_dict(),
        "compliance_labels": COMPLIANCE_LABELS,
        "households_est": round(t["dwellings"].sum()),
        "population_est": round(t["persons"].sum()),
        "total_tpd": round(t["kg_day"].sum() / 1000, 2),
        "stream_tpd": tpd,
        "dry_fraction_tpd": dry_tpd,
        "dry_fraction_labels": fraction_labels,
        "by_sector": sorted(by_sector, key=lambda r: int(re.sub(r"\D", "", r["sector"] or "0") or 0)),
        "bwg_list": [item(r) for _, r in bwgs.head(150).iterrows()],
        "survey_priority": [item(r) for _, r in to_survey.head(60).iterrows()],
        "mrf_inflow": {"dry_tpd": tpd["dry"], "fraction_tpd": dry_tpd, "rule": regs.cite("9")},
        "special_care": {
            "tpd": tpd["special"],
            "deposition_centres_required": math.ceil(area_km2 / dep["one_centre_per_km2"]),
            "rule": regs.cite(dep["rule"]),
        },
        "assumptions_note": NORMS["_status"],
    })
