# Related waste rules

These rules sit alongside the [SWM Rules 2026](swm-rules-2026.md). They matter to the platform mainly because they decide **which waste does not go into municipal door-to-door routes**, and where it goes instead.

Source PDFs are in `data/regulations/` (git-ignored). The machine-readable files are in `backend/regulations/library/`.

> **Version check needed.** Several of the files provided are not the latest text. Each entry says what to verify. The "believed superseded" notes come from general knowledge, not from the files, and should be confirmed.

## What enters municipal routes

| Waste | In door-to-door routes? | Channel | Governing document |
|---|---|---|---|
| Wet, dry, sanitary, special care | Yes | Door-to-door, then secondary storage point, then MRF | SWM Rules 2026 |
| Plastic | Yes, as a dry waste fraction | Sorted at the MRF, then to plastic waste processors under EPR | SWM r. 3(1)(zw), 9(7)(i); PWM amendments |
| Construction and demolition waste | No | Generator or authorised agency to a C&D collection point or intermediate storage facility | C&D Rules 2025 |
| Bio-medical waste | No | Common bio-medical waste treatment facility operator | BMW Rules 2016 |
| E-waste, including fluorescent lamps and solar panels | No | Registered producer, refurbisher or recycler; the ULB separates any e-waste found in municipal waste; an MRF may act as deposition centre | E-Waste Rules 2022, Schedule V; SWM r. 9(7)(ii) |
| Batteries of all kinds | No | Consumer keeps them out of domestic waste; the ULB hands collected batteries to producers or registered recyclers | Battery Waste Management Rules 2022, r. 5-6 |
| Industrial hazardous waste | No | Manifest to an authorised treatment, storage and disposal facility | HOWM Rules 2016 |

Household items overlap with these regimes. Used needles, expired medicines, batteries, CFLs and tube lights from homes are **special care waste** under SWM r. 3(1)(zx). They go to deposition centres, one per 5 km² (SWM r. 39(48)), and are then handed to the right channel.

## Environment (Construction and Demolition) Waste Management Rules, 2025

- **Instrument:** G.S.R. 219(E), 2 April 2025. In force 1 April 2026. Supersedes the 2016 C&D rules.
- **Producer:** a project with a built-up area of 20,000 m² or more (r. 3(1)(q)). Producers meet EPR recycling targets and register on the CPCB portal.
- **Waste generator duties (r. 9):** segregate into material streams, then take the waste to a collection point or intermediate storage facility, or hand it to an authorised agency or recycler.
- **Local authority duties (r. 16(2)):** set up collection points, transport waste to registered recyclers and collect charges, and set up processing or intermediate storage within a year.
- **Terminology warning:** in these rules a "collection point" is a C&D deposit site (r. 3(1)(e)). In the Route Builder, the place where small vehicles unload for trucks is the SWM **secondary storage point** (SWM r. 3(1)(zs)).

## Bio-Medical Waste Management Rules, 2016

- **Instrument:** G.S.R. 343(E), 28 March 2016. **Verify:** the file is the original text, and later amendments are not included.
- **Key rules:**
  - Never mix untreated bio-medical waste with other waste (r. 8(1)).
  - Occupiers must not give treated bio-medical waste with municipal solid waste (r. 4(f)).
  - Segregated waste goes to a common treatment facility (r. 7(2)).
  - No on-site treatment where such a facility serves within 75 km (r. 7(3)).
  - Anatomical and soiled waste may not be stored beyond 48 hours (r. 8(7)).
- **Colour codes:** yellow (anatomical, soiled, medicines, chemical), red (contaminated recyclables), white translucent (sharps), blue (glassware and metal implants).
- **For routing:** hospitals and clinics join municipal routes only for their general solid waste.

## E-Waste (Management) Rules, 2022

- **Instrument:** G.S.R. 801(E), 2 November 2022. In force 1 April 2023. Supersedes the 2016 rules.
- **Scope:** Schedule I equipment, including mercury lamps and solar photovoltaic panels. Excludes waste batteries and packaging plastics, which have their own rules.
- **Bulk consumer:** an entity using 1,000 or more units of Schedule I equipment in a financial year (r. 3(1)(b)). It hands e-waste only to registered producers, refurbishers or recyclers (r. 8). This is not the SWM bulk waste generator test.
- **Local body duties (Schedule V, item 3):**
  - Separate any e-waste found mixed with municipal solid waste and send it to a registered recycler or refurbisher.
  - Collect and channelise e-waste from orphaned products.
  - Facilitate e-waste collection, segregation and disposal systems.
- **Storage:** up to 180 days, extendable to 365 days by CPCB (r. 11).

## Battery Waste Management Rules, 2022 (as amended to 2025)

- **Instrument:** S.O. 3984(E), 22 August 2022, in force on publication. Supersedes the 2001 rules. The library holds the February 2025 amendment (S.O. 958(E)). Four earlier amendments from October 2023 to December 2024 are not in the library.
- **Scope:** all batteries, whatever the chemistry or size, including electric vehicle batteries.
- **Consumer (r. 5):** discard waste batteries separately from mixed and domestic waste and give them to a collection, refurbishment or recycling entity.
- **ULB (r. 6):** as a Public Waste Management Authority, hand collected waste batteries to producers, their agencies, or registered refurbishers or recyclers.
- **Fleet note:** traction batteries of the e-loader and EV mini tipper classes fall under these rules at end of life. EPR collection targets for e-rickshaw batteries apply from 2024-25 (Schedule II(x)).
- **2025 amendment:** labelling only. The EPR registration number may be printed as a barcode or QR code.

## CPCB Implementation Guidelines for E-Waste (Management) Rules, 2016 (superseded)

- **Document type:** CPCB guidelines, not the rules. **Superseded:** the 2016 rules were replaced by the E-Waste (Management) Rules, 2022 above. Kept for reference.
- **Consumers (section 9.1):** use producer take-back or authorised collection centres, and never municipal bins. Pack fluorescent lamps upright.
- **Bulk consumers (section 9.2):** hand e-waste only to producer take-back or authorised recyclers, and keep separate bins for lamps.

## Batteries (Management and Handling) Rules, 2001 (superseded)

- **Instrument:** S.O. 432(E), 16 May 2001, amended 2010. Covered lead acid batteries only. **Superseded** by the Battery Waste Management Rules, 2022 above. Kept for reference.
- **Take-back chain:**
  - Consumers return used batteries only to dealers, manufacturers, registered recyclers or designated collection centres (r. 10).
  - Dealers take them back against new sales (r. 7).

## Hazardous and Other Wastes (Management and Transboundary Movement) Rules, 2016

- **Instrument:** G.S.R. 395(E), 4 April 2016. **Verify:** later amendments are not included.
- **Scope:** excludes municipal solid waste and bio-medical waste (r. 2).
- **Transport:** hazardous waste moves with Form 9 information and a seven-copy manifest in Form 10 (r. 18-19).
- **Link to SWM:** SWM Rules 2026 Schedule III sends incineration ash with toxic metals above HOWM limits to a hazardous waste disposal facility.

## Plastic Waste Management (Amendment) Rules, 2021 and (Second Amendment) Rules, 2022

- **Instruments:** G.S.R. 571(E), 12 August 2021, and G.S.R. 522(E), 6 July 2022. These amend the principal 2016 rules, which are **not** in the library. The file provided in the second batch was the 2021 amendment again, so the principal rules are still needed.
- **2021:**
  - Carry bags at least 75 microns from 30 September 2021 and 120 microns from 31 December 2022.
  - Listed single-use plastic items banned from 1 July 2022.
- **2022:**
  - Defines biodegradable plastics, end-of-life disposal, waste to energy and plastic waste processors.
  - Producers, importers and brand owners fulfil EPR for plastic packaging per Schedule II.
- **For routing:** plastic stays in the dry waste stream. These rules shape what the MRF does with it, not how it is collected.
