# Regulations library

This folder holds the regulatory and guidance documents that the Urban Waste Intelligence Platform follows. The platform uses the vocabulary, thresholds and flows of these documents, and cites them wherever it applies a rule.

## How the library works

Each document exists in two forms.

| Form | Location | Used by |
|---|---|---|
| Machine-readable | `backend/regulations/library/<id>.json` | The application code, through `backend/regulations/__init__.py`, and the API at `/api/regulations` |
| Human-readable | `docs/regulations/<id>.md`, or a section of `related-rules.md` for shorter documents | Planners, reviewers and anyone writing code or reports |

The registry of documents is `backend/regulations/library/index.json`.

## Rules for using the library

1. **Use official terms.** Say "wet waste", "dry waste", "sanitary waste", "special care waste", "bulk waste generator", "material recovery facility" and "garbage vulnerable point", exactly as the rules define them.
2. **Never hard-code a regulatory number.** Thresholds such as the 100 kg/day bulk waste generator limit are read from the JSON file.
3. **Cite the rule.** Every output that applies a rule shows its citation, for example "SWM Rules 2026, r. 3(1)(i)".
4. **Keep assumptions separate from rules.** Estimation norms such as kg per person per day are assumptions, stored outside this library, and labelled as such in the interface.
5. **Record text conflicts.** When the Hindi and English texts differ, or a document conflicts with another, record it under `text_discrepancies` and state which version is used.

## Documents

| ID | Document | Status |
|---|---|---|
| `swm_rules_2026` | [Solid Waste Management Rules, 2026](swm-rules-2026.md) | In force from 1 April 2026 |
| `cnd_rules_2025` | [Environment (C&D) Waste Management Rules, 2025](related-rules.md#environment-construction-and-demolition-waste-management-rules-2025) | In force from 1 April 2026 |
| `bmw_rules_2016` | [Bio-Medical Waste Management Rules, 2016](related-rules.md#bio-medical-waste-management-rules-2016) | In force; amendments not in library |
| `ewaste_rules_2022` | [E-Waste (Management) Rules, 2022](related-rules.md#e-waste-management-rules-2022) | In force from 1 April 2023 |
| `battery_waste_rules_2022` | [Battery Waste Management Rules, 2022](related-rules.md#battery-waste-management-rules-2022-as-amended-to-2025) | In force; amended to February 2025 |
| `ewaste_guidelines_2016` | [CPCB E-Waste Implementation Guidelines, 2016](related-rules.md#cpcb-implementation-guidelines-for-e-waste-management-rules-2016-superseded) | Superseded, kept for reference |
| `batteries_rules_2001` | [Batteries (Management and Handling) Rules, 2001](related-rules.md#batteries-management-and-handling-rules-2001-superseded) | Superseded, kept for reference |
| `howm_rules_2016` | [Hazardous and Other Wastes Rules, 2016](related-rules.md#hazardous-and-other-wastes-management-and-transboundary-movement-rules-2016) | In force; amendments not in library |
| `pwm_amendments_2021_2022` | [Plastic Waste Management amendments, 2021 and 2022](related-rules.md#plastic-waste-management-amendment-rules-2021-and-second-amendment-rules-2022) | Amendments only |

## Adding a document

1. Save the source PDF outside the repository or in `data/` (git-ignored), and note where it came from.
2. Write `backend/regulations/library/<id>.json` with the same top-level structure as `swm_rules_2026.json` where it applies: definitions, thresholds, duties, deadlines, reporting and discrepancies. Every item carries its rule number.
3. Write `docs/regulations/<id>.md` as the readable summary.
4. Register it in `index.json` and move it out of the `planned` list.
5. Add or update tests that check the code reads the new values.

Planned additions are listed in `index.json`. They include CPCB guidelines under the 2026 rules, the CPHEEO manual and the Bengaluru bye-laws.
