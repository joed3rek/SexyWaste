# CityLoom architecture audit

This audit compares the code as it stands (October 2026, branch `main`) with the "connected municipal system" brief. The brief asks for this audit before any refactor.

It covers:
- what exists;
- where each idea in the brief already lives in the code;
- what is disconnected or duplicated;
- an incremental plan that keeps working features working.

Decisions taken so far:

- The product name is **CityLoom**.
- HSR Layout **sectors 1–7 stand in for wards** for now. Zones are not modelled until a second area is added.
- The audit comes first. No code changes until it has been read.

## Progress

**Step 1 is built (service requirements and demands).** It lives in `backend/demand/`, `backend/api/demand_api.py` and `tests/test_demand.py`.
- Requirements are read in one shape from the cycle, streets, bins and GVPs.
- Demands are stored in `ops.db`, can be regenerated safely, and close themselves with a reason when no longer needed.
- GVP moves sync their demands.
- The route builder plans a **date** from stored collection demands. Before and after the switch, the points, loads and order fed to the optimiser are identical for every day, sector and stream set tried.

Two changes from the plan below:
- **`gvp_demand` stays** as the GVP's own record of cleared waste in `survey.db`; the service demand mirrors it. The two tables are in different databases, so folding one into the other waits for the database decision in section 6.
- **A bug found on the way:** building classification broke ties between POI types by Python's random set order. A few buildings changed category between server starts, so point IDs and kilograms shifted. Ties now break in a fixed order, and all 11,505 buildings classify the same in every run.

**Steps 2–9 are built** on branch `feature/connected-system`, one commit per step. Each step has its own module, router and tests.

| Step | Module | What it does | Page |
|---|---|---|---|
| 2 Facilities | `backend/facilities` | Depot, truck yard, transfer stations, MRF and other places, one record each; the route builder loads and saves them | Route builder: *Save places* |
| 3 Route plan contract | `backend/plans` | Every optimiser run kept as a plan with routes and stops; feasibility with reasons and actions; adoption marks demands `planned` and assigns vehicles and crews; a vehicle's routes are derived from adopted plans | Route builder: plan box, *Adopt* |
| 4 Work planner | `backend/workplan` | Cleaning demands given to cleaning staff and equipment; shortages in worker-hours; adoption | Clean City: *Work plan* tab |
| 5 Operations | `backend/operations` | Append-only actuals for demands and routes, with context: the operational memory | Operations |
| 6 Performance | `backend/performance` | Required vs delivered, reasons, route estimate accuracy, GVP response; planning alerts from repeated failure | Performance |
| 7 Shared map | `frontend/citymap.js` | One base map for every map page, and shared layer groups read from the domain APIs | City map |
| 8 Processing | `backend/processing` | Facility intake, recovery, rejects, disposal; utilisation; observed vs assumed dry fractions; capacity alerts | Processing |
| 9 Command centre | `backend/command` | Service level, failures, today's work and the reasons per sector; the planner's home page | Today |

Fixes found on the way:
- Sweeping frequencies with a half (3.5 a week) lost the half. They now carry over between weeks.
- Streets of one frequency all fell on the same days. Each street now has a fixed stagger, so the daily load is level.
- The active navigation item and the role guard came from a hand-kept list, so new pages were open to every role. Both now come from `NAV`.

**Collection Intelligence Agent brief.** Steps 1–9 are the operational foundation that brief asks for in its phases 1, 2 and 4:
- demand, resources, constraints, GIS and database;
- routing with validation and planning exceptions;
- planned vs actual, operational history and failure signals.

Its later phases build on these records and are not started:
- the agent's tool layer and candidate-plan comparison;
- prediction of demand, travel and service time, and risk;
- model versioning and back-testing.

**Data folder.** All stored data sits in one folder, `DATA_DIR` in `backend/config.py`: `data/` in the repository, git-ignored. It can be moved with the `CITYLOOM_DATA_DIR` environment variable.

---

## 1. What the system is today

### Stack

- **Backend:** one FastAPI app (`backend/api/main.py`) with routers for survey, resources, cycle and Clean City. It also serves the frontend.
- **Frontend:** plain HTML and JavaScript pages, one script per page. MapLibre GL draws the maps. A shared shell in `frontend/ui.js` provides navigation, icons, strings, the session and `swmWrite()`.
- **Routing:** OR-Tools (`pywrapcp`) over an osmnx/networkx street graph (`backend/routing/`).
- **Stores:**
  - Two SQLite databases, described in the table below.
  - Cached OSM layers in `data/cache/`.
  - Assumption files in `backend/buildings/norms.json` and `reference/*.json`.
  - The regulations library in `backend/regulations/library/`.
- **Auth:** a dummy login. Role, name and sectors arrive as unverified `X-SWM-*` headers. `backend/auth/roles.json` defines 15 roles; 7 are built. Each role sees only its own pages.

### Data stores

| Store | Tables | Rules |
|---|---|---|
| `data/survey.db` | `visit`, `use_mix`, `field_value`, `geometry_flag`, `photo`, `review_item`, `sector_assignment`, `gvp`, `gvp_observation`, `gvp_photo`, `gvp_event`, `gvp_demand` | Field values and GVP history are append-only (triggers). Values carry a source: assumed < surveyed < verified < weighed. |
| `data/ops.db` | `vehicle`, `staff`, `equipment`, `resource_log`; `collection_schedule`, `cycle_log`; `street_plan`, `public_bin`, `cleancity_log` | Logs are append-only. Schedules are deactivated, never deleted. |
| OSM cache | sectors, buildings, land use, POIs, street graph | Read-only. Survey values override OSM. |
| Memory only | route plan jobs (`_JOBS` in `main.py`) | Lost on restart. |
| Browser only | depot, MRF, yard and transfer-station positions in the route builder (`state` in `builder.js`) | Not stored anywhere. |

### Pages

| Page | Role | What it does |
|---|---|---|
| `index.html` | everyone | Opening page and sign-in (Public or Official) |
| `report.html` | Public | Report dumped waste by device GPS |
| `surveyor.html` | Surveyor | Building visits, map problems, GVP reports |
| `supervisor.html` | Survey supervisor | Progress, spot checks, review items, GVP lifecycle |
| `map.html` | Planner | Buildings by estimated quantity, streams, BWG status |
| `cycle.html` | Planner | Collection cycle schedules |
| `cleancity.html` | Planner | Street classes, public bins, GVP clearing, workforce |
| `builder.html` | Planner | Two-tier route plan for one sector |
| `resources.html` | Fleet manager, HR manager | Vehicles, people, machinery |
| `admin.html`, `rules.html` | Admin, all | Admin and the rules library |

There are **five separate maps**: planner map, route builder, Clean City, surveyor and public report. Each builds its own layers.

### APIs

There are about 70 endpoints, all under `/api/pilots/{pilot}/…`. About half are built around domain objects, for example `resources/{kind}`, `gvps`, `cycle`, `cleancity/bins` and `visits`. The other half are built around screens, for example `v2/points`, `v2/plan`, `cleancity/workload`, `surveyor/home` and `summary`.

---

## 2. The brief's entities, mapped to the code

Status key:
- **Stored:** a table holds it.
- **Computed:** derived on request, not stored.
- **Missing:** not in the code.

| Brief entity | Where it is now | Status |
|---|---|---|
| City | `config.PILOTS["hsr"]` (city, population estimate) | Config |
| Zone | — | Missing (not needed for HSR) |
| Ward | HSR sectors from OSM boundaries | Stand-in |
| Street, StreetSegment | OSM street graph. Clean City `street_plan` stores a class per segment. GVPs and bins snap to segments through `gvp.snap_to_road`. | Computed, plus stored overrides |
| Property, Building | OSM footprints, plus survey visits and field values | Stored (survey), computed (OSM) |
| Unit | `use_mix` rows (dwellings, shops, beds) | Stored |
| WasteGenerator | `buildings/generators.py`: classification, estimates, BWG check | Computed |
| WasteProfile, WasteStream | `norms.json` (estimates). The four streams come from the regulations library. | Assumption files |
| GVP | `gvp` with observations, photos, events | Stored, append-only |
| PublicBin | `public_bin` | Stored |
| CollectionPoint | `routing/points.py`: street runs built from buildings | Computed on every request |
| ServiceRequirement | Spread over three places: `collection_schedule` (collection), `street_plan` classes plus `clean_city.json` (sweeping), the bin service rule in `cleancity` | Exists in pieces; no shared shape |
| ServiceDemand | `gvp_demand` (cleared GVP pickups) only. Everything else is rebuilt inside the planner. | Mostly missing |
| CleaningTask | `gvps/tasks`: verified GVPs as tasks | Computed (GVPs only) |
| CleaningAssignment | GVP `assigned` event with an assignee | GVPs only |
| CollectionSchedule | `collection_schedule` | Stored |
| CollectionDemand | Assembled inside `twotier._sector_points` and again in `main.get_points_v2` | Computed, in two places |
| Vehicle | `vehicle`, one record used by the route builder | Stored |
| Worker, Driver | `staff` (driver is a role) | Stored |
| Equipment | `equipment` | Stored |
| Route, RouteStop, RouteAssignment | Plan job result in memory | Not stored |
| TransferStation, MRF | Route builder browser state; stations are suggested by `v2/stations` | Not stored |
| ProcessingFacility | Park composting sites (`routing/parks.py`), computed | Partly |
| Inspection | — | Missing |
| Operation (actuals) | — | Missing |
| PerformanceRecord | — | Missing |

---

## 3. The brief's sections, mapped to features

| Section | Already built | Gap |
|---|---|---|
| City Intelligence | Buildings and estimates, BWG check, GVPs with lifecycle and recurrence state, public bins, street classes | Hotspots and recurring-problem analysis; one shared map |
| Service Planning | Collection cycle with gap checks (r. 8(h)(iii)); sweeping frequency per street class | Requirements in one shape; stored demands; planning alerts |
| Resources | Vehicles, people, machinery; five statuses; own sector plus shared pool; crew check; GPS rule (r. 8(h)(ix)) | Facilities; required vs available as one capacity view |
| City Cleanliness | Workload in worker-hours vs available; GVP clearing tasks | Work planner: assigning demands to workers and equipment; inspections |
| Collection | Two-tier OR-Tools plan; cycle days; GVP pickups; due bins; fleet capped by inventory; Suggest fleet | Stored routes; a clear "infeasible" result; tracking; missed collections |
| Processing | MRF placed per plan; park composting | Facilities with capacity, intake, recovery |
| Performance | — | Planned vs actual, gap, reason |
| Command Centre | — | Last, by design |

---

## 4. Disconnects and duplicates

1. **Collection demand is built inside the optimiser, in two places.** `twotier._sector_points` reads the cycle, GVP pickups and due bins and builds the stops. `main.get_points_v2` repeats this assembly for the map. This breaks brief rule 4 ("do not allow the route optimiser to invent demand") and rule 1 (one representation per object).
2. **There is no stored demand, so nothing can be tracked.** A demand cannot be planned, done or missed, because it only exists while a plan runs. Performance (required vs delivered) has nothing to compare against.
3. **Facilities exist only in the browser.** The depot, MRF, yard and transfer stations are placed by hand on every plan and forgotten afterwards. Processing has nothing to attach to.
4. **Plans and routes are not stored.** They live in memory and are lost on restart. A vehicle's current route cannot be derived from the vehicle record, which section 25 asks for.
5. **Cleaning work is a total, not tasks.** Clean City computes worker-hours for sweeping, bins and GVPs, but street sweeping never becomes a demand anyone is assigned to.
6. **Requirements have three different shapes.** Collection, sweeping and bin service are each stored and read differently, so "what service does the city need?" cannot be asked in one query.
7. **Data is split across two databases.** GVPs and their pickups are in `survey.db`; bins, resources and schedules are in `ops.db`. Joins happen in Python.
8. **There are five maps.** Each page builds its own layers from its own endpoint. The underlying data is shared on the server, so this is a view problem, not a data problem.
9. **Small business rules sit in the browser.** For example, `report.js` works out the sector with its own point-in-polygon test, while the server also snaps the report to a road.

## 5. The brief's coding rules: where the code stands

| Rule | Status |
|---|---|
| 1. No duplicate structures for one object | Mostly kept (one vehicle, one GVP, one bin). Collection demand is assembled twice. |
| 2. No module-specific copies of shared data | Kept |
| 3. No schedules hard-coded in the optimiser | Kept: the cycle is data |
| 4. The optimiser must not invent demand | **Broken:** demand is built inside `twotier` |
| 5. Bins are not permanent route stops | Kept: only due, full or overflowing bins |
| 6. Cleaning and collection kept separate | Kept |
| 7. The dashboard is not the source of truth | Kept (there is no dashboard yet) |
| 8. No AI where rules suffice | Kept |
| 9. Understand before rewriting | This document |
| 10. Every feature states its entity flow | To apply from now on |

---

## 6. Proposed refactor plan

Each step is small, keeps the existing screens working, and ends with the full test suite passing. The order differs from the brief in one place: the **shared map comes later**. The spatial data is already shared on the server, so merging the five maps is a view change. Stored demands come first because every later step depends on them.

### Step 1: Service requirements and service demands

**Requirements: a read model over what already exists, not a new copy.** Rule 1 forbids duplicating the schedules, so requirements are not copied into a new table. A function `requirements(pilot, sector)` returns one shape over the existing sources:

| Kind | Source |
|---|---|
| `collection` | `collection_schedule` rows (stream, generator, days, window) |
| `sweeping` | `street_plan` class × `clean_city.json` frequency per class |
| `bin_service` | each `public_bin` with its service rule |
| `gvp` | event-driven: no standing requirement |

**Demands: a new stored table in `ops.db`,** with an append-only event log:

```text
service_demand
  id, kind (collection | cleaning | bin_service)
  source_type, source_id      e.g. schedule:12, street:seg-881, bin:B-034, gvp:34
  sector, location (segment or point id, lon/lat)
  stream, generator           collection only
  quantity_kg | length_m      the expected amount
  service_date, window_start, window_end, priority
  status                      open → planned → done | missed | cancelled
  created_at
service_demand_event          append-only: status changes, actual quantity later
```

**Generation, which can run repeatedly without duplicating:** `generate(pilot, date, sector)` creates the day's demands. A unique key on (source, date) means running it twice changes nothing.
- **Collection:** for each street run whose stream and generator the cycle collects that day, one demand. The quantity is the daily estimate × the days built up since the last collection, as the cycle already computes it.
- **Sweeping:** for each street segment due that day by its class frequency, one cleaning demand, sized by length.
- **Bins:** bins that are full, overflowing or due become bin-service demands.

**Events:**
- A GVP that is **verified** creates a cleaning demand.
- A GVP that is **cleared** creates a collection demand. This replaces `gvp_demand`; its rows are migrated.

**Consumers switch over without changing their screens:**
- The route builder (`twotier`) reads open **collection** demands for the sector and date instead of assembling stops itself. `get_points_v2` reads the same demands. That removes the duplicate.
- Clean City reads **cleaning** demands for its task list and workload.

**APIs, built around the domain objects:**
- `GET /api/pilots/{p}/service-requirements`
- `GET /api/pilots/{p}/service-demands?date=&sector=&kind=`
- `POST /api/pilots/{p}/service-demands/generate`

**Tests:**
- Generation is idempotent.
- Each source creates the right demand.
- The GVP verify and clear events create demands.
- Today's route plan for Sector 1 gives the same result as before the switch.

### Step 2: Facilities as stored objects

Store the depot, yard, transfer stations and MRF (and park composting sites) in `ops.db`, with capacity. The route builder loads and saves them instead of keeping them in the browser. This gives Processing something to attach to.

### Step 3: The route plan contract

Store plans, routes and route stops, linked to vehicles, staff and the demands they serve. Return a clear **planning exception** when demands cannot be served: what is short (vehicle capacity, crew, time window) and the options. A route plan marks its demands `planned`.

### Step 4: Work planner for cleaning

Assign cleaning and bin-service demands to workers and equipment by sector, shift, skills and productivity. Output a day's work plan per team. This uses the existing `staff` and `equipment` records.

### Step 5: Operations: actuals

Record what happened against each demand and route: done or missed, actual kilograms, metres swept, kilometres driven, time. Entry starts as a simple form for supervisors, since there is no driver or collector interface yet.

### Step 6: Performance

Required vs delivered per sector, kind and day: service level, gap, and the reason from the linked resources and events. Recurring GVPs become **planning alerts** (section 30).

### Step 7: One shared map

One map component with the brief's layer groups (base, waste intelligence, cleanliness, collection, resources, processing). Each page opens it with its own layers on.

### Step 8: Processing

Facility intake, recovery and rejects, feeding capacity alerts back into planning.

### Step 9: Command centre

Service status, exceptions, capacity gaps, today's demands and performance, built on steps 1–8.

### Open questions for later steps

- Whether to merge `survey.db` and `ops.db` into one database. Not needed for step 1: demands refer to GVPs by ID.
- How fine-grained collection demands should be. Step 1 proposes one per street run per stream per day, about 350 runs for each sector.
