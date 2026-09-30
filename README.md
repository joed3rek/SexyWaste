# Urban Waste Intelligence Platform (SWM)

**An AI-enabled, GIS-based decision-support desktop application for municipal solid waste management in Indian cities.**

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

The pilot covers HSR Layout Sectors 1-7, Bengaluru, using the sector boundaries mapped in OpenStreetMap. It runs as a local web app and will be wrapped in a desktop shell later.

**Opening page: role picker.** The surveyor and collection planner roles work end to end. Generator, recycler and ward officer are stubs.

- **Surveyor.** Map of about 11,500 OSM building footprints coloured by survey status, with likely bulk waste generators marked "survey first". Tap a building to fill its card: building use, units, floors, water use and source, on-site processing (none, compost, biogas, EBWGR certificate) and segregation observed. Survey data overrides the OSM-based guess.
- **Collection planner.** Buildings coloured by estimated quantity, chosen as stream then fraction (for example dry waste, then plastic). Also by generator category, household vs commercial, and bulk waste generator compliance. Includes stream and dry-fraction totals, per-sector figures and a 3D view with height from floors. Each sector links to the route builder, which builds collection stops from the buildings.
- **Bulk waste generator check.** Computed, not surveyed. Floor area is footprint × floors. Water counts only when a surveyor or BWSSB figure is recorded. Waste is units × typology. Every result shows the criterion met, the source of the value and the rule citation.
- **Rules library.** The SWM Rules 2026 are stored as machine-readable JSON and a readable summary. The code reads thresholds, stream names and citations from it. See `docs/regulations/`.
- **Route builder.** `builder.html` plans two-tier collection for one sector at a time.
  - You place the start point and the MRF on the map. Transfer stations are suggested on main roads to cover every collection point within a service radius along the road network, and you can drag, add or remove them.
  - You enter the fleet as vehicle type and number, for door-to-door vehicles and for trucks.
  - Small vehicles collect door to door from street-run collection points and make as many trips to the transfer stations as their capacity needs. Trucks carry the loads to the MRF.
  - The result shows the routes, trips per vehicle, fill per trip, truck trips and the time to complete, against the shift length.
  - Collection points are street runs: buildings are snapped to their frontage street and grouped by use, with stable IDs from OSM nodes.
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

Then open http://127.0.0.1:8000. The first load downloads OSM data through Overpass and caches it in `data/cache/`, which takes a few minutes. Survey records are stored in `data/survey.db`. Run the tests with:

```powershell
.venv\Scripts\python -m pytest
```

Current code layout:

```
backend/
├── config.py              # Study areas, pilots, road speed assumptions
├── osm.py                 # Overpass access with mirror fallback, layer caching
├── regulations/           # Regulations library loader
│   └── library/           # index.json, swm_rules_2026.json
├── buildings/
│   ├── layers.py          # OSM sectors, buildings, land use, POIs for a pilot
│   ├── generators.py      # Classification, estimates, BWG check, summaries, collection stops
│   └── norms.json         # ASSUMPTIONS: generation rates, typologies, dry fractions
├── survey/store.py        # SQLite survey records (building cards)
├── api/main.py            # FastAPI endpoints and static frontend hosting
└── routing/               # Road network, street-run collection points (points.py) and two-tier planner (twotier.py)
frontend/
├── index.html             # Role picker
├── map.html, map.js       # Surveyor and planner map, building card
├── builder.html, builder.js  # Route builder
├── rules.html, rules.js   # Rules library viewer
└── stub.html              # Placeholder for roles not yet built
docs/regulations/          # Readable summaries of the regulations library
tests/                     # Network, points, planner, buildings, survey and regulations tests
```

Known limits:

- Road speeds are assumed per road class, not measured.
- Generation norms are placeholders until a characterisation survey and weighed data calibrate them.
- Vehicle width limits apply to the streets a vehicle collects from, not to streets it only drives through.
- Trucks are assumed to shuttle while door-to-door collection is under way, with enough room at each transfer station.
- The app has no login yet. Anyone who can open it can edit survey records.

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
