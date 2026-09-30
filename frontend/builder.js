// Route Builder v2, step 2: review door-to-door collection points (street runs).

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const empty = { type: "FeatureCollection", features: [] };
const state = { data: null, byId: new Map(), buildings: null, sectorsFC: null };

const USE_COLOURS = { residential: "#2563eb", commercial: "#f97316", mixed: "#a855f7", institutional: "#16a34a" };
const ONSITE = {
  compost: ["#16a34a", "Composting"], biogas: ["#16a34a", "Biogas"], certificate: ["#0d9488", "EBWGR certificate"],
  none: ["#dc2626", "No processing"], not_surveyed: ["#f59e0b", "Not surveyed"],
};
const STREAM_LABEL = { total_kg: "all streams", wet: "wet", dry: "dry", sanitary: "sanitary", special: "special care" };
const WIDTH_STOPS = [[0, "#dc2626"], [4, "#f97316"], [6, "#eab308"], [8, "#84cc16"], [12, "#16a34a"]];

const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}
function eachCoord(geom, fn) { const w = (c) => (typeof c[0] === "number" ? fn(c) : c.forEach(w)); w(geom.coordinates); }
function boundsOf(geoms) { const b = new maplibregl.LngLatBounds(); geoms.forEach((g) => eachCoord(g, (c) => b.extend(c))); return b; }

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.45, "raster-saturation": -0.7 } }],
  },
  center: [77.641, 12.9125], zoom: 14.3,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

map.on("load", async () => {
  try {
    const [sectors, blocks, segments, vehicles] = await Promise.all([
      api(`/api/pilots/${PILOT}/sectors`), api(`/api/pilots/${PILOT}/v2/blocks`), api(`/api/pilots/${PILOT}/v2/segments`), api("/api/reference/vehicles"),
    ]);
    state.sectorsFC = sectors;
    sectors.features.forEach((f) => $("sector").insertAdjacentHTML("beforeend", `<option>${esc(f.properties.name)}</option>`));
    $("vehicle").innerHTML = vehicles.classes.filter((c) => c.tier === "primary")
      .map((c) => `<option value="${c.key}">${esc(c.label)}</option>`).join("");

    map.addSource("blocks", { type: "geojson", data: blocks });
    map.addLayer({ id: "blocks", type: "fill", source: "blocks", paint: { "fill-color": "#94a3b8", "fill-opacity": 0.12 } });
    map.addLayer({ id: "blocks-line", type: "line", source: "blocks", paint: { "line-color": "#64748b", "line-width": 0.8 } });
    map.addSource("segments", { type: "geojson", data: segments });
    map.addLayer({ id: "widths", type: "line", source: "segments", layout: { visibility: "none" },
      paint: { "line-color": ["interpolate", ["linear"], ["get", "width_m"], ...WIDTH_STOPS.flat()], "line-width": 3 } });
    map.addSource("sectors", { type: "geojson", data: sectors });
    map.addLayer({ id: "sectors", type: "line", source: "sectors", paint: { "line-color": "#0f172a", "line-width": 2, "line-dasharray": [3, 2] } });
    map.addSource("members", { type: "geojson", data: empty });
    map.addLayer({ id: "members", type: "fill", source: "members", paint: { "fill-color": "#0f172a", "fill-opacity": 0.55 } });
    map.addSource("points", { type: "geojson", data: empty });
    map.addLayer({ id: "points", type: "circle", source: "points", filter: ["!", ["get", "is_bwg"]], paint: {
      "circle-color": ["match", ["get", "use"], ...Object.entries(USE_COLOURS).flat(), "#64748b"],
      "circle-radius": ["interpolate", ["linear"], ["get", "size"], 0, 3, 50, 6, 200, 10, 600, 15],
      "circle-stroke-color": "#fff", "circle-stroke-width": 1.2, "circle-opacity": 0.9 } });
    map.addLayer({ id: "bwg", type: "circle", source: "points", filter: ["to-boolean", ["get", "is_bwg"]], paint: {
      "circle-color": ["match", ["get", "onsite"], ...Object.entries(ONSITE).flatMap(([k, v]) => [k, v[0]]), "#f59e0b"],
      "circle-radius": 9, "circle-stroke-color": "#111827", "circle-stroke-width": 3 } });
    map.addLayer({ id: "selected", type: "circle", source: "points", filter: ["==", ["get", "id"], ""], paint: {
      "circle-radius": 16, "circle-color": "transparent", "circle-stroke-color": "#000", "circle-stroke-width": 3 } });
    for (const l of ["points", "bwg"]) {
      map.on("click", l, (e) => select(e.features[0].properties.id));
      map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
    }
    map.fitBounds(boundsOf(sectors.features.map((f) => f.geometry)), { padding: 20 });
    renderLegend();
    await generate();
    api(`/api/pilots/${PILOT}/buildings`).then((b) => { state.buildings = new Map(b.features.map((f) => [f.properties.id, f])); });
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  }
});

function queryString() {
  const q = new URLSearchParams({ vehicle: $("vehicle").value, streams: $("collect").value });
  if ($("sector").value) q.set("sector", $("sector").value);
  q.set("max_run_m", $("maxRun").value); q.set("max_buildings", $("maxBldA").value);
  q.set("min_buildings", $("minBld").value); q.set("merge_radius_m", $("mergeR").value);
  return q.toString();
}

async function generate() {
  $("apply").disabled = true;
  $("status").textContent = "Generating points…";
  try {
    const t0 = performance.now();
    state.data = await api(`/api/pilots/${PILOT}/v2/points?${queryString()}`);
    state.byId = new Map(state.data.points.map((p) => [p.id, p]));
    drawPoints();
    renderSummary();
    $("detail").hidden = true;
    $("status").textContent = `${fmt(state.data.summary.points)} street-run points in ${fmt((performance.now() - t0) / 1000, 1)} s. Click a point to see its buildings.`;
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  } finally {
    $("apply").disabled = false;
  }
}

function drawPoints() {
  const key = $("stream").value;
  map.getSource("points").setData({
    type: "FeatureCollection",
    features: state.data.points.map((p) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { id: p.id, use: p.use, is_bwg: !!p.is_bwg, onsite: p.onsite_processing || "", size: key === "total_kg" ? p.total_kg : p.kg[key] },
    })),
  });
}

function renderLegend() {
  $("legend").innerHTML =
    Object.entries(USE_COLOURS).map(([k, c]) => `<div><span class="swatch round" style="background:${c}"></span>${k[0].toUpperCase() + k.slice(1)}</div>`).join("") +
    `<div class="muted small">Point size = estimated kg/day</div>` +
    Object.values(ONSITE).filter((v, i, a) => a.findIndex((x) => x[1] === v[1]) === i).map(([c, l]) => `<div><span class="swatch round ring" style="background:${c}"></span>BWG · ${l}</div>`).join("") +
    `<div class="muted small">Road width: ${WIDTH_STOPS.map(([w, c]) => `<span class="swatch" style="background:${c}"></span>${w}+ m`).join(" ")}</div>`;
}

function renderSummary() {
  const s = state.data.summary;
  if (!s.points) { $("summary").innerHTML = "<tr><td>No points.</td></tr>"; $("bySector").innerHTML = ""; return; }
  $("summary").innerHTML = [
    ["Points (excluding BWG)", fmt(s.points - s.bwg_points)],
    ["Bulk waste generator points", fmt(s.bwg_points)],
    ["By use", Object.entries(s.by_use).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ")],
    ["Buildings per point", `median ${fmt(s.buildings_per_point.median)} · 90th pct ${fmt(s.buildings_per_point.p90)} · max ${fmt(s.buildings_per_point.max)}`],
    ["kg/day per point (est.)", `median ${fmt(s.kg_per_point.median, 1)} · 90th pct ${fmt(s.kg_per_point.p90, 1)} · max ${fmt(s.kg_per_point.max, 1)}`],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  $("bySector").innerHTML = `<tr><th>Sector</th><th>Points</th></tr>` +
    Object.entries(s.by_sector).map(([k, v]) => `<tr class="clickable" data-sector="${esc(k)}"><td>${esc(k)}</td><td>${fmt(v)}</td></tr>`).join("");
  document.querySelectorAll("#bySector tr.clickable").forEach((tr) => tr.addEventListener("click", () => {
    const f = state.sectorsFC.features.find((x) => x.properties.name === tr.dataset.sector);
    if (f) map.fitBounds(boundsOf([f.geometry]), { padding: 30 });
  }));
  const bwgs = state.data.points.filter((p) => p.is_bwg).sort((a, b) => b.total_kg - a.total_kg);
  $("bwgList").innerHTML = bwgs.map((p) => `<div class="result" data-id="${esc(p.id)}"><span class="swatch round" style="background:${ONSITE[p.onsite_processing][0]}"></span><b>${esc(p.label.replace(" (bulk waste generator)", ""))}</b> · ${esc(p.sector)}<br>
    <span class="muted">${esc(ONSITE[p.onsite_processing][1])} · ${fmt(p.total_kg)} kg/day routed · ${fmt(p.wet_kg_excluded)} kg wet excluded</span></div>`).join("") || `<div class="muted small">None.</div>`;
  document.querySelectorAll("#bwgList .result").forEach((el) => el.addEventListener("click", () => select(el.dataset.id, true)));
}

function select(id, fly = false) {
  const p = state.byId.get(id);
  if (!p) return;
  map.setFilter("selected", ["==", ["get", "id"], id]);
  if (fly) map.easeTo({ center: [p.lon, p.lat], zoom: 17 });
  if (state.buildings) {
    map.getSource("members").setData({ type: "FeatureCollection", features: p.building_ids.map((b) => state.buildings.get(b)).filter(Boolean) });
  }
  const key = $("stream").value === "total_kg" ? "wet" : $("stream").value;
  const headline = p.is_bwg
    ? `${esc(p.label)}`
    : `${esc(p.label)}: ${fmt(p.buildings)} ${esc(p.use)}, ${fmt(p.kg[key], 1)} kg ${STREAM_LABEL[key]}`;
  $("detail").hidden = false;
  $("detail").innerHTML = `
    <h2>${headline} <button class="close" id="closeDetail" aria-label="Close">×</button></h2>
    <p class="hint">${esc(p.id)} · ${esc(p.sector)}${p.merged_from ? ` · merged ${p.merged_from.length} small point(s)` : ""}</p>
    <table class="card">
      <tr><td>Use</td><td><span class="swatch round" style="background:${USE_COLOURS[p.use]}"></span>${esc(p.use)}</td></tr>
      <tr><td>Buildings</td><td>${fmt(p.buildings)}</td></tr>
      <tr><td>Estimated kg/day</td><td>wet ${fmt(p.kg.wet, 1)} · dry ${fmt(p.kg.dry, 1)} · sanitary ${fmt(p.kg.sanitary, 1)} · special ${fmt(p.kg.special, 1)}</td></tr>
      ${p.is_bwg ? `<tr><td>Wet waste</td><td>${fmt(p.wet_kg_excluded, 1)} kg/day excluded from routes (r. 6)</td></tr>
        <tr><td>On-site processing</td><td>${esc(ONSITE[p.onsite_processing][1])}</td></tr>` : ""}
      <tr><td>Travel along street</td><td>${fmt(p.span_m)} m${p.run_length_m ? ` of a ${fmt(p.run_length_m)} m run` : ""}</td></tr>
      <tr><td>Narrowest street</td><td>${p.min_width_m == null ? "–" : `${fmt(p.min_width_m, 1)} m (estimated)`}</td></tr>
      <tr><td>Service time</td><td><b>${fmt(p.service_min, 1)} min</b> for ${esc($("vehicle").selectedOptions[0].text)}, ${esc($("collect").selectedOptions[0].text.toLowerCase())}</td></tr>
    </table>
    <p class="muted small">Service time = per building (1–5 min by vehicle class and kg) + travel along the street. Rates are editable assumptions in reference/vehicles.json.</p>`;
  $("closeDetail").addEventListener("click", () => { $("detail").hidden = true; map.setFilter("selected", ["==", ["get", "id"], ""]); map.getSource("members").setData(empty); });
}

$("apply").addEventListener("click", generate);
$("sector").addEventListener("change", generate);
$("vehicle").addEventListener("change", generate);
$("collect").addEventListener("change", generate);
$("stream").addEventListener("change", () => state.data && drawPoints());
$("showBlocks").addEventListener("change", (e) => ["blocks", "blocks-line"].forEach((l) => map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none")));
$("showWidths").addEventListener("change", (e) => map.setLayoutProperty("widths", "visibility", e.target.checked ? "visible" : "none"));
$("showBwg").addEventListener("change", (e) => map.setLayoutProperty("bwg", "visibility", e.target.checked ? "visible" : "none"));
