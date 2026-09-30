# Solid Waste Management Rules, 2026

**Instrument:** S.O. 388(E), Ministry of Environment, Forest and Climate Change, Gazette of India Extraordinary, Part II, Section 3(ii), No. 360, published 28 January 2026.
**In force:** 1 April 2026. Supersedes the Solid Waste Management Rules, 2016.
**Text used:** English version. Differences from the Hindi text are listed at the end.
**Machine-readable version:** [`backend/regulations/library/swm_rules_2026.json`](../../backend/regulations/library/swm_rules_2026.json)

This is a working summary for the platform, not a substitute for the notification.

---

## 1. Scope (rule 2)

The rules apply to every urban and rural local body and every entity in its jurisdiction. That includes government, private and PPP bodies, SEZs, railways, airports, ports, defence establishments, places of pilgrimage, landowners, and every domestic, institutional, commercial and other non-residential waste generator.

They do **not** cover industrial, hazardous, bio-medical, e-waste, battery or radio-active waste. Those have their own rules.

## 2. The four source-segregated streams (rule 5(1)(b))

| Stream | Definition (rule) | Public bin colour (r. 39(51)) | Goes to |
|---|---|---|---|
| **Wet waste** | Kitchen, food, vegetable, meat, fruit, flower and similar biodegradable waste (3(1)(zzl)) | Green | Registered wet waste processing: composting or bio-methanation, on-site preferred |
| **Dry waste** | All waste other than wet, sanitary and special care waste, recyclable and non-recyclable (3(1)(s)) | Blue | Registered material recovery facility (MRF) |
| **Sanitary waste** | Used diapers, sanitary napkins, tampons, condoms, incontinence sheets (3(1)(zp)) | Red, in public toilets if required | Registered processor, dedicated incinerator or common bio-medical waste treatment facility |
| **Special care waste** | Paint drums, pesticide containers, CFLs and tube lights, expired medicines, mercury thermometers, batteries, needles and syringes, contaminated gauze, generated at household level (3(1)(zx)) | None specified | Deposition centre, one per 5 km² or part thereof (39(48)), then a registered processor |

Also kept separate:

- **Horticultural waste** from parks, gardens and medians (3(1)(y), 5(1)(e), 39(22)).
- **Construction and demolition waste**, under the C&D Rules 2025 (5(1)(d)).
- **Street sweepings and drain silt**, called inerts, which must never be mixed with wet waste (3(1)(z), 39(33)).
- **Bio-medical waste**, never mixed with solid waste (5(1)(k)).

## 3. Who generates what

### Waste generator (3(1)(zzh))
Every person, residential premises and non-residential establishment that generates solid waste. Duties are in rule 5: segregate into four streams, hand over to authorised collectors, pay user fees, and do not burn, bury or litter.

### Bulk waste generator, BWG (3(1)(i))
An entity from the lists below that meets **at least one** criterion:

| Criterion | Threshold |
|---|---|
| Building floor area | 20,000 m² or more |
| Water consumption | 40,000 litres per day |
| Solid waste generation | 100 kg per day |

- **Institutional:** government departments and undertakings, local bodies, PSUs and private companies, educational institutions, community places.
- **Commercial:** transport hubs, industrial units, malls, hotels, hospitals and nursing homes, hostels, wholesale markets and mandis, stadiums, community and convention halls, marriage halls, exhibition centres, tourist spots.
- **Residential:** residential societies.

BWG duties (rule 6):

- Register on the CPCB centralised portal.
- Process wet waste on site. New BWGs must do so. Existing BWGs may instead buy Extended Bulk Waste Generator Responsibility (EBWGR) certificates.
- Hand dry, sanitary and special care waste to authorised agencies.
- Deal only with registered entities.
- File annual returns by 30 June.

**ULB duty (39(36)):** identify BWGs by detailed survey, geo-tag them, and update the list on the portal by 1 April every year.

### Large premises (5(2))
Gated communities and institutions over 5,000 m², and all RWAs, market associations, hotels and restaurants, must within one year of notification segregate at source and process biodegradable waste on premises as far as possible.

### Development plans (35(2)(xiv))
Complexes over 200 dwellings or on plots over 5,000 m² need demarcated space for segregation, storage and decentralised processing.

## 4. Flow mechanism

```mermaid
flowchart LR
  subgraph Source["Waste generators (r.5)"]
    HH[Households]
    NR[Shops, offices, institutions]
    BWG[Bulk waste generators (r.6)]
  end

  HH -- "4 streams, door to door (r.39(4))" --> V
  NR -- "4 streams, door to door (r.39(4))" --> V
  BWG -- "on-site wet processing (r.6(c)-(d))" --> ONSITE[On-site compost / biomethanation]
  BWG -- "dry, sanitary, special care (r.6(b))" --> V

  V["Compartmentalised, covered, GPS-tracked vehicles (r.8(h), r.39(46))"]

  V -- wet --> WET["Wet waste processing: compost, biomethanation, CBG (r.39(23), r.39(45))"]
  V -- dry --> MRF["Material Recovery Facility (r.9)"]
  V -- sanitary --> SAN["Incinerator / CBWTF / registered processor (r.39(2)(xii))"]
  HH -- special care --> DEP["Deposition centre, 1 per 5 km2 (r.39(48))"]
  DEP --> SPC[Registered processor]

  MRF -- "recyclables" --> REC[Authorised recyclers / EPR uptake (r.9(3), 9(7))]
  MRF -- "combustibles >= 1500 kcal/kg" --> RDF["RDF / co-processing / WtE (r.11, r.13)"]
  MRF -- "rejects only" --> SLF["Sanitary landfill (r.14(4))"]
  WET -- "residues" --> SLF
```

Key rules on the flow:

- Streams stay segregated from source to facility. There is no intermixing in vehicles (8(h)(v)-(vi)).
- Decentralised processing is preferred to cut transport (3(1)(p), 39(45)).
- Waste of 1,500 kcal/kg or more may not be landfilled (13(1)).
- Only inerts, rejects and residues may go to a sanitary landfill. Wet waste and C&D waste never go there (14(4)).
- Markets are collected every day (39(19)).
- Garbage vulnerable points must be geo-mapped by 31 October 2026 (15(1)).

## 5. Digital and monitoring obligations

| Obligation | Rule |
|---|---|
| GPS tracking of the collection fleet in cities over 50,000 people | 8(h)(ix) |
| Central control room in cities over 5 lakh people | 39(34) |
| Geo-tagging of all SWM infrastructure and facilities | 39(8), 41(8) |
| Monthly generation and collection reporting on the portal | 39(40) |
| Annual ward-wise generation accounting | 39(39) |
| Ward-wise database of personnel including waste pickers | 39(53) |
| Monthly MRF throughput reporting | 39(44) |
| Online grievance redressal | 39(35) |

## 6. ULB solid waste action plan (39(2))

The plan must include ward-wise generation, a five-year projection, ward-wise collection and transport plans, and ward-wise maps of infrastructure, markets and community facilities. It also covers garbage vulnerable points, water-body ingress points, vacant plots at risk of dumping, sanitary and special care waste plans, and infrastructure needs. This list defines what the platform's outputs must be able to produce for each ward.

## 7. Reporting calendar

| What | When | Rule |
|---|---|---|
| BWG list update | 1 April | 39(36) |
| Infrastructure inventory update | 31 March | 39(20) |
| Facility, collector and MRF quarterly returns | 15th of the first month of the next quarter | 7(c), 8(g), 9(4) |
| MRF throughput and ULB generation/collection | Monthly | 39(44), 39(40) |
| BWG, facility and ULB annual returns | 30 June | 6(h), 7(c), 39(27) |
| SPCB report to CPCB (Form V) | 31 July | 20(4) |
| CPCB consolidated report | 31 August | 20(5) |

## 8. Key dates

| Date | What | Rule |
|---|---|---|
| 1 Apr 2026 | Rules in force | 1(2) |
| 1 Oct 2026 | CPCB centralised portal due | 16(1) |
| 31 Oct 2026 | Dumpsites and garbage vulnerable points geo-mapped | 15(1) |
| 28 Jan 2027 | Large premises segregation and on-site processing | 5(2) |
| 31 Mar 2027 | ULB bye-laws framed | 39(5) |
| 1 Oct 2027 | Schedule I deadline for million-plus cities, English text | Schedule I |

## 9. Other definitions used by the platform

| Term | Meaning | Rule |
|---|---|---|
| Door to door collection | Collection from the door step, or the gate or ground-floor point of a housing society or complex | 3(1)(r) |
| Primary collection | Source to secondary storage point or transfer station | 3(1)(zh) |
| Secondary storage | Temporary containment at depots, MRFs or bins before onward transport | 3(1)(zs) |
| Transfer station | Receives waste from collection areas and moves it in bulk in covered vehicles | 3(1)(zzc) |
| Material recovery facility | Temporary storage and sorting of non-wet waste, and transfer to recyclers or processors | 3(1)(ze) |
| Sorting | Separating paper and paperboard, plastic, metal, glass etc. | 3(1)(zw) |
| Decentralised processing | Dispersed facilities closest to the source to optimise transport | 3(1)(p) |
| Refuse derived fuel | Pellets or fluff from the combustible fraction, excluding chlorinated materials | 3(1)(zl) |
| Waste picker | Person informally collecting reusable and recyclable waste for livelihood | 3(1)(zzj) |
| Waste hierarchy | Prevention, reduction, reuse, recycling, recovery, disposal | 3(1)(zzi) |
| User fee | Fee on generators for full or part cost of SWM services | 3(1)(zzf) |
| Tipping fee | Fee paid to a processing or landfill operator | 3(1)(zzb) |

## 10. Differences between the Hindi and English texts

| Topic | English | Hindi | Platform uses |
|---|---|---|---|
| Schedule I urban timelines | 18 / 24 / 36 months | 6 / 12 / 18 months | English. Confirm before committing to a deadline |
| CPCB norms for WtE and CBG plants | 1 October 2026 | 1 April 2026 | English |
| Rural State policy deadline | 31 September 2026, which is not a real date | Same | Treated as 30 September 2026 |
| C&D rules referenced | 2025 rules | 2016 rules | English |
| Fertiliser offtake reporting | 30 June every year | 30 June 2024 | English |
