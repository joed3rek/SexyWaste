# CityLoom (SWM): Urban Waste Intelligence Platform

GIS and optimisation decision-support app for municipal solid waste management in Indian cities. The pilot is HSR Layout sectors 1-7, Bengaluru. See README.md.

## Regulations library: always consult it

- Before writing code, UI text, docs or analysis that touches waste streams, generator types, facilities, collection, reporting or deadlines, read `docs/regulations/` and `backend/regulations/library/`.
- Use the official terms of the SWM Rules 2026: wet waste, dry waste, sanitary waste, special care waste, bulk waste generator (BWG), material recovery facility (MRF), garbage vulnerable point (GVP), EBWGR certificate.
- Never hard-code a regulatory threshold. Read it through `backend.regulations` and cite the rule, for example "SWM Rules 2026, r. 3(1)(i)".
- Keep estimation assumptions, such as generation rates, out of the regulations library. They live in `backend/buildings/norms.json` and are labelled as estimates in the UI.
- Streams and fractions are different things. The four streams are wet, dry, sanitary and special care waste. Paper, plastic, metal, glass, wood and rubber are fractions of dry waste, sorted at the MRF. E-waste is governed by the separate E-Waste Rules.
- The surveyor role is the backbone: building use, use mix (units, occupants, beds), floors, collection arrangement and home composting come from surveys (`backend/survey/`), not OSM. Survey values override OSM guesses. Weighed data overrides estimates.
- Bulk waste generator status is computed, never surveyed: floor area = surveyed floors × OSM footprint, waste = use mix × typology. "Confirmed" needs a surveyed BWG building use; estimate-only cases are "likely" (Round 2 candidates). Water is not used for now.

## Survey data rules

- Field values are append-only (triggers block UPDATE and DELETE). Correct by appending; never edit rows. Write through `backend/survey/db.record_value()`.
- Every value has a source: assumed < surveyed < verified < weighed. Read values through `backend/survey/resolve.py`, never straight from the table.
- Sign-in is a dummy login: role, name and sectors arrive as `X-SWM-*` headers and are unverified (`backend/auth`). Browser writes go through `swmWrite()` in `ui.js`.
- Surveyor and supervisor text lives in `frontend/strings.json` (en, kn). Use `t()`; do not hard-code UI text in those pages. Server messages carry codes, worded in the browser.
- Garbage vulnerable points live in the survey database (`backend/survey/gvp.py`): on a street segment, never a property. Reports, photos, events and pickups are append-only. A GVP reaches the route builder only as a one-off pickup after it is cleared; cleaning and transport are separate steps. Size and build-up factors are estimates in `norms.json`.
- When a new regulatory document is provided, add it to the library following `docs/regulations/README.md`. Source PDFs go in `data/regulations/` (git-ignored).
- Write text files as UTF-8 explicitly. Python's default encoding on this machine is cp1252.

## Planning modules (`data/ops.db`)

- All stored data lives in `DATA_DIR` (`backend/config.py`; `data/` by default, or `CITYLOOM_DATA_DIR`). Never hard-code another data path.
- Service planning (`backend/demand`) is the hub: requirements are read from the cycle, street plans, bins and GVPs (never copied), and each day's **service demands** are stored in `service_demand` with an append-only `service_demand_event`. `generate()` is idempotent. Consumers read demands; they do not work demand out themselves. Every new feature states which entity it reads, which it creates or updates, and where that flows next. See `docs/architecture.md`.

- Resource management (`backend/resources`): vehicles (fleet manager), people (HR manager) and machinery (fleet manager). Statuses are available, assigned, in_use, maintenance, unavailable; only the first three count for plans. A sector plans with its own resources plus the shared pool, and plans are labelled hypothetical while the inventory is empty. Changes are logged in the append-only `resource_log`. Tests get an empty store from `tests/conftest.py`.
- Collection cycle (`backend/cycle`, planner): schedules per stream × generator type, for every sector or one sector's override. Schedules are deactivated, never deleted. The route builder takes the day's plan (built-up days per stream) and uses its window as the shift.
- Clean City (`backend/cleancity`, planner): street classes from the street graph, public bins on the road, GVP clearing tasks and the workforce calculation. Bins that are full, overflowing or due become route builder demands. Assumptions live in `reference/clean_city.json`.
- The route builder (`backend/routing/twotier.py`) plans a date from that date's stored collection demands (`demand.plan_points`): door-to-door runs, bulk waste generators, cleared GVP pickups and public bins. It is capped by the inventory and checked for crew. Without a date it uses the what-if demands (every stream, one day's waste). GVP moves sync their demands (`gvp.act` → `demand.sync_gvps`).
- Assumptions with sources live in `reference/` (vehicles, roads, composting, clean city, collection cycle template), never in the regulations library.
- The loop after demands: `backend/facilities` (places) → `backend/plans` (route plans: saved, feasibility, adoption, vehicle and crew assignment) and `backend/workplan` (cleaning work plans) → `backend/operations` (append-only actuals; the operational memory) → `backend/performance` (required vs delivered, planning alerts) → `backend/processing` (facility intake, recovery) → `backend/command` (the planner's home). Plans, records and alerts are never deleted: superseded, corrected by a new record, or resolved.
- Never return a bad plan silently: a plan that leaves demand unserved, runs past its window or cannot be staffed is `infeasible` with reasons and actions, and adopting it needs an explicit `accept_exceptions`.
- Maps use `cityMap()` and the shared layers in `frontend/citymap.js`; do not copy a map style into a page. A new page needs only its `NAV` entry in `ui.js` for the rail and the role guard.

## Roles and pages

- Each role sees only its own pages: the `roles` lists in `NAV` (`frontend/ui.js`), with `canOpen()` and `guardPage()`. Admin sees everything. Role definitions and home pages are in `backend/auth/roles.json`.
- Sign-in has Public (role `generator`, opens `report.html`) and Official (surveyor, survey supervisor, planner, fleet manager, HR manager, admin).
- Roles, the survey flow and GVPs are described in `docs/roles.md`; resources, the cycle and Clean City in README section 12.

## Design base: always

- Every page uses the shadcn/ui look in `frontend/style.css`: zinc tokens, 1 px borders, small radii, quiet shadows. Use the CSS tokens (`--ink`, `--surface`, `--line`, `--on-ink` and so on), never hard-coded colours, so both themes work.
- One font everywhere: Poppins (`--font`). No display fonts per page.
- App pages default to light, with a light/dark switch in the rail (`theme.js`, `setTheme()` in `ui.js`). The opening page (`index.html`, `landing.css`, `landing.js`) is always dark (`data-theme-fixed="dark"`).
- New templates the user supplies are adapted onto this base: layout and motion may follow the template, colours, type and components stay on the base. Accent colours only for small touches (type, dots, rules).
- Every new page loads `theme.js` in `<head>` before `style.css`.

## Running

```powershell
.venv\Scripts\python -m uvicorn backend.api.main:app --reload
.venv\Scripts\python -m pytest
```

OSM data comes through Overpass. The main server often refuses connections, so the code falls back to mirrors (see `backend/osm.py`). Downloaded data is cached in `data/cache/` (git-ignored). The server sends frontend files with `Cache-Control: no-cache`, but backend changes need a restart unless it runs with `--reload`.
