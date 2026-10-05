# SWM: Urban Waste Intelligence Platform

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
- Garbage vulnerable points live in the survey database (`backend/survey/gvp.py`); observations and events are append-only. Their kg/day factors are estimates in `norms.json`.
- The fleet inventory (`backend/fleet`, `data/ops.db`) caps plans per sector. Tests get an empty fleet store from `tests/conftest.py`.
- Roles, the survey flow, GVPs and the fleet are described in `docs/roles.md`.
- When a new regulatory document is provided, add it to the library following `docs/regulations/README.md`. Source PDFs go in `data/regulations/` (git-ignored).
- Write text files as UTF-8 explicitly. Python's default encoding on this machine is cp1252.

## Running

```powershell
.venv\Scripts\python -m uvicorn backend.api.main:app --reload
.venv\Scripts\python -m pytest
```

OSM data comes through Overpass. The main server often refuses connections, so the code falls back to mirrors (see `backend/osm.py`). Downloaded data is cached in `data/cache/` (git-ignored).
