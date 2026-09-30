// Route Builder: door to door -> transfer station -> MRF, planned per sector.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const empty = { type: "FeatureCollection", features: [] };
const PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#be185d", "#4d7c0f", "#b45309", "#1e40af", "#7c3aed", "#0f766e"];
const state = { parks: [], parkRef: null, parkOverrides: {}, parkExcluded: new Set(), sectors: null, vehicles: [], points: [], placing: null, depot: null, mrf: null, yard: null, stations: [], result: null, selectedVehicle: null };

const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hm = (min) => { if (min == null) return "–"; const m = Math.round(min); return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`; };
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 300)}`);
  return res.json();
}
function eachCoord(geom, fn) { const w = (c) => (typeof c[0] === "number" ? fn(c) : c.forEach(w)); w(geom.coordinates); }
function boundsOf(geoms) { const b = new maplibregl.LngLatBounds(); geoms.forEach((g) => eachCoord(g, (c) => b.extend(c))); return b; }

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.55, "raster-saturation": -0.6 } }],
  },
  center: [77.641, 12.9125], zoom: 14.3,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });

// ---------- Markers ----------

function pinEl(kind, label) {
  const el = document.createElement("div");
  el.className = `map-pin ${kind}`;
  el.textContent = label;
  return el;
}
const markers = { depot: null, mrf: null, yard: null, stations: [] };

function setPlace(kind, lngLat) {
  const labels = { depot: "Start", mrf: "MRF", yard: "Yard" };
  state[kind] = [lngLat.lng, lngLat.lat];
  if (!markers[kind]) {
    markers[kind] = new maplibregl.Marker({ element: pinEl(kind, labels[kind]), draggable: true }).setLngLat(lngLat).addTo(map);
    markers[kind].on("dragend", () => { const p = markers[kind].getLngLat(); state[kind] = [p.lng, p.lat]; placeStates(); clearResult(); });
  } else markers[kind].setLngLat(lngLat);
  placeStates();
  clearResult();
}

function renderStations() {
  markers.stations.forEach((m) => m.remove());
  markers.stations = state.stations.map((s, i) => {
    const load = state.stationLoads?.[i];
    const m = new maplibregl.Marker({ element: pinEl("station", load != null ? `TS${i + 1} · ${fmt(load / 1000, 1)} t` : `TS${i + 1}`), draggable: true }).setLngLat(s).addTo(map);
    m.on("dragend", () => { const p = m.getLngLat(); state.stations[i] = [p.lng, p.lat]; state.stationLoads = null; renderStations(); clearResult(); });
    m.getElement().addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm(`Remove transfer station TS${i + 1}?`)) { state.stations.splice(i, 1); state.stationLoads = null; renderStations(); clearResult(); }
    });
    return m;
  });
  $("stationState").textContent = state.stations.length ? `${state.stations.length} transfer station(s) placed.` : "No transfer stations yet.";
}

function placeStates() {
  const t = (p) => (p ? `${p[1].toFixed(5)}, ${p[0].toFixed(5)}` : "not set");
  $("depotState").textContent = t(state.depot);
  $("mrfState").textContent = t(state.mrf);
  $("yardState").textContent = state.yard ? t(state.yard) : "trucks start at the MRF";
}

document.querySelectorAll("button.place").forEach((b) => b.addEventListener("click", () => {
  const kind = b.dataset.kind;
  state.placing = state.placing === kind ? null : kind;
  document.querySelectorAll("button.place").forEach((x) => x.classList.toggle("active", x.dataset.kind === state.placing));
  map.getCanvas().style.cursor = state.placing ? "crosshair" : "";
  $("placeHint").textContent = state.placing ? "Now click on the map to place it." : "Click a button, then click the map. Drag markers to move them. Click a transfer station to remove it.";
}));

map.on("click", (e) => {
  if (!state.placing) return;
  if (state.placing === "station") { state.stations.push([e.lngLat.lng, e.lngLat.lat]); state.stationLoads = null; renderStations(); clearResult(); }
  else setPlace(state.placing, e.lngLat);
  state.placing = null;
  document.querySelectorAll("button.place").forEach((x) => x.classList.remove("active"));
  map.getCanvas().style.cursor = "";
  $("placeHint").textContent = "Drag markers to move them. Click a transfer station to remove it.";
});

// ---------- Fleet rows ----------

function fleetRow(container, tier, type, count) {
  const opts = state.vehicles.filter((v) => v.tier === tier);
  const chosen = opts.find((o) => o.key === type) || opts[0];
  const row = document.createElement("div");
  row.className = "fleet-row";
  row.innerHTML = `<img alt="" src="${esc(chosen.icon)}" />
    <select>${opts.map((o) => `<option value="${o.key}" ${o.key === chosen.key ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>
    <input type="number" min="0" max="200" value="${count}" title="Number of vehicles" />
    <button class="x" title="Remove">×</button>`;
  const sel = row.querySelector("select");
  sel.addEventListener("change", () => { row.querySelector("img").src = opts.find((o) => o.key === sel.value).icon; clearResult(); });
  row.querySelector("input").addEventListener("change", clearResult);
  row.querySelector(".x").addEventListener("click", () => { row.remove(); clearResult(); });
  container.appendChild(row);
}
const fleetOf = (id) => [...$(id).querySelectorAll(".fleet-row")].map((r) => ({ type: r.querySelector("select").value, count: +r.querySelector("input").value || 0 })).filter((r) => r.count > 0);
$("addPrimary").addEventListener("click", () => fleetRow($("primaryFleet"), "primary", null, 1));
$("addSecondary").addEventListener("click", () => fleetRow($("secondaryFleet"), "secondary", null, 1));

// ---------- Load ----------

map.on("load", async () => {
  try {
    const [sectors, vehicles, compost] = await Promise.all([api(`/api/pilots/${PILOT}/sectors`), api("/api/reference/vehicles"), api("/api/reference/composting")]);
    state.parkRef = compost;
    $("parkMethod").innerHTML = Object.entries(compost.methods).map(([k, m]) => `<option value="${k}" ${k === compost.default_method ? "selected" : ""}>${esc(m.label)} (${m.kg_per_day_per_m2} kg/day per m²)</option>`).join("");
    $("dropoff").value = compost.resident_dropoff_pct.value;
    $("parkRadius").value = compost.catchment_radius_m.value;
    state.sectors = sectors;
    state.vehicles = vehicles.classes;
    $("sector").innerHTML = sectors.features.map((f) => `<option>${esc(f.properties.name)}</option>`).join("");
    const wanted = new URLSearchParams(location.search).get("sector");
    if (wanted && sectors.features.some((f) => f.properties.name === wanted)) $("sector").value = wanted;
    fleetRow($("primaryFleet"), "primary", "e_loader_3w", 4);
    fleetRow($("primaryFleet"), "primary", "mini_tipper", 2);
    fleetRow($("secondaryFleet"), "secondary", "rear_loader_compactor", 1);

    map.addSource("sectors", { type: "geojson", data: sectors });
    map.addLayer({ id: "sector-fill", type: "fill", source: "sectors", paint: { "fill-color": "#0f172a", "fill-opacity": 0 } });
    map.addLayer({ id: "sector-line", type: "line", source: "sectors", paint: { "line-color": "#0f172a", "line-width": 1, "line-dasharray": [3, 2] } });
    map.addSource("parks", { type: "geojson", data: empty });
    map.addLayer({ id: "parks-fill", type: "fill", source: "parks", paint: { "fill-color": "#16a34a", "fill-opacity": ["case", ["get", "off"], 0.08, 0.28] } });
    map.addLayer({ id: "parks-line", type: "line", source: "parks", paint: { "line-color": "#15803d", "line-width": 1.5, "line-opacity": ["case", ["get", "off"], 0.35, 1] } });
    map.on("click", "parks-fill", (e) => {
      const p = state.parks.find((x) => x.id === e.features[0].properties.id);
      if (p && !state.placing) new maplibregl.Popup().setLngLat(e.lngLat).setHTML(parkPopup(p)).addTo(map);
    });
    map.addSource("catch", { type: "geojson", data: empty });
    map.addLayer({ id: "catch", type: "line", source: "catch", paint: { "line-color": "#15803d", "line-width": 1.2, "line-opacity": 0.7, "line-dasharray": [2, 2] } });
    map.addSource("routes", { type: "geojson", data: empty });
    map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": 0.8 } });
    map.addSource("haul", { type: "geojson", data: empty });
    map.addLayer({ id: "haul-casing", type: "line", source: "haul", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#ffffff", "line-width": 10, "line-opacity": 0.9 } });
    map.addLayer({ id: "haul", type: "line", source: "haul", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#7c2d12", "line-width": 6 } });
    map.addSource("points", { type: "geojson", data: empty });
    map.addLayer({ id: "points", type: "circle", source: "points", paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "kg"], 0, 2.5, 100, 5, 500, 9],
      "circle-color": ["coalesce", ["get", "color"], "#64748b"], "circle-stroke-color": "#fff", "circle-stroke-width": 1, "circle-opacity": 0.9 } });
    map.on("mouseenter", "points", (e) => {
      const p = e.features[0].properties;
      popup.setLngLat(e.lngLat).setHTML(`<b>${esc(p.label)}</b><br>${esc(p.use)} · ${fmt(p.buildings)} buildings · ${fmt(p.kg, 1)} kg/day (est.)${p.station ? `<br>→ ${esc(p.station)}` : ""}`).addTo(map);
    });
    map.on("mouseleave", "points", () => popup.remove());
    await selectSector();
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  }
});

let sectorToken = 0;
async function selectSector() {
  const name = $("sector").value;
  const token = ++sectorToken;
  map.setPaintProperty("sector-fill", "fill-opacity", ["case", ["==", ["get", "name"], name], 0.06, 0]);
  map.setPaintProperty("sector-line", "line-width", ["case", ["==", ["get", "name"], name], 3, 1]);
  const f = state.sectors.features.find((x) => x.properties.name === name);
  map.fitBounds(boundsOf([f.geometry]), { padding: 40 });
  clearResult();
  $("sectorInfo").textContent = "Loading collection points…";
  const d = await api(`/api/pilots/${PILOT}/v2/points?sector=${encodeURIComponent(name)}`);
  if (token !== sectorToken) return; // the user picked another sector meanwhile
  state.points = d.points;
  const kg = d.points.reduce((a, p) => a + p.total_kg, 0);
  $("sectorInfo").textContent = `${fmt(d.points.length)} door-to-door collection points (street runs), about ${fmt(kg / 1000, 1)} t/day estimated.`;
  drawPoints();
  await Promise.all([suggest(), loadParks()]);
}

function drawPoints(assign) {
  map.getSource("points").setData({ type: "FeatureCollection", features: state.points.map((p) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    properties: { label: p.label, use: p.use, buildings: p.buildings, kg: p.total_kg, station: assign?.[p.id]?.station || "", color: assign?.[p.id]?.color || null },
  })) });
}

async function suggest() {
  $("stationState").textContent = "Finding transfer stations…";
  const token = sectorToken, name = $("sector").value;
  try {
    const trucks = [...new Set(fleetOf("secondaryFleet").map((r) => r.type))].join(",");
    const d = await api(`/api/pilots/${PILOT}/v2/stations?sector=${encodeURIComponent(name)}&radius_m=${+$("radius").value}&trucks=${trucks}`);
    if (token !== sectorToken || name !== $("sector").value) return; // stale answer for another sector
    const st = d.stations;
    state.stations = st.map((s) => [s.lon, s.lat]);
    state.stationLoads = st.map((s) => s.kg);
    renderStations();
    const t = st.map((s) => s.kg / 1000);
    $("stationState").textContent = `${st.length} transfer station(s) on main roads, ${fmt(Math.min(...t), 1)} to ${fmt(Math.max(...t), 1)} t/day each. ` +
      `Each serves points within ${$("radius").value} m by road and holds up to ${fmt(d.capacity_kg / 1000, 1)} t (${d.capacity_basis}). Drag, add or remove them.`;
  } catch (err) {
    $("stationState").textContent = `Error: ${err.message}`;
  }
  clearResult();
}

$("sector").addEventListener("change", selectSector);
$("suggest").addEventListener("click", suggest);
$("radius").addEventListener("change", clearResult);

// ---------- Optimise ----------

const haulArrows = [];
function clearArrows() { haulArrows.splice(0).forEach((m) => m.remove()); }

function clearResult() {
  state.result = null;
  $("results").hidden = true;
  clearArrows();
  clearParkPins();
  if (map.getSource("routes")) { map.getSource("routes").setData(empty); map.getSource("haul").setData(empty); map.getSource("catch").setData(empty); drawPoints(); }
}

// Arrow and label halfway along each truck route, pointing towards the MRF.
function addHaulArrows(stations, truckTrips) {
  clearArrows();
  stations.filter((x) => x.kg > 0 && x.to_mrf_geometry.length > 1).forEach((x) => {
    const c = x.to_mrf_geometry;
    let total = 0;
    const seg = c.slice(1).map((q, i) => { const d = Math.hypot(q[0] - c[i][0], q[1] - c[i][1]); total += d; return d; });
    let acc = 0, k = 0;
    while (k < seg.length - 1 && acc + seg[k] < total / 2) acc += seg[k++];
    const a = c[k], b = c[k + 1];
    const f = seg[k] ? (total / 2 - acc) / seg[k] : 0;
    const pos = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
    const lat0 = (a[1] * Math.PI) / 180;
    const angle = (Math.atan2(-(b[1] - a[1]), (b[0] - a[0]) * Math.cos(lat0)) * 180) / Math.PI;
    const el = document.createElement("div");
    el.className = "haul-arrow";
    el.innerHTML = `<span class="arrow" style="transform: rotate(${angle}deg)">&#10148;</span><span class="lbl">${esc(x.id)} &rarr; MRF &middot; ${truckTrips[x.id] || 0} truck trip(s)</span>`;
    haulArrows.push(new maplibregl.Marker({ element: el }).setLngLat(pos).addTo(map));
  });
}

$("optimise").addEventListener("click", async () => {
  const missing = [!state.depot && "start point", !state.mrf && "MRF", !state.stations.length && "at least one transfer station"].filter(Boolean);
  const primary = fleetOf("primaryFleet");
  if (!primary.length) missing.push("at least one door-to-door vehicle");
  if (missing.length) { $("status").textContent = `Set the ${missing.join(", ")} first.`; return; }
  const body = {
    sector: $("sector").value, depot: state.depot, mrf: state.mrf, truck_depot: state.yard,
    stations: state.stations, radius_m: +$("radius").value,
    primary_fleet: primary, secondary_fleet: fleetOf("secondaryFleet"),
    streams: [...document.querySelectorAll(".streams input:checked")].map((x) => x.value),
    shift_h: +$("shift").value, unload_min: +$("unload").value, time_limit_s: +$("timeLimit").value,
    park: {
      enabled: $("parkOn").checked, method: $("parkMethod").value, share_mode: $("parkShare").value,
      dropoff_pct: +$("dropoff").value, radius_m: +$("parkRadius").value,
      overrides: state.parkOverrides, excluded: [...state.parkExcluded],
    },
  };
  $("optimise").disabled = true;
  $("status").textContent = "";
  showProgress({ stage: "Starting", progress: 0, elapsed_s: 0, eta_s: null }, body.time_limit_s + 10);
  try {
    const job = await api(`/api/pilots/${PILOT}/v2/plan/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const t0 = performance.now();
    let st;
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      st = await api(`/api/pilots/${PILOT}/v2/plan/jobs/${job.job_id}`);
      showProgress(st, job.expected_s, (performance.now() - t0) / 1000);
      if (st.status !== "running") break;
    }
    if (st.status === "error") throw new Error(st.error);
    state.result = st.result;
    $("status").textContent = `Done in ${fmt(st.elapsed_s)} s.`;
    renderResult();
  } catch (err) {
    $("status").textContent = `Error: ${err.message}`;
  } finally {
    $("optimise").disabled = false;
    $("progress").hidden = true;
    $("mapBusy").hidden = true;
  }
});

function showProgress(st, expected, localElapsed) {
  const elapsed = st.elapsed_s ?? localElapsed ?? 0;
  // Until the optimiser reports enough progress, estimate from the time budget.
  const eta = st.eta_s ?? Math.max(0, Math.round(expected - elapsed));
  const pct = Math.round(100 * (st.status === "done" ? 1 : st.progress || Math.min(0.1, elapsed / expected)));
  const etaText = st.status === "done" ? "finished" : eta > 0 ? `about ${eta >= 60 ? `${Math.floor(eta / 60)} min ${eta % 60} s` : `${eta} s`} left` : "almost done…";
  $("progress").hidden = false;
  $("mapBusy").hidden = false;
  $("progStage").textContent = st.stage + "…";
  $("progPct").textContent = `${pct}%`;
  $("progBar").style.width = `${pct}%`;
  $("progElapsed").textContent = `${Math.round(elapsed)} s elapsed`;
  $("progEta").textContent = etaText;
  $("busyStage").textContent = `${st.stage.replace(/^Optimising routes: /, "")} · ${pct}%`;
  $("busyEta").textContent = etaText;
}

function renderResult() {
  const r = state.result, s = r.summary;
  const colorOf = {};
  r.primary.vehicles.forEach((v, i) => (colorOf[v.id] = PALETTE[i % PALETTE.length]));

  const feats = [], assign = {};
  r.primary.vehicles.forEach((v) => {
    v.trips.forEach((t) => {
      feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: t.geometry }, properties: { vehicle: v.id, trip: t.trip_id, color: colorOf[v.id] } });
      t.point_ids.forEach((pid) => { assign[pid.split("#")[0]] = { station: t.station, color: colorOf[v.id] }; });
    });
    if (v.return_geometry) feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: v.return_geometry }, properties: { vehicle: v.id, trip: "return", color: colorOf[v.id] } });
  });
  map.getSource("routes").setData({ type: "FeatureCollection", features: feats });
  map.getSource("haul").setData({ type: "FeatureCollection", features: r.stations.filter((x) => x.kg > 0).map((x) => ({ type: "Feature", geometry: { type: "LineString", coordinates: x.to_mrf_geometry }, properties: { station: x.id } })) });
  const tripsAt = {};
  r.secondary.trucks.forEach((t) => t.trips.forEach((x) => (tripsAt[x.station] = (tripsAt[x.station] || 0) + 1)));
  if (r.secondary.trucks.length) addHaulArrows(r.stations, tripsAt);
  $("showRoutes").checked = true;
  $("showHaul").checked = true;
  ["haul", "haul-casing", "routes"].forEach((l) => map.setLayoutProperty(l, "visibility", "visible"));
  drawPoints(assign);
  map.setFilter("routes", null);

  $("kpis").innerHTML = [
    ["Time to complete", hm(s.time_to_complete_min), s.within_shift ? "within shift" : `shift is ${hm(s.shift_min)}`, !s.within_shift],
    ["Door-to-door trips", fmt(r.primary.trips), `${r.primary.vehicles.filter((v) => v.trips.length).length} vehicles · ${fmt(r.primary.km, 1)} km`, false],
    ["Truck trips to MRF", fmt(r.secondary.trips), r.secondary.trucks.length ? `${hm(r.secondary.time_min)} of truck time` : "no trucks added", false],
    ["Waste collected", `${fmt(s.kg_collected / 1000, 2)} t`, `${fmt(s.points_served)} of ${fmt(s.points)} points`, false],
    ["Wet waste kept from MRF", `${fmt(s.composting.wet_diverted_kg / 1000, 2)} t/day`, `parks ${fmt(s.composting.wet_to_parks_kg / 1000, 2)} t · in buildings ${fmt(s.composting.home_composted_kg / 1000, 2)} t`, false],
    ["Compost produced", `${fmt(s.composting.compost_kg / 1000, 2)} t/day`, `in ${fmt(s.composting.park_sites)} park site(s)`, false],
  ].map(([k, v, sub, bad]) => `<div class="kpi ${bad ? "bad" : ""}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`).join("");

  const warn = [];
  if (!s.within_shift) warn.push(`The work takes ${hm(s.time_to_complete_min)}, longer than the ${hm(s.shift_min)} shift. Add vehicles or plan a second shift.`);
  if (s.vehicles_over_shift.length) warn.push(`Over shift: ${s.vehicles_over_shift.map(esc).join(", ")}.`);
  if (s.uncollected_kg > 0) warn.push(`${fmt(s.uncollected_kg)} kg/day at ${s.uncollected_points.length} point(s) could not be collected (street too narrow for every vehicle, or not enough capacity).`);
  if (!r.secondary.trucks.length) warn.push("No trucks added: waste stays at the transfer stations.");
  if (s.bwg_wet_excluded_kg) warn.push(`${fmt(s.bwg_wet_excluded_kg)} kg/day of wet waste from bulk waste generators is not collected: they process it on site or hold EBWGR certificates (SWM Rules 2026, r. 6).`);
  $("warnings").innerHTML = warn.map((w) => `<p class="warn">${w}</p>`).join("");

  $("vehTable").innerHTML = `<tr><th></th><th>Vehicle</th><th>Trips</th><th>km</th><th>Time</th></tr>` + r.primary.vehicles.map((v) =>
    `<tr class="clickable ${v.within_shift ? "" : "over"}" data-v="${esc(v.id)}"><td><span class="swatch" style="background:${colorOf[v.id]}"></span></td><td>${esc(v.id)}</td><td>${v.trips.length}</td><td>${fmt(v.km, 1)}</td><td>${hm(v.total_min)}</td></tr>`).join("");
  document.querySelectorAll("#vehTable tr.clickable").forEach((tr) => {
    tr.addEventListener("mouseenter", () => map.setFilter("routes", ["==", ["get", "vehicle"], tr.dataset.v]));
    tr.addEventListener("mouseleave", () => map.setFilter("routes", state.selectedVehicle ? ["==", ["get", "vehicle"], state.selectedVehicle] : null));
    tr.addEventListener("click", () => showTrips(tr.dataset.v));
  });
  $("tripDetail").innerHTML = "";
  state.selectedVehicle = null;

  $("truckTable").innerHTML = r.secondary.trucks.length ? `<tr><th>Truck</th><th>Trips</th><th>km</th><th>Time</th></tr>` + r.secondary.trucks.map((t) =>
    `<tr class="clickable" data-st="${esc([...new Set(t.trips.map((x) => x.station))].join(","))}"><td>${esc(t.id)}</td><td>${t.trips.length}</td><td>${fmt(t.km, 1)}</td><td>${hm(t.total_min)}</td></tr>`).join("") : `<tr><td class="muted">No trucks.</td></tr>`;
  document.querySelectorAll("#truckTable tr.clickable").forEach((tr) => {
    const sts = tr.dataset.st.split(",");
    const f = ["in", ["get", "station"], ["literal", sts]];
    tr.addEventListener("mouseenter", () => { map.setFilter("haul", f); map.setFilter("haul-casing", f); });
    tr.addEventListener("mouseleave", () => { map.setFilter("haul", null); map.setFilter("haul-casing", null); });
  });
  const truckTrips = {};
  r.secondary.trucks.forEach((t) => t.trips.forEach((x) => (truckTrips[x.station] = (truckTrips[x.station] || 0) + 1)));
  $("stationTable").innerHTML = `<tr><th>Station</th><th>Points</th><th>t/day</th><th>Of capacity</th><th>Trips in</th><th>Truck trips</th><th>To MRF</th></tr>` + r.stations.map((x) =>
    `<tr><td>${esc(x.id)}${x.on_main_road ? "" : ' <span class="muted small">(side road)</span>'}</td><td>${fmt(x.points)}</td><td>${fmt(x.kg / 1000, 2)}</td><td class="${x.kg > r.station_capacity_kg ? "over" : ""}">${fmt(100 * x.kg / r.station_capacity_kg)}%</td><td>${fmt(x.primary_trips)}</td><td>${fmt(truckTrips[x.id] || 0)}</td><td>${fmt(x.to_mrf_km, 1)} km</td></tr>`).join("");
  $("assumptions").innerHTML = r.assumptions.map((a) => `<li>${esc(a)}</li>`).join("");
  renderParkResults(r);
  $("results").hidden = false;
  $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showTrips(id) {
  const v = state.result.primary.vehicles.find((x) => x.id === id);
  state.selectedVehicle = state.selectedVehicle === id ? null : id;
  map.setFilter("routes", state.selectedVehicle ? ["==", ["get", "vehicle"], id] : null);
  if (!state.selectedVehicle) { $("tripDetail").innerHTML = ""; return; }
  const clock = (m) => { const t = Math.round(6 * 60 + m); return `${String(Math.floor(t / 60) % 24).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`; };
  $("tripDetail").innerHTML = `<div class="trip-card"><b>${esc(v.id)}</b> <span class="muted small">(clock times assume a 06:00 start)</span>
    <table class="results-table"><tr><th>Trip</th><th>Unloads at</th><th>Points</th><th>kg</th><th>Full</th><th>Time</th></tr>
    ${v.trips.map((t, i) => `<tr><td>${i + 1}</td><td>${t.park_drops?.length ? `${t.park_drops.map((d) => `${esc(parkName(d.park_id))} (${fmt(d.kg)} kg wet)`).join(", ")}, then ` : ""}${esc(t.station)}</td><td>${new Set(t.point_ids.map((p) => p.split("#")[0])).size}</td><td>${fmt(t.kg)}</td><td>${t.fill_pct}%</td><td>${clock(t.start_min)}–${clock(t.end_min)}</td></tr>`).join("")}
    </table><p class="muted small">Back at the start point by ${clock(v.total_min)}.</p></div>`;
  const b = new maplibregl.LngLatBounds();
  v.trips.forEach((t) => t.geometry.forEach((c) => b.extend(c)));
  if (!b.isEmpty()) map.fitBounds(b, { padding: 40 });
}

$("showRoutes").addEventListener("change", (e) => map.setLayoutProperty("routes", "visibility", e.target.checked ? "visible" : "none"));
$("showHaul").addEventListener("change", (e) => {
  ["haul", "haul-casing"].forEach((l) => map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
  haulArrows.forEach((m) => (m.getElement().style.display = e.target.checked ? "" : "none"));
});

// ---------- Park composting ----------

const parkName = (id) => state.parks.find((p) => p.id === id)?.label || id;
const parkPins = [];
function clearParkPins() { parkPins.splice(0).forEach((m) => m.remove()); }

function siteCalc(p) {
  const ref = state.parkRef;
  const m = ref.methods[$("parkMethod").value];
  const pct = Math.min(ref.share.max_pct, Math.max(ref.share.min_pct, state.parkOverrides[p.id] ?? p.share_auto_pct));
  const site = (p.area_m2 * pct) / 100;
  const usable = site >= ref.min_site_m2.value && !state.parkExcluded.has(p.id);
  return { pct, site, cap: usable ? Math.min(site * m.kg_per_day_per_m2, state.parkCap) : 0, usable, capped: site * m.kg_per_day_per_m2 > state.parkCap };
}

async function loadParks() {
  const name = $("sector").value;
  try {
    const d = await api(`/api/pilots/${PILOT}/v2/parks?sector=${encodeURIComponent(name)}&method=${$("parkMethod").value}&share_mode=${$("parkShare").value}`);
    if (name !== $("sector").value) return;
    state.parks = d.sites;
    state.parkCap = d.site_cap_kg;
    state.parkCapRule = d.cap_rule;
    state.parkOverrides = {};
    state.parkExcluded = new Set();
    renderParks();
  } catch (err) {
    $("parkInfo").textContent = `Error: ${err.message}`;
  }
}

function renderParks() {
  const on = $("parkOn").checked;
  $("parkOpts").hidden = !on;
  map.getSource("parks").setData({ type: "FeatureCollection", features: on ? state.parks.map((p) => ({ type: "Feature", geometry: p.geometry, properties: { id: p.id, off: !siteCalc(p).usable } })) : [] });
  const calcs = state.parks.map(siteCalc);
  const cap = calcs.reduce((a, c) => a + c.cap, 0);
  $("parkInfo").textContent = state.parks.length
    ? `${state.parks.length} public park(s) in this sector. Composting sites ${fmt(calcs.filter((c) => c.usable).reduce((a, c) => a + c.site, 0))} m² in total, able to take up to ${fmt(cap / 1000, 1)} t/day of wet waste. Each site is capped at ${fmt(state.parkCap / 1000)} t/day (${state.parkCapRule}, no buffer zone needed).`
    : "No public parks mapped in this sector.";
  $("parkTable").innerHTML = `<tr><th></th><th>Park</th><th>Area m²</th><th>Share %</th><th>Site m²</th><th>t/day</th></tr>` + state.parks.map((p, i) => {
    const c = calcs[i];
    return `<tr data-id="${esc(p.id)}"><td><input type="checkbox" class="pk-on" ${state.parkExcluded.has(p.id) ? "" : "checked"} title="Use this park" /></td>
      <td>${esc(p.label)}</td><td>${fmt(p.area_m2)}</td>
      <td><input type="number" class="pk-pct" min="1" max="5" step="0.5" value="${c.pct}" style="width:56px" /></td>
      <td>${fmt(c.site)}</td><td>${c.usable ? fmt(c.cap / 1000, 2) + (c.capped ? " (cap)" : "") : '<span class="muted">–</span>'}</td></tr>`;
  }).join("");
  document.querySelectorAll("#parkTable tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    tr.querySelector(".pk-pct").addEventListener("change", (e) => { state.parkOverrides[id] = +e.target.value; renderParks(); clearResult(); });
    tr.querySelector(".pk-on").addEventListener("change", (e) => { e.target.checked ? state.parkExcluded.delete(id) : state.parkExcluded.add(id); renderParks(); clearResult(); });
  });
}

function parkPopup(p) {
  const c = siteCalc(p);
  const res = state.result?.parks?.find((x) => x.id === p.id);
  return `<b>${esc(p.label)}</b><br>${fmt(p.area_m2)} m² park · ${fmt(c.pct, 1)}% for composting = ${fmt(c.site)} m²<br>
    Can take ${fmt(c.cap)} kg/day of wet waste${c.capped ? " (capped)" : ""}` +
    (res ? `<br><b>Receives ${fmt(res.received_kg)} kg/day</b> (${res.use_pct}% full): residents bring ${fmt(res.dropoff_kg)} kg, vehicles ${fmt(res.vehicle_kg)} kg<br>Compost about ${fmt(res.compost_kg)} kg/day, for the park's own plants and lawns` : "");
}

function renderParkResults(r) {
  const s = r.summary.composting;
  const parks = (r.parks || []).filter((p) => p.usable);
  clearParkPins();
  const site = Object.fromEntries(parks.map((p) => [p.id, [p.lon, p.lat]]));
  map.getSource("catch").setData({ type: "FeatureCollection", features: (r.catchment || []).filter((c) => site[c.park_id]).map((c) => ({ type: "Feature", geometry: { type: "LineString", coordinates: [c.from, site[c.park_id]] }, properties: {} })) });
  parks.filter((p) => p.received_kg > 0).forEach((p) => {
    const el = pinEl("park", `${p.label.length > 18 ? p.label.slice(0, 17) + "…" : p.label} · ${fmt(p.received_kg / 1000, 2)} t`);
    el.addEventListener("click", (e) => { e.stopPropagation(); new maplibregl.Popup().setLngLat([p.lon, p.lat]).setHTML(parkPopup(p)).addTo(map); });
    parkPins.push(new maplibregl.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
  });
  $("showCatch").checked = true;
  map.setLayoutProperty("catch", "visibility", "visible");
  if (!s.parks_enabled) {
    $("parkResults").innerHTML = s.home_composted_kg > 0 ? `<h3>Composting</h3><p class="small">${fmt(s.home_composted_kg)} kg/day of wet waste is composted inside buildings (from the survey) and not collected.</p>` : "";
    return;
  }
  const w = s.without_parks;
  const truckLine = w ? `<p class="small">Waste reaching the MRF falls from ${fmt(w.kg_to_mrf / 1000, 2)} t to ${fmt((w.kg_to_mrf - s.wet_to_parks_kg) / 1000, 2)} t a day.
    Truck trips: ${fmt(r.secondary.trips)} (without parks ${fmt(w.truck_trips)}); truck time ${hm(r.secondary.time_min)} (without parks ${hm(w.truck_min)}).</p>` : "";
  $("parkResults").innerHTML = `<h3>Park composting</h3>
    <p class="small">${fmt(s.wet_to_parks_kg / 1000, 2)} t/day of wet waste goes to ${parks.filter((p) => p.received_kg > 0).length} park(s): residents bring ${fmt(s.resident_dropoff_kg)} kg and vehicles ${fmt(s.vehicle_to_parks_kg)} kg.
    ${s.home_composted_kg > 0 ? `A further ${fmt(s.home_composted_kg)} kg/day is composted inside buildings.` : ""} About ${fmt(s.compost_kg)} kg/day of compost is produced.</p>${truckLine}
    <table class="results-table"><tr><th>Park</th><th>Site m²</th><th>Receives kg/day</th><th>Full</th><th>Residents</th><th>Compost kg/day</th></tr>
    ${parks.map((p) => `<tr><td>${esc(p.label)}</td><td>${fmt(p.site_m2)}</td><td>${fmt(p.received_kg)}</td><td>${p.use_pct}%</td><td>${fmt(p.dropoff_kg)}</td><td>${fmt(p.compost_kg)}</td></tr>`).join("")}</table>`;
}

$("parkOn").addEventListener("change", () => { renderParks(); clearResult(); });
$("parkMethod").addEventListener("change", () => { renderParks(); clearResult(); });
$("parkShare").addEventListener("change", async () => { await loadParks(); clearResult(); });
$("dropoff").addEventListener("change", clearResult);
$("parkRadius").addEventListener("change", clearResult);
$("showCatch").addEventListener("change", (e) => map.setLayoutProperty("catch", "visibility", e.target.checked ? "visible" : "none"));
