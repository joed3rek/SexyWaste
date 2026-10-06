# CityLoom: Urban Waste Intelligence Platform (SWM)

**CityLoom, Municipal Operations Intelligence.** An AI-enabled, GIS-based decision-support desktop application for municipal solid waste management in Indian cities.**

It covers the full waste value chain: generation, collection, routing, transfer, processing, material recovery, markets and the circular economy. The first pilot is a representative ward cluster in Bengaluru.

> **Working title:** *AI-enabled Urban Waste Intelligence: integrating spatial intelligence, predictive analytics and optimisation for circular municipal solid waste management in Indian cities.*
>
> **Institutional title:** *Intelligent Urban Solid Waste Management Systems: a GIS, AI and circular-economy framework for Indian Urban Local Bodies.*

---

## Table of contents

1. [Why this project](#1-why-this-project)
2. [Research question and hypothesis](#2-research-question-and-hypothesis)
3. [Design principles](#3-design-principles)
4. [System architecture](#4-system-architecture)
5. [Modules](#5-modules)
6. [Data sources: Google Maps, Google Earth Engine and others](#6-data-sources)
7. [Technology stack](#7-technology-stack)
8. [Proposed repository structure](#8-proposed-repository-structure)
9. [Research workstreams and roadmap](#9-research-workstreams-and-roadmap)
10. [Pilot: Bengaluru](#10-pilot-bengaluru)
11. [Key performance indicators](#11-key-performance-indicators)
12. [Getting started](#12-getting-started)
13. [Licensing and data-use notes](#13-licensing-and-data-use-notes)
14. [Contributing](#14-contributing)

---

## 1. Why this project

India's **Solid Waste Management Rules, 2026** came into force on 1 April 2026, replacing the 2016 rules. They introduce:

- **Four-stream source segregation:** wet, dry, sanitary and special-care waste.
- **Stronger online tracking** of waste flows, including the CPCB centralised SWM portal.
- **Expanded Bulk Waste Generator (BWG) responsibilities.** BWG thresholds include 20,000 m² floor area, 40,000 litres/day water consumption or 100 kg/day of solid waste.
- **Circular-economy principles** across the chain.

> ⚠️ Verify all regulatory details against the official gazette notification before relying on them in the application or in publications.

Many Indian cities already run digital SWM systems: GPS vehicle tracking, weighbridges, attendance and blackspot surveillance. What is missing is the **intelligence that connects these datasets**. This project asks what that missing layer should look like, and builds it.

The goal is not "collect garbage efficiently". The goal is to:

> **Maximise resource recovery while minimising the environmental and financial cost of the waste-management system.**

---

## 2. Research question and hypothesis

**Central hypothesis**

> Indian municipal solid waste systems can be made more efficient, resource-efficient and environmentally sustainable by integrating GIS, artificial intelligence, operational data and circular-economy intelligence across the waste value chain, from generation and collection to processing, recovery and disposal.

**Research question**

> How can AI-enabled spatial and operational intelligence improve the planning, routing, collection, processing and resource recovery of municipal solid waste in Indian cities?

**Sub-questions**

1. Where and when is waste generated?
2. How should it be collected and transported?
3. Where should it be processed?
4. How can material recovery and circular value be maximised?
5. How can this intelligence be embedded into municipal decision-making?

---

## 3. Design principles

1. **Do not start with AI.** Build in this order: `SYSTEM → DATA → GIS → OPTIMISATION → AI → PLATFORM`. AI is added only where it measurably beats simpler methods.
2. **Shortest is not optimal.** Routing optimises a whole collection system, not just kilometres.
3. **Smart bins are a data source, not the architecture.** Indian door-to-door collection means the household is often the "bin". The model is built around *generators + collection points + vehicles + facilities*.
4. **Four streams everywhere.** Every dataset, forecast and route is stream-aware: wet, dry, sanitary and special-care.
5. **Urban form drives system design.** Land use, density and street morphology decide which collection system suits a neighbourhood.
6. **Data baseline first.** Conflicting figures across sources are treated as a finding, not noise.
7. **The portal is the output, not the starting point.**
8. **AI sits across the system, not in one box.**

---

## 4. System architecture

```
                    URBAN SYSTEM
                         │
        ┌────────────────┼────────────────┐
        ↓                ↓                ↓
   LAND USE          POPULATION       ACTIVITIES
        │                │                │
        └────────────────┼────────────────┘
                         ↓
                WASTE GENERATION
                         │
              AI FORECASTING ENGINE
                         ↓
              WASTE FLOW DATABASE
                         │
       ┌─────────────────┼──────────────────┐
       ↓                 ↓                  ↓
   COLLECTION         ROUTING          SCHEDULING
       │                 │                  │
       └─────────────────┼──────────────────┘
                         ↓
                  TRANSPORT SYSTEM
                         │
              ┌──────────┼──────────┐
              ↓          ↓          ↓
             MRF     PROCESSING   RECYCLER
              │          │          │
              └──────────┼──────────┘
                         ↓
                 RESOURCE RECOVERY
                         │
              ┌──────────┼──────────┐
              ↓          ↓          ↓
           MATERIAL    ENERGY    COMPOST
              │          │          │
              └──────────┼──────────┘
                         ↓
                  CIRCULAR ECONOMY
                         │
                         ↓
              MUNICIPAL DECISION
                   SUPPORT PORTAL
```

### Platform layers

| Layer | Name | Contents |
|---|---|---|
| 1 | Spatial intelligence | Wards, parcels and buildings, land use, roads, generators, collection points, blackspots, MRFs, processing facilities, landfills, markets |
| 2 | Waste intelligence | Quantity, composition, generation forecasts, segregation levels, seasonal variation |
| 3 | Operations | Vehicles, routes, schedules, workforce, capacities, missed collections |
| 4 | AI | Forecasting, routing, anomaly detection, computer vision, demand prediction, infrastructure optimisation |
| 5 | Circular economy | Material flows, recyclers, MRFs, markets, recovered materials, revenue, avoided landfill |
| 6 | Decision support | Scenario comparison: cost, environmental impact, service coverage, recovery, infrastructure need |

---

## 5. Modules

### M1. Urban Waste GIS

The spatial foundation. Every other module reads from and writes to it.

- Ward boundaries, buildings, land use, and a road network with hierarchy, width and one-way rules.
- Facilities: MRFs (Material Recovery Facilities), DWCCs (Dry Waste Collection Centres), transfer stations, composting, biomethanation, RDF (Refuse-Derived Fuel) plants, landfills and recyclers.
- An **Urban Waste Intelligence Map** with three hotspot classes:

| Hotspot class | Examples |
|---|---|
| **Generation hotspots** | Markets, restaurants, commercial streets, apartments, institutions, hospitals, hotels, construction sites, transit nodes, event spaces, informal settlements, street-vending areas |
| **Leakage hotspots** | Repeated littering, illegal dumping, overflowing points, missed collection, mixed waste, roadside accumulation, drain dumping, vacant-land dumping |
| **Infrastructure hotspots** | MRFs, DWCCs, transfer stations, composting, biomethanation, RDF, landfills, recycling facilities |

Blackspots are referred to as Garbage Vulnerable Points (GVPs) in Indian municipal usage.

### M2. Waste Generation Forecasting Engine

Predicts **where, how much, what type and when** waste is generated.

| Input group | Features |
|---|---|
| Spatial | Population, households, land use, built-up area, commercial intensity, floor area, road density, markets, institutions |
| Temporal | Day of week, month, season, holidays, festivals, events |
| Environmental | Rainfall, temperature, flooding |
| Operational | Past collection quantities, missed collections, vehicle availability, processing capacity |

Example outputs:

- "Ward X is expected to generate 18% more wet waste tomorrow."
- "Collection point Y is likely to exceed capacity between 5 and 7 pm."

Models progress from rate-based baselines to gradient boosting to sequence models such as LSTM. Each model is kept only if it beats the simpler baseline on held-out municipal data.

### M3. Bulk Waste Generator and land-use mapping

Estimates generation by property type using GIS and land-use data. It moves a city from *"we have a list of BWGs"* to *"we have a spatially complete picture of the waste-generating landscape"*.

Generator types include residential, apartments and RWAs, commercial, restaurants, hotels, institutions, markets and industrial. Each has its own generation rate, composition, collection frequency, vehicle need and processing pathway.

### M4. Collection Optimisation Engine

A capacitated, time-windowed, multi-stream vehicle routing engine.

- **Inputs:** waste forecast, collection points, stream, fleet, vehicle capacity, road network, facilities and time windows.
- **Outputs:** which vehicle collects which points, in what sequence, at what time, for which stream, where it unloads, and whether extra vehicles are needed.
- **Constraints:** traffic, one-way roads, road width, loading time, labour, vehicle type, facility capacity, neighbourhood density, seasonal variation, flooding and fuel use.
- **Dynamic rerouting:** triggered by breakdowns, blocked or flooded roads, points filling early, volumes above forecast, or an MRF reaching capacity.

Three modes are compared: **static**, **optimised** and **dynamic** routing.

### M5. Collection System Design Optimiser

Compares whole collection systems rather than individual routes.

| System | Flow |
|---|---|
| A | Door-to-door → direct processing |
| B | Door-to-door → secondary collection → MRF |
| C | Door-to-door → decentralised MRF → recycler |
| D | Door-to-door → transfer station → central processing |
| E | Hybrid |

It recommends a system by **urban typology**:

| Urban typology | Likely system |
|---|---|
| Dense old city | High-frequency small vehicles |
| Large apartments | Scheduled bulk collection |
| Market | High-frequency wet-waste collection |
| Low-density residential | Door-to-door |
| Commercial corridor | Timed collection |
| Industrial area | Specialised streams |
| Peripheral growth area | Decentralised infrastructure |
| Informal settlement | Adapted collection points |

### M6. Resource Recovery Optimiser

Allocates each material stream to the **least-cost, highest-value pathway**, not simply the nearest facility.

```
Paper   → MRF A → Recycler X
Plastic → MRF B → Recycler Y
Organic → Decentralised composting / biomethanation
```

It uses facility capacity, current throughput, recovery rates and market prices. This is a multi-objective problem:

- **Minimise:** cost, distance, fuel, emissions, missed collection and landfill disposal.
- **Maximise:** coverage, segregation, material recovery, processing efficiency and recovered value.

### M7. AI Vision (optional, evidence-led)

Computer vision on images from collection vehicles, MRFs, streets, CCTV and field workers.

- **Source segregation:** classify loads as wet, dry, sanitary or special-care.
- **Public space:** detect litter, overflow, illegal dumping, construction debris, mixed waste and open burning.
- **MRF sorting:** identify PET, HDPE, paper, cardboard, metal, glass and rejects.

The research question here is *where in the chain vision adds real value*, not just whether garbage can be classified.

### M8. Economics and circularity

Calculates cost per tonne, per household and per km. It also covers fuel, labour, vehicle utilisation, processing cost, material revenue, landfill-avoidance value, carbon emissions and circular value.

### M9. Scenario planner and digital twin

A living model of where waste is generated, how it moves, where it is processed and where residuals end up. It is not necessarily a 3D model. Example "what if" scenarios:

- Add an MRF at a given site.
- Change a ward's collection frequency.
- Increase waste generation by 20%.
- Decentralise wet-waste processing.
- Add 50 collection vehicles.
- Festival peak.
- Flooded roads.

Standard comparison set:

| Scenario | Description |
|---|---|
| A | Current system |
| B | Optimised routing |
| C | Decentralised processing |
| D | Routing + decentralised processing + MRF optimisation |

---

## 6. Data sources

### Google Maps Platform

| API | Use |
|---|---|
| Places API | Locate markets, restaurants, hotels, hospitals and institutions as candidate generators and BWGs |
| Geocoding API | Convert municipal address lists into coordinates |
| Routes API / Distance Matrix | Traffic-aware travel times for validating and calibrating routes |
| Roads API | Snap GPS vehicle traces to roads |
| Maps JavaScript / Static Maps | Basemap in the desktop UI, where the licence allows |

### Google Earth Engine

| Dataset | Use |
|---|---|
| Dynamic World / ESA WorldCover | Land-use and land-cover classification |
| Google Open Buildings | Building footprints for generation estimates |
| Sentinel-2 / Landsat | Built-up change, peripheral growth, candidate dumping sites on vacant land |
| GHSL / WorldPop | Gridded population |
| CHIRPS / ERA5 | Rainfall and temperature for forecasting |
| JRC Surface Water / Sentinel-1 | Flood exposure for road accessibility |

### Other sources

| Source | Use |
|---|---|
| OpenStreetMap | Primary routable road network, one-way rules and road classes |
| Census of India | Population and household baselines |
| Municipal SWM systems (e.g. BBMP / GBA) | Vehicle GPS, weighbridge data, ward tonnage, GVPs, facility capacities |
| CPCB SWM portal | BWG registration and compliance data |
| Field surveys | Composition sampling and ground truth for vision models |
| Recycler and scrap-market surveys | Material prices and demand |

---

## 7. Technology stack

This is the proposed stack. It can change as the research evolves.

| Layer | Choice | Reason |
|---|---|---|
| Desktop shell | **Tauri** (Rust) or **Electron** | Cross-platform desktop app with a web UI |
| Frontend | React + TypeScript | Mature ecosystem |
| Maps | MapLibre GL / deck.gl, optional Google Maps JS | Vector tiles and large-layer rendering |
| Local backend | Python + FastAPI (bundled sidecar) | Access to the Python GIS and ML ecosystem |
| Spatial database | PostGIS, or SpatiaLite / GeoPackage / DuckDB-spatial for offline use | Spatial queries |
| GIS processing | GeoPandas, Shapely, Rasterio, OSMnx, `earthengine-api` | Vector, raster and network analysis |
| Routing | Google OR-Tools (CVRPTW), OSRM or Valhalla for travel times | Proven vehicle routing solvers |
| Forecasting | scikit-learn, LightGBM, PyTorch (LSTM) | Baselines through to deep models |
| Optimisation | OR-Tools, PuLP or Pyomo | Facility allocation and multi-objective models |
| Vision | PyTorch + YOLO-family detectors | Waste detection and classification |
| Testing | pytest, Vitest | Backend and frontend tests |

---

## 8. Proposed repository structure

```
SWM/
├── app/                    # Desktop shell (Tauri/Electron) and React UI
│   ├── src/
│   └── src-tauri/
├── backend/                # Python FastAPI service
│   ├── api/
│   ├── gis/                # M1  Urban Waste GIS
│   ├── forecasting/        # M2  Generation forecasting
│   ├── bwg/                # M3  BWG and land-use mapping
│   ├── routing/            # M4  Collection optimisation
│   ├── system_design/      # M5  Collection system design
│   ├── recovery/           # M6  Resource recovery
│   ├── vision/             # M7  Computer vision
│   ├── economics/          # M8  Cost and circularity
│   └── scenarios/          # M9  Scenario planner / digital twin
├── connectors/             # Google Maps, Earth Engine, OSM, municipal data
├── data/                   # Local data (git-ignored); sample data only in repo
├── notebooks/              # Exploratory research
├── docs/                   # Research notes, methodology, data dictionary
└── tests/
```

---

## 9. Research workstreams and roadmap

| Workstream | Goal | Output |
|---|---|---|
| **WS1** Understand the existing system | Map generation → collection → transport → transfer → MRF → processing → recycling → disposal. Identify actors, infrastructure, contracts, vehicles, routes, schedules, data, costs and failures | System map and data inventory |
| **WS2** Spatial data model | Build the Urban Waste GIS | M1 |
| **WS3** Waste generation model | Where, how much, what type, when | M2, M3 |
| **WS4** Collection optimisation | Test static vs optimised vs dynamic routing | M4, M5 |
| **WS5** Processing and recovery | Waste → facility → material → market | M6 |
| **WS6** Economics and circularity | ₹, tonnes, CO₂ and recovery per scenario | M8 |
| **WS7** Decision-support platform | Integrate everything into the desktop app | M9 and the UI |

### Roadmap

- [ ] **Phase 0: Setup.** Repository scaffold, desktop shell, map viewer, Google and Earth Engine credentials.
- [ ] **Phase 1: Baseline.** WS1 system mapping, data inventory, reconciled waste baseline for the pilot area.
- [ ] **Phase 2: GIS.** Load wards, roads, buildings, land use, facilities and GVPs. Build the hotspot map.
- [ ] **Phase 3: Generation.** Rate-based estimates by land use, then ML forecasting where data allows.
- [ ] **Phase 4: Routing.** Static CVRP on OSM, then time windows and multiple streams, then dynamic rerouting.
- [ ] **Phase 5: Recovery.** Facility allocation and material-to-market pathways.
- [ ] **Phase 6: Economics and scenarios.** Scenarios A to D with cost, emissions and recovery.
- [ ] **Phase 7: Vision (optional).** Pilot only where WS1 shows clear value.
- [ ] **Phase 8: Validation.** Compare against municipal ground truth and run a stakeholder review with the ULB.

---

## 10. Pilot: Bengaluru

Bengaluru is the first laboratory, using one representative zone or ward cluster.

**Why Bengaluru**

- Existing digital SWM systems: live vehicle tracking, weighbridge data, attendance, blackspot surveillance and facility monitoring.
- A history of decentralised, stream-wise waste management.
- The city's climate action plan calls for more dry-waste and MRF infrastructure, and a waste recovery platform linking businesses, service providers and NGOs.
- An ongoing transition to the Greater Bengaluru governance and collection system.

**First research task: establish a reliable data baseline.** Published figures disagree. The BBMP SWM site has reported roughly 3,000 to 3,500 TPD. Recent reporting on the Greater Bengaluru system cites about 6,500 TPD generated against 5,200 TPD collection capacity. Different definitions, boundaries and datasets produce different numbers. Reconciling them is itself a key finding, and it demonstrates the problem this platform addresses.

---

## 11. Key performance indicators

| Area | Indicators |
|---|---|
| Collection | Km travelled, time, fuel, vehicles needed, vehicle utilisation, coverage, missed collections |
| Segregation | Share of waste correctly segregated per stream |
| Processing | Facility utilisation, throughput, rejects |
| Recovery | Material recovery rate, recovered value (₹), tonnes diverted from landfill |
| Economics | Cost per tonne, per household, per km |
| Environment | CO₂e emissions, landfill disposal |
| Forecasting | MAE / MAPE against a simple baseline |

Published Indian studies report gains such as route-length reductions of up to 18% in Kadapa, and LSTM plus vehicle-routing improvements in Surat. These are treated as **evidence of feasibility, not expected results**. Every claim is tested against local municipal data.

---

## 12. Getting started

### Current state: HSR Layout pilot as a local web app

The pilot covers HSR Layout Sectors 1-7, Bengaluru, using the sector boundaries mapped in OpenStreetMap. It runs as a local web app called **CityLoom**.

**Opening page.** A dark page with a scroll-driven cover (photos from `frontend/img/hero/`, one at random on each load), the modules, the pilot sectors and the key dates of the SWM Rules 2026. It has two actions: **Report dumped waste** and **Sign in**. Sign-in opens a panel with two options:

- **Public.** Opens the reporting page with no sectors to pick.
- **Official.** Pick a role, type a name and, for sector roles, tick sectors. The built roles are surveyor, survey supervisor, planner, fleet manager, human resource manager and admin.

Sign-in is a dummy login for the pilot: the role, name and sectors stay in the browser and are sent as unverified `X-SWM-*` headers. Each role sees only its own pages in the side rail, and opening another role's page sends it to its home page. Admin sees everything. See `docs/roles.md` for every role in detail.

**Look and feel.** Every page uses the shadcn/ui look (zinc greys, thin borders, small radii) and one font, Poppins. App pages open in a light theme, with a sun/moon switch at the foot of the side rail for the dark theme; the choice is kept in the browser. The opening page is always dark. Surveyor and supervisor text lives in `frontend/strings.json` in English and Kannada.

- **Surveyor.** Map of the surveyor's sectors, buildings coloured by visit outcome, likely bulk waste generators outlined in red as "survey first". Tap a building to start a visit: outcome, building use, use mix (dwellings, shops, beds and so on), collection arrangement, segregation and home composting. Values are append-only with their source (assumed, surveyed, verified, weighed) and override the OSM-based guess. Surveyors also report map problems and missing buildings.
- **Garbage mapping.** Surveyors, supervisors and the public report garbage vulnerable points (GVPs). A GVP can only be placed on a road: it snaps to the nearest street segment within 30 m. Each report has a severity, up to 3 photos and its source, and reports near each other merge. Supervisors move each GVP through reported → verified → assigned → cleaning → cleared → monitoring (or recurred, or rejected). Verified GVPs become cleaning tasks in Clean City. A GVP reaches the route builder only after it is cleared, as a one-off pickup. All GVPs download as GeoJSON for publishing (SWM Rules 2026, r. 15(1)).
- **Public reporting** (`report.html`). Anyone can report dumped waste: the page finds the person's location by device GPS, says which sector they are in and drops the pin on the nearest road. They add a severity, rough size and photos.
- **Survey supervisor.** Progress by sector and surveyor, a blind spot-check of 5% of each surveyor's visits, mismatch rates, items to review and sector assignments.
- **Collection planner.** Buildings coloured by estimated quantity, chosen as stream then fraction (for example dry waste, then plastic). Also by generator category, household vs commercial, and bulk waste generator compliance. Includes stream and dry-fraction totals, per-sector figures and a 3D view with height from floors. Each sector links to the route builder, which builds collection stops from the buildings.
- **Bulk waste generator check.** Computed, not surveyed. Floor area is footprint × floors. Water counts only when a surveyor or BWSSB figure is recorded. Waste is units × typology. Every result shows the criterion met, the source of the value and the rule citation.
- **Rules library.** The SWM Rules 2026 are stored as machine-readable JSON and a readable summary. The code reads thresholds, stream names and citations from it. See `docs/regulations/`.
- **Route builder.** `builder.html` plans two-tier collection for one sector at a time.
  - You place the start point and the MRF on the map. Transfer stations are suggested on main roads to cover every collection point within a service radius along the road network, and you can drag, add or remove them.
  - You enter the fleet as vehicle type and number, for door-to-door vehicles and for trucks.
  - Small vehicles collect door to door from street-run collection points and make as many trips to the transfer stations as their capacity needs. Trucks carry the loads to the MRF.
  - The result shows the routes, trips per vehicle, fill per trip, truck trips and the time to complete, against the shift length.
  - Collection points are street runs: buildings are snapped to their frontage street and grouped by use, with stable IDs from OSM nodes. Vehicles drive the whole stretch of street; OR-Tools picks which end to enter, and one-way streets are taken the right way. Hovering a point highlights its plots and street.
  - **Plan for** a date (today or the next six days): the plan collects that date's stored collection demands. The collection cycle decides which streams and generators are due, and its time window becomes the shift. Without a cycle, every stream's daily waste is due.
  - Demands come from four sources: door-to-door street runs, bulk waste generators, cleared GVP pickups and public bins that are full, overflowing or due.
  - The fleet is filled in from the resource inventory. A plan cannot use more available vehicles than the sector has (its own plus the shared pool), and it checks that there are enough drivers and collectors to crew them. Until any vehicles are entered, plans are labelled as a hypothetical fleet.
  - **Suggest fleet** finds the smallest fleet, keeping the chosen vehicle types and mix, that finishes within a target time (5 h by default). It runs the optimiser a few times and says what limits the time. In the HSR plans, collecting at houses takes about 70–75% of vehicle time and driving under 10%, so the number of vehicles matters far more than the number of transfer stations.
- **Resource management** (`resources.html`). What the municipality actually has, in three tabs:
  - **Vehicles** (fleet manager): type, registration, home sector or shared pool, status, shift, drivers and collectors needed, GPS device, and specs such as payload, body volume and width.
  - **People** (human resource manager): worker ID, role (driver, collector, sweeper and so on), skills, hours per day, supervisor and assignment.
  - **Machinery** (fleet manager): mechanical sweepers, loaders, compactors, handcarts, pressure washers and other equipment, with capacity, condition and service dates.
  - Statuses are available, assigned, in use, maintenance and unavailable. Plans count only the first three.
  - Every change is logged and kept. The page warns when vehicles lack GPS, which the rules require above a city population (SWM Rules 2026, r. 8(h)(ix)).
- **Service planning** (`backend/demand`). What service each sector needs (from the collection cycle, street classes, bin rules and GVPs) and, for each day, the **service demands**: collection demands (a stream's waste at a street run, a public bin or a cleared GVP) and cleaning demands (streets due for sweeping, verified GVPs). Demands are stored with their history and can be regenerated at any time; when no longer needed they close with a reason (cancelled, or done when a bin is serviced or a GVP cleared). The route builder plans a date from that date's collection demands. See `docs/architecture.md`.
- **Today** (`command.html`, the planner's home). How the city is doing (service level over 7 days), where it is failing (alerts, recurring and critical GVPs, full bins, sectors below target, workforce short), today's work and, per sector, why: no adopted plan, the plan's exceptions, cleaning hours needed against those available.
- **Route plans.** Every optimiser run is saved with its routes and stops. A plan that leaves demand unserved, runs a vehicle past the window or leaves waste at a transfer station is marked infeasible, with reasons and the actions that would fix it. Adopting a plan marks its demands planned and assigns vehicles and crews from Resources. The depot, transfer stations, MRF and truck yard are saved facilities.
- **Work plans** (Clean City, *Work plan* tab). The day's cleaning demands given to cleaning staff and equipment, GVPs first; what does not fit is a workforce shortage in worker-hours.
- **Operations** (`operations.html`). The day's actual routes (km, time, kg, missed stops) and cleaning (done, partly done, missed, with the reason), recorded against the adopted plans. Records are append-only and keep their context: this is the operational history later learning uses.
- **Performance** (`performance.html`). Service required vs delivered by sector and kind of work, the gap and its reasons, route estimates against actuals, GVP response times, and planning alerts that turn repeated failure (a GVP that keeps coming back, a place missed again and again, a sector short of capacity, an MRF over capacity) into planning questions.
- **Processing** (`processing.html`). Intake at each facility by stream, recovered material, rejects and disposal; utilisation against capacity; observed dry waste fractions next to the assumed ones.
- **City map** (`citymap.html`). One map with switchable layers from the same records: sectors, street cleaning classes, bins, GVPs, today's collection demands and facilities. Every map page uses the same base (`frontend/citymap.js`).
- **Collection cycle** (`cycle.html`, planner). The weekly schedule for each stream and generator type (households, commercial, institutions, bulk waste generators), with days, a time window, collection method and vehicle types. Entries can cover every sector, or override the schedule for one sector.
  - A week grid shows what is collected each day. An example template can be loaded from `reference/collection_cycle.json`.
  - Checks flag streams or generators with no regular collection (SWM Rules 2026, r. 8(h)(iii)). The page also shows the rule that markets are cleaned daily (r. 39(19)).
  - The route builder reads the day's plan: waste built up since the last collection day is collected in one go.
- **Clean City** (`cleancity.html`, planner). Street cleaning, public bins and GVP clearing on one map.
  - **Streets.** Every street segment in the sectors is classified as primary, commercial, market, residential or low intensity from road class and building density. The planner can change a class, and each class has a sweeping frequency (SWM Rules 2026, r. 39(16)).
  - **Bins.** Public bins sit on the road, with fill level and service times. Bin spacing per street class suggests where bins are missing. Bins that are full, overflowing or due become route builder demands.
  - **GVP clearing.** Verified GVPs as cleaning tasks, with the equipment their severity needs.
  - **Workforce.** Worker-hours needed for sweeping, bins and GVPs against the people available. For example, Sector 1 needs about 183 worker-hours a day, about 23 workers on 8-hour shifts.
  - Assumptions (frequencies, productivity of 120 m per worker-hour, 5 minutes per bin) live in `reference/clean_city.json`.
- **Park composting.** Public parks (OSM parks and gardens not tagged private) set aside part of their area to compost the neighbourhood's wet waste.
  - The share is by park size, from 5% for parks under 2,000 m² down to 1% for parks over 20,000 m², or a flat 3% average. It can be edited or switched off per park.
  - Capacity = composting area × land norm (vermicomposting 2.5 kg/day per m² or windrow 6.25, CPHEEO Table 3.5), capped at 5 t/day per site so no buffer zone is needed (SWM Rules 2026, r. 3(1)(h)).
  - Buildings within 300 m by road feed the nearest site with room, residential first. Residents bring a share themselves (30% by default); vehicles unload the rest at the park before going to the transfer station.
  - Results show wet waste kept from the MRF, compost produced, each park's load and the truck trips and time compared with no park composting.
- **Composting inside buildings.** The surveyor records whether wet waste is composted in the building (all, most or some, the method, and kg/day if known). Only the remaining wet waste is collected.
- Composting assumptions and sources live in `reference/composting.json`.
  - Uses are never mixed in one point. Bulk waste generators are separate, and their wet waste is excluded.
  - Service time is 1–5 minutes per building, by vehicle class and kg, plus travel along the street.
  - Vehicle classes and road-width assumptions live in `reference/` with a source for every figure.

All waste quantities are **estimates** from typology norms in `backend/buildings/norms.json`. The data model carries a weighed value that replaces the estimate when available.

What OSM does and does not give for HSR:

| Tag | Buildings |
|---|---|
| Footprint | 11,541 inside the sector boundaries |
| `building=yes` only, with no use | About 96% |
| House number | About 3,800 |
| Floors (`building:levels`) | About 130 |

Building use therefore comes from the survey.

Run it on Windows from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn backend.api.main:app --reload
```

Then open http://127.0.0.1:8000. The first load downloads OSM data through Overpass and caches it in `data/cache/`, which takes a few minutes. Everything the app stores lives in one folder, `data/` (git-ignored; set `CITYLOOM_DATA_DIR` to move it). Survey records and GVPs are in `data/survey.db`; resources, the collection cycle, Clean City and service demands in `data/ops.db`; photos in `data/photos/`. Run the tests with:

```powershell
.venv\Scripts\python -m pytest
```

#### Deploying

The app is one FastAPI server that also serves the frontend, so it needs a host that runs Python. GitHub Pages only serves static files.

- **Full app (Render).** `render.yaml` is a Render Blueprint. In the Render dashboard choose *New → Blueprint* and pick this repository. Render installs `requirements.txt` and starts `uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT`. The build copies the prebuilt HSR map data from `deploy/cache/` into `data/cache/`, because Nominatim and Overpass often block cloud servers.
  - The free plan has no persistent disk, so survey records are lost on each restart, and the app sleeps when idle. Use a paid plan with the disk in `render.yaml` before collecting real surveys.
  - The app has no login yet, so anyone with the link can edit survey records.
  - Live at https://swm-urban-waste.onrender.com (`FULL_APP_URL` in `frontend/ui.js`).
- **GitHub Pages.** `.github/workflows/static.yml` publishes `frontend/` on every push to `main`. Pages cannot run the API, so its map, route builder and rules pages forward to the Render app.

Current code layout:

```
backend/
├── config.py              # Study areas, pilots, road speed and GVP settings
├── osm.py                 # Overpass access with mirror fallback, layer caching
├── regulations/           # Regulations library loader
│   └── library/           # index.json, swm_rules_2026.json
├── buildings/
│   ├── layers.py          # OSM sectors, buildings, land use, POIs for a pilot
│   ├── generators.py      # Classification, estimates, BWG check, summaries, collection stops
│   └── norms.json         # ASSUMPTIONS: generation rates, typologies, dry fractions, GVP sizes
├── auth/roles.json        # Roles, their modules, home pages and jurisdictions
├── survey/                # data/survey.db: append-only field values (db.py), reading them (resolve.py), GVPs (gvp.py)
├── resources/             # data/ops.db: vehicles, people and machinery, with plan caps and crew checks
├── cycle/                 # Collection cycle schedules and day plans (data/ops.db)
├── cleancity/             # Street classes, public bins, cleaning workload (data/ops.db)
├── demand/                # Service requirements and the stored daily service demands (data/ops.db)
├── facilities/            # Depots, truck yards, transfer stations, MRFs and other places
├── plans/                 # Route plans: saved runs, routes, stops, feasibility, adoption, assignment
├── workplan/              # Cleaning work plans: demands given to staff and equipment
├── operations/            # Append-only actuals against plans (operational history)
├── performance/           # Required vs delivered, route accuracy, GVP response, planning alerts
├── processing/            # Facility intake, recovery, rejects, disposal
├── command/               # The command centre read model
├── api/                   # FastAPI: main.py plus survey, resources, cycle, Clean City and service-planning routers; serves the frontend
└── routing/               # Road network, street-run collection points (points.py), park composting (parks.py) and two-tier planner (twotier.py)
frontend/
├── index.html, landing.*  # Opening page and sign-in
├── style.css, ui.js       # Design system (light and dark themes) and shared shell (navigation by role, icons, strings)
├── theme.js               # Applies the saved theme before the page draws
├── strings.json           # UI text in English and Kannada
├── surveyor.*, supervisor.*, survey_flow.js  # Survey section
├── report.*               # Public reporting of dumped waste
├── map.*                  # Planner map and building card
├── cycle.*, cleancity.*, resources.*  # Collection cycle, Clean City, resource management
├── builder.*              # Route builder
├── admin.html, rules.*    # Admin and the rules library viewer
├── img/hero/              # Opening page cover photos
└── stub.html              # Placeholder for roles not yet built
reference/                 # Sourced assumptions: vehicles, roads, composting, clean city, collection cycle template
docs/                      # roles.md and readable summaries of the regulations library
tests/                     # Network, points, planner, buildings, survey, GVP, resources, cycle, Clean City, roles, strings and regulations tests
```

Known limits:

- Road speeds are assumed per road class, not measured.
- Generation norms are placeholders until a characterisation survey and weighed data calibrate them.
- Vehicle width limits apply to the streets a vehicle collects from, not to streets it only drives through.
- Trucks are assumed to shuttle while door-to-door collection is under way, with enough room at each transfer station.
- Sign-in is a dummy login. Anyone who can open the app can pick any role and edit records.
- Clean City productivity, bin spacing and sweeping frequencies are assumptions until measured.

### Full application prerequisites (planned)

- Node.js 20+ and Rust (for Tauri), or Node.js only (for Electron)
- Python 3.11+
- A Google Cloud project with the Maps Platform APIs enabled
- A Google Earth Engine account registered for noncommercial or research use

Planned configuration, kept in a git-ignored `.env` file:

```env
GOOGLE_MAPS_API_KEY=your-key
EE_PROJECT=your-earth-engine-cloud-project
DATABASE_URL=postgresql://user:pass@localhost:5432/swm
```

Never commit API keys.

---

## 13. Licensing and data-use notes

- **Google Maps Platform terms restrict caching and storing** most content, and restrict displaying it on non-Google maps. Use Google APIs for lookups, geocoding and travel-time validation. Use **OpenStreetMap as the stored, routable network**. Review the current terms before storing any Google-derived data.
- **Google Earth Engine** is free for noncommercial and research use. Commercial or operational government deployment needs a commercial licence.
- **OpenStreetMap** data is under the ODbL and requires attribution.
- **Municipal data** may include personal data such as worker attendance or household records. Anonymise it and follow the Digital Personal Data Protection Act, 2023.

Project licence: *to be decided*.

---

## 14. Contributing

This is an early-stage research project. Issues and discussion on methodology, data sources and Indian municipal practice are welcome.
