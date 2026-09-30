// Waste Generator Map: surveyor and planner modes over OSM buildings in the pilot.
// Terms and rule citations follow the SWM Rules 2026 (see rules.html). Quantities are estimates.

const PILOT = "hsr";
const ROLE = new URLSearchParams(location.search).get("role") === "planner" ? "planner" : "surveyor";
const $ = (id) => document.getElementById(id);
const byId = new Map();
let fc = null;
let summary = null;
let selectedId = null;
let sectorsFC = null;

const CATEGORY_COLOURS = {
  residential_house: "#93c5fd", residential_apartment: "#1d4ed8", unclassified: "#cbd5e1",
  mixed_use: "#a855f7", commercial_retail: "#f59e0b", commercial_office: "#0d9488",
  food_service: "#ef4444", hotel: "#db2777", healthcare: "#16a34a", educational: "#84cc16",
  religious: "#eab308", market: "#991b1b", industrial: "#78716c", public_institutional: "#0891b2",
  construction: "#92400e", structure: "#e5e7eb",
};
const TYPE_COLOURS = { household: "#3b82f6", non_residential: "#f97316", mixed: "#a855f7", none: "#d1d5db" };
const TYPE_LABELS = { household: "Household", non_residential: "Commercial / institutional", mixed: "Mixed use", none: "No regular waste" };
const CONF_COLOURS = { high: "#16a34a", medium: "#eab308", low: "#ef4444" };
const CONF_LABELS = { high: "OSM tag or mapped place inside", medium: "From land use only", low: "No information: assumed household" };
const COMPLIANCE_COLOURS = { processing_at_source: "#16a34a", ebwgr_certificate: "#0d9488", non_compliant: "#dc2626", not_verified: "#f59e0b" };
const SURVEY_COLOURS = { surveyed: "#16a34a", priority: "#dc2626", unsurveyed: "#fbbf24" };
const LANDUSE_COLOURS = {
  residential: "#fde68a", commercial: "#fca5a5", retail: "#fca5a5", industrial: "#d8b4fe", religious: "#fcd34d",
  education: "#bfdbfe", construction: "#d6d3d1", park: "#86efac", garden: "#86efac", playground: "#86efac",
  pitch: "#86efac", recreation_ground: "#86efac", grass: "#86efac", cemetery: "#a7f3d0", marketplace: "#fb923c",
};
const STREAMS = {
  wet: { label: "Wet waste", colour: "#16a34a", bin: "green bin" },
  dry: { label: "Dry waste", colour: "#2563eb", bin: "blue bin" },
  sanitary: { label: "Sanitary waste", colour: "#dc2626", bin: "red bin" },
  special: { label: "Special care waste", colour: "#6b7280", bin: "deposition centre" },
};
const FRACTIONS = { paper: "Paper", plastic: "Plastic", metal: "Metal", glass: "Glass", wood: "Wood", rubber: "Rubber", other_and_rejects: "Other and rejects" };
const USE_LABELS = { residential: "Residential", commercial: "Commercial", mixed: "Mixed", institutional: "Institutional", vacant: "Vacant", construction: "Under construction" };
const ONSITE_LABELS = { none: "None", compost: "Composting", biogas: "Biogas", certificate: "EBWGR certificate" };
const SEG_LABELS = { mixed: "Mixed", partial: "Partial", four_stream: "Four-stream" };
const HOME_COMPOST_LABELS = { no: "No", all: "Yes, all wet waste", most: "Yes, most of it", some: "Yes, some of it" };
const HOME_COMPOST_METHODS = { pit: "Compost pit", bin: "Compost bin / pot", aerobic: "Aerobic composting unit", biogas: "Biogas unit" };

const COLOUR_MODES = ROLE === "surveyor"
  ? { survey: "Survey status", category: "Generator category (OSM guess)", confidence: "Classification confidence" }
  : { quantity: "Waste quantity (stream, fraction)", category: "Generator category", generator_type: "Household vs commercial", compliance: "BWG compliance", survey: "Survey status" };

const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const fmtQ = (v) => fmt(v, v < 10 ? 2 : 1);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const matchExpr = (prop, colours, fallback = "#e5e7eb") => ["match", ["to-string", ["get", prop]], ...Object.entries(colours).flat(), fallback];
const isPriority = ["in", ["get", "bwg_status"], ["literal", ["bwg", "watch"]]];

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// ---------- Colouring ----------

function quantityProp() {
  const s = $("stream").value;
  return s === "dry" && $("fraction").value ? `dry_${$("fraction").value}` : s;
}

function quantityStops(prop) {
  const vals = fc.features.map((f) => f.properties[prop] || 0).filter((v) => v > 0).sort((a, b) => a - b);
  if (!vals.length) return [0, 1, 2, 3];
  const q = (p) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
  // Many houses share one estimate, so step to the next distinct value when quantiles collide.
  const stops = [q(0.5)];
  for (const p of [0.9, 0.99]) stops.push(vals.find((v) => v > stops[stops.length - 1] && v >= q(p)) ?? stops[stops.length - 1] + 0.01);
  stops.push(Math.max(vals[vals.length - 1], stops[2] + 0.01));
  return stops;
}

function colourSpec() {
  const mode = $("colourBy").value;
  if (mode === "category") return { expr: matchExpr("category", CATEGORY_COLOURS), legend: Object.entries(CATEGORY_COLOURS).filter(([k]) => summary.category_counts[k]).map(([k, c]) => [c, summary.category_labels[k], summary.category_counts[k]]) };
  if (mode === "generator_type") return { expr: matchExpr("generator_type", TYPE_COLOURS), legend: Object.entries(TYPE_COLOURS).map(([k, c]) => [c, TYPE_LABELS[k], summary.generator_type_counts[k]]) };
  if (mode === "confidence") return { expr: matchExpr("confidence", CONF_COLOURS), legend: Object.entries(CONF_COLOURS).map(([k, c]) => [c, CONF_LABELS[k], summary.confidence_counts[k]]) };
  if (mode === "compliance") return {
    expr: matchExpr("bwg_compliance", COMPLIANCE_COLOURS, "#e5e7eb"),
    legend: [...Object.entries(COMPLIANCE_COLOURS).map(([k, c]) => [c, summary.compliance_labels[k], summary.compliance_counts[k] || 0]), ["#e5e7eb", "Not a bulk waste generator"]],
  };
  if (mode === "survey") {
    const n = countSurvey();
    return {
      expr: ["case", ["to-boolean", ["get", "surveyed"]], SURVEY_COLOURS.surveyed, isPriority, SURVEY_COLOURS.priority, SURVEY_COLOURS.unsurveyed],
      legend: [[SURVEY_COLOURS.priority, "Survey first: likely bulk generator", n.priority], [SURVEY_COLOURS.unsurveyed, "Not surveyed", n.unsurveyed], [SURVEY_COLOURS.surveyed, "Surveyed", n.surveyed]],
    };
  }
  const prop = quantityProp();
  const [a, b, c, d] = quantityStops(prop);
  const colours = ["#f1f5f9", "#fde68a", "#fb923c", "#dc2626", "#7f1d1d"];
  return {
    expr: ["interpolate", ["linear"], ["get", prop], 0, colours[0], a, colours[1], b, colours[2], c, colours[3], d, colours[4]],
    legend: [[colours[0], "0 kg/day"], [colours[1], `${fmtQ(a)} (median)`], [colours[2], `${fmtQ(b)} (top 10%)`], [colours[3], `${fmtQ(c)} (top 1%)`], [colours[4], `${fmtQ(d)} kg/day (max)`]],
  };
}

function countSurvey() {
  const n = { surveyed: 0, priority: 0, unsurveyed: 0 };
  fc.features.forEach(({ properties: p }) => {
    if (p.surveyed) n.surveyed++;
    else if (p.bwg_status === "bwg" || p.bwg_status === "watch") n.priority++;
    else n.unsurveyed++;
  });
  return n;
}

function applyColour() {
  const spec = colourSpec();
  map.setPaintProperty("buildings", "fill-color", spec.expr);
  map.setPaintProperty("buildings-3d", "fill-extrusion-color", spec.expr);
  $("quantityControls").hidden = $("colourBy").value !== "quantity";
  const title = $("colourBy").value === "quantity"
    ? `<div class="muted small">Estimated ${esc(($("stream").value === "kg_day" ? "total waste" : STREAMS[$("stream").value].label.toLowerCase()) + ($("fraction").value ? `: ${FRACTIONS[$("fraction").value].toLowerCase()}` : ""))}, kg/day</div>`
    : "";
  $("legend").innerHTML = title +
    spec.legend.map(([c, label, n]) => `<div><span class="swatch" style="background:${c}"></span>${esc(label)}${n != null ? ` <span class="muted">${fmt(n)}</span>` : ""}</div>`).join("") +
    `<div><span class="swatch outline"></span>Potential bulk waste generator <span class="muted">${fmt(summary.bwg_counts.bwg || 0)}</span></div>` +
    `<div><span class="swatch outline dashed"></span>Near a BWG threshold <span class="muted">${fmt(summary.bwg_counts.watch || 0)}</span></div>`;
}

function applyFilters() {
  const sector = $("sector").value;
  const conds = [];
  if (sector) conds.push(["==", ["get", "sector"], sector]);
  if ($("filterFocus").checked) conds.push(ROLE === "surveyor" ? ["!", ["to-boolean", ["get", "surveyed"]]] : ["==", ["get", "bwg_status"], "bwg"]);
  const f = conds.length ? ["all", ...conds] : null;
  ["buildings", "buildings-line", "buildings-3d"].forEach((id) => map.setFilter(id, f));
  map.setFilter("bwg", ["all", ["==", ["get", "bwg_status"], "bwg"], ...conds]);
  map.setFilter("watch", ["all", ["==", ["get", "bwg_status"], "watch"], ...conds]);
}

// ---------- Map ----------

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.45, "raster-saturation": -0.6 } }],
  },
  center: [77.641, 12.9125],
  zoom: 14.2,
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");

function eachCoord(geom, fn) {
  const walk = (c) => (typeof c[0] === "number" ? fn(c) : c.forEach(walk));
  walk(geom.coordinates);
}
function boundsOf(geom) {
  const b = new maplibregl.LngLatBounds();
  eachCoord(geom, (c) => b.extend(c));
  return b;
}

map.on("load", async () => {
  try {
    document.title = ROLE === "surveyor" ? "Surveyor Map" : "Planner Map";
    $("title").textContent = ROLE === "surveyor" ? "Surveyor: building cards" : "Planner: generators and quantities";
    document.querySelector(`.tabs a[data-role="${ROLE}"]`)?.classList.add("active");
    $("colourBy").innerHTML = Object.entries(COLOUR_MODES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    $("filterFocusLabel").textContent = ROLE === "surveyor" ? "Only unsurveyed buildings" : "Only potential bulk waste generators";
    $("showLanduse").checked = ROLE === "planner";

    const [sectors, landuse] = await Promise.all([api(`/api/pilots/${PILOT}/sectors`), api(`/api/pilots/${PILOT}/landuse`)]);
    map.addSource("landuse", { type: "geojson", data: landuse });
    map.addLayer({ id: "landuse", type: "fill", source: "landuse", layout: { visibility: $("showLanduse").checked ? "visible" : "none" }, paint: { "fill-color": matchExpr("lu_class", LANDUSE_COLOURS), "fill-opacity": 0.4 } });
    sectorsFC = sectors;
    map.addSource("sectors", { type: "geojson", data: sectors });
    map.addLayer({ id: "sectors", type: "line", source: "sectors", paint: { "line-color": "#0f172a", "line-width": 2, "line-dasharray": [3, 2] } });
    sectors.features.forEach((f) => {
      $("sector").insertAdjacentHTML("beforeend", `<option>${esc(f.properties.name)}</option>`);
      const el = document.createElement("div");
      el.className = "sector-label";
      el.textContent = f.properties.name;
      new maplibregl.Marker({ element: el }).setLngLat(boundsOf(f.geometry).getCenter()).addTo(map);
    });
    map.fitBounds(boundsOf({ coordinates: sectors.features.map((f) => f.geometry.coordinates) }), { padding: 20 });

    $("status").textContent = "Loading about 11,500 buildings…";
    const [buildings, overrides] = await Promise.all([api(`/api/pilots/${PILOT}/buildings`), api(`/api/pilots/${PILOT}/overrides`)]);
    fc = buildings;
    fc.features.forEach((f) => {
      byId.set(f.properties.id, f);
      if (overrides[f.properties.id]) Object.assign(f.properties, overrides[f.properties.id]);
    });
    map.addSource("buildings", { type: "geojson", data: fc });
    map.addLayer({ id: "buildings", type: "fill", source: "buildings", paint: { "fill-color": "#cbd5e1", "fill-opacity": 0.88 } });
    map.addLayer({ id: "buildings-3d", type: "fill-extrusion", source: "buildings", layout: { visibility: "none" },
      paint: { "fill-extrusion-color": "#cbd5e1", "fill-extrusion-height": ["*", ["coalesce", ["get", "levels"], 1], 3.2], "fill-extrusion-opacity": 0.9 } });
    map.addLayer({ id: "buildings-line", type: "line", source: "buildings", minzoom: 15, paint: { "line-color": "#475569", "line-width": 0.4 } });
    map.addLayer({ id: "bwg", type: "line", source: "buildings", filter: ["==", ["get", "bwg_status"], "bwg"], paint: { "line-color": "#7f1d1d", "line-width": 2.5 } });
    map.addLayer({ id: "watch", type: "line", source: "buildings", filter: ["==", ["get", "bwg_status"], "watch"], paint: { "line-color": "#7f1d1d", "line-width": 1.5, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "selected", type: "line", source: "buildings", filter: ["==", ["get", "id"], ""], paint: { "line-color": "#000", "line-width": 3.5 } });

    for (const layer of ["buildings", "buildings-3d"]) {
      map.on("click", layer, (e) => select(e.features[0].properties.id));
      map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    }
    await refreshSummary();
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  }
});

async function refreshSummary() {
  summary = await api(`/api/pilots/${PILOT}/summary`);
  applyColour();
  applyFilters();
  renderRoleSections();
  $("assumptions").textContent = summary.assumptions_note;
  $("status").textContent = ROLE === "surveyor"
    ? `${fmt(summary.surveyed)} of ${fmt(summary.buildings)} buildings surveyed. Tap a building to open its card.`
    : `${fmt(summary.buildings)} buildings · ${fmt(summary.total_tpd, 1)} t/day estimated · ${fmt(summary.surveyed)} surveyed. Tap a building for its card.`;
}

// ---------- Sidebar sections by role ----------

function listItems(items, extra) {
  return items.map((b) => `<div class="result" data-id="${esc(b.id)}"><b>${esc(b.name || b.address || "Unnamed building")}</b> · ${esc(b.category_label)}<br><span class="muted">${esc(b.sector || "")} · ~${fmt(b.kg_day)} kg/day (est.)${extra ? extra(b) : ""}</span></div>`).join("")
    || `<div class="muted small">None.</div>`;
}

function renderRoleSections() {
  const s = summary;
  const bySector = (cols) => `<div class="scroll"><table><tr>${cols.map((c) => `<th>${c[0]}</th>`).join("")}</tr>` +
    s.by_sector.map((r) => `<tr class="clickable" data-sector="${esc(r.sector)}">${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("") + `</table></div>`;

  if (ROLE === "surveyor") {
    const pct = s.buildings ? (100 * s.surveyed) / s.buildings : 0;
    $("roleSections").innerHTML = `
      <section>
        <h2>Survey progress</h2>
        <div class="progress"><div style="width:${pct}%"></div></div>
        <p class="hint">${fmt(s.surveyed)} of ${fmt(s.buildings)} buildings surveyed (${fmt(pct, 1)}%). OSM gives footprints and ${fmt(s.with_house_number)} house numbers, but building use comes from the survey.</p>
        ${bySector([["Sector", (r) => esc(r.sector)], ["Buildings", (r) => fmt(r.buildings)], ["Surveyed", (r) => fmt(r.surveyed)], ["Likely BWG", (r) => fmt(r.bwg)]])}
      </section>
      <section>
        <h2>Survey first <span class="cite">r. 39(36)</span></h2>
        <p class="hint">Unsurveyed buildings whose estimates meet or approach a bulk waste generator threshold. ULBs must identify BWGs by survey and geo-tag them.</p>
        <div class="results tall">${listItems(s.survey_priority, (b) => (b.bwg_status === "watch" ? " · near threshold" : ""))}</div>
      </section>`;
  } else {
    const streamRows = Object.entries(s.stream_tpd).map(([k, v]) => `<tr><td><span class="swatch" style="background:${STREAMS[k].colour}"></span>${STREAMS[k].label}</td><td>${STREAMS[k].bin}</td><td>${fmt(v, 1)}</td></tr>`).join("");
    const fracRows = Object.entries(s.dry_fraction_tpd).map(([k, v]) => `<tr><td>${esc(s.dry_fraction_labels[k])}</td><td>${fmt(v, 2)}</td></tr>`).join("");
    const comp = Object.entries(s.compliance_labels).map(([k, label]) => `<tr><td><span class="swatch" style="background:${COMPLIANCE_COLOURS[k]}"></span>${esc(label)}</td><td>${fmt(s.compliance_counts[k] || 0)}</td></tr>`).join("");
    $("roleSections").innerHTML = `
      <section>
        <h2>Pilot totals (estimated)</h2>
        <table>
          <tr><td>Buildings / surveyed</td><td>${fmt(s.buildings)} / ${fmt(s.surveyed)}</td></tr>
          <tr><td>Households / population</td><td>${fmt(s.households_est)} / ${fmt(s.population_est)}</td></tr>
          <tr><td>Total waste</td><td>${fmt(s.total_tpd, 1)} t/day</td></tr>
        </table>
        <h3>By stream <span class="cite">r. 5(1)(b)</span></h3>
        <table><tr><th>Stream</th><th>Goes in</th><th>t/day</th></tr>${streamRows}</table>
        <h3>Dry waste fractions to MRF <span class="cite">r. 9, r. 3(1)(zw)</span></h3>
        <table><tr><th>Fraction</th><th>t/day</th></tr>${fracRows}</table>
        <p class="hint">Special care waste: ${fmt(s.special_care.tpd, 1)} t/day. At least ${s.special_care.deposition_centres_required} deposition centres are needed for ${s.area_km2} km² (one per 5 km², r. 39(48)). E-waste is governed by the E-Waste Rules; a registered MRF may act as its deposition centre (r. 9(7)(ii)).</p>
      </section>
      <section>
        <h2>By sector</h2>
        ${bySector([["Sector", (r) => esc(r.sector)], ["Wet", (r) => fmt(r.wet_tpd, 1)], ["Dry", (r) => fmt(r.dry_tpd, 1)], ["t/day", (r) => fmt(r.tpd, 1)], ["BWG", (r) => fmt(r.bwg)],
          ["", (r) => `<a href="builder.html?sector=${encodeURIComponent(r.sector)}">Routes →</a>`]])}
      </section>
      <section>
        <h2>Bulk waste generators <span class="cite">r. 3(1)(i), r. 6</span></h2>
        <table>${comp}</table>
        <div class="results tall">${listItems(s.bwg_list, (b) => ` · ${esc(s.compliance_labels[b.compliance] || "")}`)}</div>
      </section>`;
  }
  document.querySelectorAll("#roleSections tr.clickable").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.tagName === "A") return;
    $("sector").value = tr.dataset.sector;
    applyFilters();
    zoomToSector(tr.dataset.sector);
  }));
  document.querySelectorAll("#roleSections .result").forEach((el) => el.addEventListener("click", () => select(el.dataset.id, true)));
}

function zoomToSector(name) {
  const f = sectorsFC.features.find((x) => x.properties.name === name);
  if (f) map.fitBounds(boundsOf(f.geometry), { padding: 30 });
}

// ---------- Building card ----------

async function select(id, fly = false) {
  const f = byId.get(id);
  if (!f) return;
  selectedId = id;
  map.setFilter("selected", ["==", ["get", "id"], id]);
  if (fly) map.fitBounds(boundsOf(f.geometry), { padding: 120, maxZoom: 18 });
  $("detail").hidden = false;
  $("detail").innerHTML = `<p class="hint">Loading building card…</p>`;
  const p = await api(`/api/pilots/${PILOT}/building?id=${encodeURIComponent(id)}`);
  if (selectedId === id) renderCard(p);
}

function renderCard(p) {
  const [kind, num] = p.id.split("/");
  const title = p.name || p.address || "Unnamed building";
  const bwgBadge = {
    bwg: `<span class="badge bad">Bulk waste generator</span>`,
    watch: `<span class="badge warn">Near a threshold</span>`,
    no: `<span class="badge">No</span>`,
    not_applicable: `<span class="badge">Not a BWG entity type</span>`,
  }[p.bwg_status];
  const s = p.survey || {};
  const streamRows = Object.keys(STREAMS).map((k) => `<tr><td><span class="swatch" style="background:${STREAMS[k].colour}"></span>${STREAMS[k].label}</td><td>${fmt(p[k], 1)}</td></tr>`).join("");
  const fracRows = Object.keys(FRACTIONS).map((k) => `<tr><td class="indent">${FRACTIONS[k]}</td><td>${fmt(p[`dry_${k}`], 2)}</td></tr>`).join("");

  $("detail").innerHTML = `
    <h2>${esc(title)} <button class="close" id="closeDetail" aria-label="Close">×</button></h2>
    <p class="hint">${esc(p.sector || "")} · <a href="https://www.openstreetmap.org/${kind}/${num}" target="_blank" rel="noopener">OSM ${esc(p.id)}</a>
      ${p.house_number ? ` · House no. <b>${esc(p.house_number)}</b>` : " · No house number in OSM"}${p.street ? ` · ${esc(p.street)}` : ""}</p>
    <table class="card">
      <tr><td>Building use</td><td>${p.surveyed && s.use ? `<b>${USE_LABELS[s.use]}</b> (surveyed)` : `<span class="muted">Not surveyed. OSM guess:</span>`}
        <br><span class="swatch" style="background:${CATEGORY_COLOURS[p.category]}"></span>${esc(p.category_label)}${p.sub_use ? ` (ground floor: ${esc(p.sub_use.replaceAll("_", " "))})` : ""}
        ${!p.surveyed ? `<br><span class="muted small">Why: ${esc(p.basis)} · confidence ${esc(p.confidence)}</span>` : ""}</td></tr>
      <tr><td>Units</td><td>${s.units != null ? fmt(s.units) + (s.commercial_units ? ` (${fmt(s.commercial_units)} commercial)` : "") : `<span class="muted">Not surveyed</span>${p.dwellings ? ` · est. ${fmt(p.dwellings)} households` : ""}`}</td></tr>
      <tr><td>Floor area</td><td>${fmt(p.footprint_m2)} m² × ${fmt(p.levels, 1)} floors (${esc(p.levels_source)}) = ${fmt(p.floor_area_m2)} m²</td></tr>
      <tr><td>Water use</td><td>${p.water_lpd != null ? `${fmt(p.water_lpd)} L/day (${esc(p.water_source)})` : `<span class="muted">Not recorded (surveyor or BWSSB)</span>`}</td></tr>
    </table>
    <h3>Estimated waste: ${fmt(p.kg_day, 1)} kg/day</h3>
    <p class="muted small">${esc(p.quantity_source)}${p.quantity_source !== "weighed" ? ". Replace with weighed data when available." : ""}</p>
    <table>${streamRows}<tr><td colspan="2" class="muted small">Dry waste fractions</td></tr>${fracRows}</table>
    ${p.wet_home_composted > 0 ? `<p class="small"><b>${fmt(p.wet_home_composted, 1)} kg/day</b> of the wet waste is composted in the building (${esc(p.home_compost_basis)}), so only <b>${fmt(p.wet_to_collect, 1)} kg/day</b> of wet waste is collected.</p>` : ""}
    <h3>Bulk waste generator <span class="cite">r. 3(1)(i)</span></h3>
    <p>${bwgBadge}${p.bwg_group ? ` <span class="muted small">${esc(p.bwg_group)}</span>` : ""}</p>
    ${p.bwg_criteria?.length ? `<ul class="small">${p.bwg_criteria.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${p.bwg_compliance ? `<p><span class="swatch" style="background:${COMPLIANCE_COLOURS[p.bwg_compliance]}"></span>${esc(summary.compliance_labels[p.bwg_compliance])} <span class="cite">r. 6(c)-(f)</span></p>` : ""}
    <table class="card">
      <tr><td>On-site processing</td><td>${s.onsite_processing ? ONSITE_LABELS[s.onsite_processing] : `<span class="muted">Not surveyed</span>`}</td></tr>
      <tr><td>Composting in the building</td><td>${s.home_compost ? HOME_COMPOST_LABELS[s.home_compost] + (s.home_compost_method ? ` · ${HOME_COMPOST_METHODS[s.home_compost_method]}` : "") + (s.home_compost_kg != null ? ` · ${fmt(s.home_compost_kg, 1)} kg/day` : "") : `<span class="muted">Not asked</span>`}</td></tr>
      <tr><td>Segregation observed</td><td>${s.segregation_observed ? SEG_LABELS[s.segregation_observed] : `<span class="muted">No collector record</span>`}</td></tr>
    </table>
    <h3>Collection</h3>
    <p class="small">${esc(p.collection.label)} ${p.collection.rule ? `<span class="cite">${esc(p.collection.rule)}</span>` : ""}</p>
    ${p.obligations?.length ? `<h3>Obligations</h3><ul class="small">${p.obligations.map((o) => `<li>${esc(o.text)} <span class="cite">${esc(o.rule)}</span></li>`).join("")}</ul>` : ""}
    ${ROLE === "surveyor" ? surveyForm(p) : ""}`;

  $("closeDetail").addEventListener("click", () => { $("detail").hidden = true; selectedId = null; map.setFilter("selected", ["==", ["get", "id"], ""]); });
  if (ROLE === "surveyor") wireSurveyForm(p);
  $("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function options(labels, value, blank = "Select…") {
  return `<option value="">${blank}</option>` + Object.entries(labels).map(([k, v]) => `<option value="${k}"${k === value ? " selected" : ""}>${v}</option>`).join("");
}

function surveyForm(p) {
  const s = p.survey || {};
  let surveyor = s.surveyor || "";
  try { surveyor = surveyor || localStorage.getItem("surveyor") || ""; } catch { /* storage unavailable */ }
  return `
    <form id="surveyForm" class="survey">
      <h3>${p.surveyed ? "Update survey" : "Fill building card"}</h3>
      <label>Building use <select name="use" required>${options(USE_LABELS, s.use)}</select></label>
      <div class="row">
        <label>Number of units <input name="units" type="number" min="0" step="1" value="${s.units ?? ""}" placeholder="homes / shops / rooms" /></label>
        <label id="comUnitsWrap">Of which commercial <input name="commercial_units" type="number" min="0" step="1" value="${s.commercial_units ?? ""}" /></label>
      </div>
      <label>Floors <input name="floors" type="number" min="1" max="100" step="1" value="${s.floors ?? ""}" placeholder="OSM: ${p.levels_source === "from OSM" ? p.levels : "unknown"}" /></label>
      <div class="row">
        <label>Water use, L/day <input name="water_lpd" type="number" min="0" step="100" value="${s.water_lpd ?? ""}" /></label>
        <label>Water source <select name="water_source">${options({ surveyor: "Surveyor estimate", bwssb: "BWSSB bill" }, s.water_source, "—")}</select></label>
      </div>
      <label>On-site processing (bulk waste generators) <select name="onsite_processing">${options(ONSITE_LABELS, s.onsite_processing, "Not checked")}</select></label>
      <label>Is wet waste composted in the building? <select name="home_compost">${options(HOME_COMPOST_LABELS, s.home_compost, "Not asked")}</select></label>
      <div class="row" id="homeCompostWrap">
        <label>How <select name="home_compost_method">${options(HOME_COMPOST_METHODS, s.home_compost_method, "—")}</select></label>
        <label>kg/day (optional) <input name="home_compost_kg" type="number" min="0" step="0.1" value="${s.home_compost_kg ?? ""}" placeholder="if known" /></label>
      </div>
      <label>Segregation observed (collector) <select name="segregation_observed">${options(SEG_LABELS, s.segregation_observed, "Not observed")}</select></label>
      <details${s.weighed_kg_day != null ? " open" : ""}><summary class="small">Weighed data (optional)</summary>
        <label>Weighed waste, kg/day <input name="weighed_kg_day" type="number" min="0" step="0.1" value="${s.weighed_kg_day ?? ""}" /></label>
      </details>
      <div class="row">
        <label>Surveyor <input name="surveyor" value="${esc(surveyor)}" /></label>
        <label>Notes <input name="notes" value="${esc(s.notes || "")}" /></label>
      </div>
      <button type="submit">Save building card</button>
      ${p.surveyed ? `<button type="button" id="clearSurvey" class="secondary">Clear survey</button>` : ""}
      <p id="surveyMsg" class="hint"></p>
    </form>`;
}

function wireSurveyForm(p) {
  const form = $("surveyForm");
  const toggle = () => ($("comUnitsWrap").hidden = form.use.value !== "mixed");
  form.use.addEventListener("change", toggle);
  toggle();
  const toggleCompost = () => ($("homeCompostWrap").hidden = !["all", "most", "some"].includes(form.home_compost.value));
  form.home_compost.addEventListener("change", toggleCompost);
  toggleCompost();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    for (const k of Object.keys(data)) if (data[k] === "") data[k] = null;
    if (data.use !== "mixed") data.commercial_units = null;
    try { if (data.surveyor) localStorage.setItem("surveyor", data.surveyor); } catch { /* ignore */ }
    try {
      const updated = await api(`/api/pilots/${PILOT}/survey?id=${encodeURIComponent(p.id)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
      });
      afterSave(updated, "Saved.");
    } catch (err) {
      $("surveyMsg").textContent = `Could not save: ${err.message}`;
    }
  });
  $("clearSurvey")?.addEventListener("click", async () => {
    const updated = await api(`/api/pilots/${PILOT}/survey?id=${encodeURIComponent(p.id)}`, { method: "DELETE" });
    afterSave(updated, "Survey cleared.");
  });
}

async function afterSave(updated, msg) {
  const f = byId.get(updated.id);
  const keep = Object.keys(f.properties);
  keep.forEach((k) => { if (k in updated) f.properties[k] = updated[k]; });
  map.getSource("buildings").setData(fc);
  renderCard(updated);
  $("surveyMsg").textContent = msg;
  await refreshSummary();
}

// ---------- Controls ----------

$("colourBy").addEventListener("change", applyColour);
$("stream").addEventListener("change", () => {
  const dry = $("stream").value === "dry";
  $("fraction").disabled = !dry;
  $("fraction").innerHTML = `<option value="">All</option>` + (dry ? Object.entries(FRACTIONS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("") : "");
  applyColour();
});
$("fraction").addEventListener("change", applyColour);
$("sector").addEventListener("change", (e) => { applyFilters(); if (e.target.value) zoomToSector(e.target.value); });
$("filterFocus").addEventListener("change", applyFilters);
$("showLanduse").addEventListener("change", (e) => map.setLayoutProperty("landuse", "visibility", e.target.checked ? "visible" : "none"));
$("show3d").addEventListener("change", (e) => {
  const on = e.target.checked;
  map.setLayoutProperty("buildings-3d", "visibility", on ? "visible" : "none");
  map.setLayoutProperty("buildings", "visibility", on ? "none" : "visible");
  map.easeTo({ pitch: on ? 55 : 0, bearing: on ? -20 : 0 });
});
$("search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  if (!q) { $("searchResults").innerHTML = ""; return; }
  const hits = [];
  for (const f of byId.values()) {
    const p = f.properties;
    if ((p.house_number && p.house_number.toLowerCase() === q) || (q.length > 2 && `${p.name || ""} ${p.address || ""}`.toLowerCase().includes(q))) hits.push(p);
    if (hits.length >= 12) break;
  }
  $("searchResults").innerHTML = hits.length
    ? hits.map((p) => `<div class="result" data-id="${esc(p.id)}"><b>${esc(p.name || p.address || p.id)}</b> <span class="muted">${esc(p.sector || "")}</span></div>`).join("")
    : `<div class="muted small">No match. Only about a third of buildings have a house number in OSM.</div>`;
  document.querySelectorAll("#searchResults .result").forEach((el) => el.addEventListener("click", () => select(el.dataset.id, true)));
});
