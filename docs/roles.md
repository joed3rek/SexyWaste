# Roles and the survey section

## Role system

Roles are defined in `backend/auth/roles.json`. Each role has a key, a label, a short description, whether it is built yet, and its home page. The opening page (`index.html`) reads the list from `/api/roles`.

| Role | Built | Home page | What it does |
|---|---|---|---|
| Surveyor | Yes | `surveyor.html` | Visits buildings and records building use, use mix and collection basics |
| Survey supervisor | Yes | `supervisor.html` | Assigns sectors, spot-checks surveys and resolves map problems |
| Admin | Yes | `admin.html` | Manages users, roles and jurisdictions (role list only for now) |
| Planner | Partly | `map.html?role=planner` | Waste profile, routes and transfer stations (map and route builder) |
| Fleet and workforce manager | No | | Vehicle inventory, staff, shifts and PPE records |
| Driver | No | | Assigned route and navigation only |
| Collector (pourakarmika) | No | | Shift, collection confirmations, missed pickups, GVPs, unsafe conditions |
| Operations supervisor | No | | Live command centre, route reassignment, penalty validation |
| Collection contractor | No | | Performance on their contract |
| MRF or DWCC operator | No | | Incoming loads, sorting, rejects, downtime, incidents |
| Recycler (formal or informal) | No | | Pickups, materials and volumes |
| Ward officer | No | | Coverage, BWGs, compliance and analytics for the ward |
| Trainer | No | | Training modules and completion |
| Generator (household, RWA, BWG) | No | | Their building, schedule and status; complaints |

Roles that are not built open `stub.html`.

### Sign-in is a dummy login

There are no stored users, passwords or sessions. On the opening page a person picks a role, types a name and ticks their sectors. The browser keeps this in `localStorage` (`swm.session`) and sends it with every write as three headers:

| Header | Value |
|---|---|
| `X-SWM-Role` | Role key |
| `X-SWM-User` | Name as typed |
| `X-SWM-Sectors` | Sectors, comma separated |

`backend.auth.actor()` reads them and marks the actor `verified: False`. The survey API refuses writes without a role and a name (401). Anyone can claim any role, so this is for the pilot only; real accounts are needed before field operations.

All writes from the browser go through one function, `swmWrite()` in `ui.js`, so an offline queue can be added in one place later.

### Interface text

Text shown to surveyors and supervisors lives in `frontend/strings.json`, with an English column and an empty Kannada column. Pages use `t(key)` and `data-t` attributes; the language is picked on the opening page. Server warnings and spot-check mismatches carry codes and details and are worded in the browser, so they can be translated too. `tests/test_strings.py` checks that every key used exists.

## Survey section (Round 1)

Round 1 is a quick visit to every building: what it is, what uses it holds, and how waste leaves it. It feeds the estimates and the bulk waste generator (BWG) status. Detailed weighing is Round 2, for buildings flagged as candidates.

### Surveyor screen

- Map of the surveyor's sectors, opened at the buildings. Buildings are coloured by the last visit outcome; grey means not visited.
- A red outline marks "survey first" buildings: likely BWGs not yet visited.
- Tapping a building (or near it) opens its sheet: name, sector, the map's guess of its use, the last visit, and **Start visit**.
- **Report map problem** records a footprint issue. **Missing building** drops a pin where a building is missing from the map.
- `surveyor.html?building=<id>` opens a building directly (used by the supervisor's review links).

### The visit

`survey_flow.js` walks through five steps:

1. **Outcome:** completed, partial, refused, locked, revisit requested, demolished, not a building, footprint issue. Only completed and partial go on.
2. **Building use:** one of 18 uses from `backend/survey/building_uses.json`, plus floors and, for apartments, the society name.
3. **Use mix:** how many units of each use (dwellings, shops, offices, beds and so on), with occupants or beds where they matter. Contradictions, such as a hospital with no beds, are shown as warnings at the top; the surveyor can continue anyway and the warning goes to the supervisor.
4. **Collection:** current collection arrangement, segregation reported, home composting (method and kg/day if known).
5. **Confirm**, then save.

Closing the flow early saves the visit as partial. The phone's GPS is compared with the footprint; more than 50 m away (`GPS_WARN_DISTANCE_M`) raises a warning and a review item. One photo per visit is allowed (JPEG, PNG or WebP, up to 3 MB), kept for 365 days.

### Data model

Stored in SQLite at `data/survey.db` (`backend/survey/db.py`).

- **Field values are append-only.** Database triggers block updates and deletes. A correction is a new row.
- Every value carries its source: **assumed → surveyed → verified → weighed**, weakest to strongest.
- `backend/survey/resolve.py` picks the value in use: strongest source first, then the latest, then the last written. The history stays visible.
- Survey values override OSM guesses; weighed data overrides estimates.

### Supervisor screen

- Progress by sector and by surveyor.
- **Spot-check queue:** 5% of each surveyor's completed visits per week (at least one), never the supervisor's own. The spot-check is blind: the supervisor does not see the surveyor's answers. Matching values are recorded as verified; differences are recorded as surveyed and raise a review item.
- Mismatch rates per surveyor, with building-use mismatches shown separately.
- **Items to review:** contradictions, GPS distance, spot-check mismatches, and map problems, each with a link to the building.
- **Sector assignments:** which surveyor works which sector.

### What the survey changes downstream

- `backend/buildings/generators.py` estimates waste from the use mix, then from the building-use typology, then from OSM. Rates are estimates in `backend/buildings/norms.json`.
- BWG status is computed, never surveyed:
  - **Confirmed:** the surveyed building use is a BWG entity, and floor area (surveyed floors × OSM footprint) or weighed waste is over the threshold read from the regulations library.
  - **Likely:** the estimates alone point to a BWG. These are Round 2 candidates and are outlined in red on the surveyor map.
- The route planner leaves out wet waste only for confirmed BWGs (SWM Rules 2026, r. 6), and only collects the wet waste a building does not compost itself.
