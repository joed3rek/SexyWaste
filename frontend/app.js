// Collection Route Planner frontend. Talks to the FastAPI backend under /api.

const $ = (id) => document.getElementById(id);
const state = { areas: {}, area: null, scenario: null, result: null, show: "optimised", depotMarker: null, facilityMarker: null };
const empty = { type: "FeatureCollection", features: [] };

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.55 } }],
  },
  center: [77.605, 12.9857],
  zoom: 14,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

function setStatus(msg) { $("status").textContent = msg; }

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

map.on("load", async () => {
  map.addSource("network", { type: "geojson", data: empty });
  map.addSource("routes", { type: "geojson", data: empty });
  map.addSource("points", { type: "geojson", data: empty });
  map.addLayer({ id: "network", type: "line", source: "network", paint: { "line-color": "#94a3b8", "line-width": 1 } });
  map.addLayer({
    id: "routes", type: "line", source: "routes",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: {
      "line-color": ["get", "colour"],
      "line-width": ["case", ["boolean", ["feature-state", "highlight"], false], 7, 4],
      "line-opacity": 0.85,
    },
  });
  map.addLayer({
    id: "points", type: "circle", source: "points",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "kg"], 0, 3, 300, 9],
      "circle-stroke-opacity": 1,
      "circle-color": ["coalesce", ["get", "colour"], "#64748b"],
      "circle-stroke-color": ["case", ["get", "dropped"], "#dc2626", ["==", ["get", "kind"], "bwg"], "#111827", "#ffffff"],
      "circle-stroke-width": ["case", ["get", "dropped"], 3, 1.5],
    },
  });
  map.on("click", "points", (e) => {
    const p = e.features[0].properties;
    new maplibregl.Popup().setLngLat(e.lngLat)
      .setHTML(`<b>${p.id}</b> · ${p.label}${p.kind === "bwg" ? " (bulk waste generator)" : ""}<br>${p.kg} kg/day ${$("stream").value} (estimate)${p.vehicle ? `<br>Vehicle ${p.vehicle}, stop ${p.order}` : ""}${p.dropped ? "<br><b>Not served</b>" : ""}`)
      .addTo(map);
  });
  map.on("mouseenter", "points", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "points", () => (map.getCanvas().style.cursor = ""));

  const areas = await api("/api/areas");
  areas.forEach((a) => (state.areas[a.key] = a));
  $("area").innerHTML = areas.map((a) => `<option value="${a.key}">${a.name}</option>`).join("");
  const params = new URLSearchParams(location.search);
  const start = params.get("area") && state.areas[params.get("area")] ? params.get("area") : (areas.find((a) => a.pilot) || areas[0]).key;
  $("area").value = start;
  if (params.get("source")) $("source").value = params.get("source");
  await loadArea(start, params.get("sector"));
});

async function loadArea(key, sector = null) {
  state.area = key;
  const area = state.areas[key];
  $("pilotControls").hidden = !area.pilot;
  $("sectorSel").innerHTML = (area.sectors || []).map((s) => `<option>${s}</option>`).join("");
  if (sector) $("sectorSel").value = sector;
  if (!area.pilot) $("source").value = "synthetic";
  toggleSourceControls();
  setStatus("Loading road network (first load downloads from OpenStreetMap)…");
  const network = await api(`/api/areas/${key}/network`);
  map.getSource("network").setData(network);
  const b = new maplibregl.LngLatBounds();
  network.features.forEach((f) => f.geometry.coordinates.forEach((c) => b.extend(c)));
  map.fitBounds(b, { padding: 30 });
  await newScenario();
}

async function newScenario() {
  setStatus("Generating collection points…");
  const q = $("source").value === "buildings"
    ? `source=buildings&sector=${encodeURIComponent($("sectorSel").value)}`
    : `n_points=${$("nPoints").value}&seed=${$("seed").value}`;
  state.scenario = await api(`/api/areas/${state.area}/scenario?${q}`);
  state.result = null;
  $("results").hidden = true;
  map.getSource("routes").setData(empty);
  placeMarkers();
  renderPoints();
  setStatus(`${state.scenario.points.length} points loaded. ${state.scenario.note}`);
}

function makeMarker(cls, letter, lnglat, key) {
  const el = document.createElement("div");
  el.className = `marker ${cls}`;
  el.textContent = letter;
  el.title = cls === "depot" ? "Vehicle depot" : "Unloading facility (MRF / transfer station)";
  const m = new maplibregl.Marker({ element: el, draggable: true }).setLngLat(lnglat).addTo(map);
  m.on("dragend", () => { const ll = m.getLngLat(); state.scenario[key] = [ll.lng, ll.lat]; });
  return m;
}

function placeMarkers() {
  state.depotMarker?.remove();
  state.facilityMarker?.remove();
  state.depotMarker = makeMarker("depot", "D", state.scenario.depot, "depot");
  state.facilityMarker = makeMarker("facility", "F", state.scenario.facility, "facility");
}

function renderPoints() {
  const stream = $("stream").value;
  const plan = state.result?.[state.show];
  const assigned = {};
  plan?.vehicles.forEach((v) => v.stops.forEach((id, i) => (assigned[id] = { vehicle: v.vehicle, colour: v.colour, order: i + 1 })));
  const dropped = new Set(plan?.dropped ?? []);
  map.getSource("points").setData({
    type: "FeatureCollection",
    features: state.scenario.points.map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { id: p.id, label: p.label || p.id, kind: p.kind || "point", kg: p.demand_kg[stream], dropped: dropped.has(p.id), ...(assigned[p.id] ?? {}) },
    })),
  });
}

function renderRoutes() {
  const plan = state.result?.[state.show];
  map.getSource("routes").setData({
    type: "FeatureCollection",
    features: (plan?.vehicles ?? []).map((v) => ({ type: "Feature", id: v.vehicle, geometry: v.geometry, properties: { colour: v.colour } })),
  });
  renderPoints();
  renderTables();
}

function delta(opt, base, lowerIsBetter = true) {
  if (opt == null || base == null || base === 0) return "";
  const pct = ((opt - base) / base) * 100;
  const good = lowerIsBetter ? pct < 0 : pct > 0;
  return `<span class="${Math.abs(pct) < 0.5 ? "" : good ? "good" : "bad"}">${pct > 0 ? "+" : ""}${pct.toFixed(1)}%</span>`;
}

function renderTables() {
  const o = state.result.optimised?.totals;
  const b = state.result.baseline.totals;
  const rows = [
    ["Vehicles used", "vehicles_used", true],
    ["Distance (km)", "distance_km", true],
    ["Total time (min)", "total_min", true],
    ["Points served", "points_served", false],
    ["Collected (kg)", "collected_kg", false],
  ];
  $("compare").innerHTML =
    `<tr><th></th><th>Baseline</th><th>Optimised</th><th>Change</th></tr>` +
    rows.map(([label, k, low]) => `<tr><td>${label}</td><td>${b[k]}</td><td>${o?.[k] ?? "–"}</td><td>${delta(o?.[k], b[k], low)}</td></tr>`).join("");

  const plan = state.result[state.show];
  if (!plan) { $("vehicleTable").innerHTML = "<tr><td>The solver found no solution.</td></tr>"; return; }
  const cap = Number($("capacity").value);
  $("vehicleTable").innerHTML =
    `<tr><th>${plan.method}</th><th>Stops</th><th>Load</th><th>km</th><th>min</th></tr>` +
    plan.vehicles.map((v) =>
      `<tr class="clickable" data-v="${v.vehicle}"><td><span class="swatch" style="background:${v.colour}"></span>Vehicle ${v.vehicle}</td>` +
      `<td>${v.stops.length}</td><td>${Math.round((v.load_kg / cap) * 100)}%</td><td>${v.distance_km}</td><td>${v.total_min}</td></tr>`).join("");
  $("dropped").textContent = plan.dropped.length
    ? `${plan.dropped.length} point(s) not served within fleet capacity and shift length: ${plan.dropped.join(", ")}. Add vehicles, raise capacity or extend the shift.`
    : "";
  document.querySelectorAll("#vehicleTable tr.clickable").forEach((tr) => {
    tr.addEventListener("mouseenter", () => map.setFeatureState({ source: "routes", id: Number(tr.dataset.v) }, { highlight: true }));
    tr.addEventListener("mouseleave", () => map.setFeatureState({ source: "routes", id: Number(tr.dataset.v) }, { highlight: false }));
  });
}

async function solve() {
  $("solve").disabled = true;
  setStatus("Optimising routes…");
  try {
    state.result = await api(`/api/areas/${state.area}/solve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        depot: state.scenario.depot,
        facility: state.scenario.facility,
        points: state.scenario.points,
        stream: $("stream").value,
        num_vehicles: Number($("vehicles").value),
        capacity_kg: Number($("capacity").value),
        shift_min: Number($("shift").value),
        time_limit_s: Number($("timeLimit").value),
      }),
    });
    $("results").hidden = false;
    renderRoutes();
    setStatus("Done. Hover a vehicle row to highlight its route.");
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  } finally {
    $("solve").disabled = false;
  }
}

$("area").addEventListener("change", (e) => loadArea(e.target.value).catch((err) => setStatus(`Error: ${err.message}`)));
$("newScenario").addEventListener("click", () => newScenario().catch((err) => setStatus(`Error: ${err.message}`)));
$("solve").addEventListener("click", solve);
$("stream").addEventListener("change", () => { state.result = null; $("results").hidden = true; map.getSource("routes").setData(empty); renderPoints(); });
document.querySelectorAll(".toggle button").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".toggle button").forEach((b) => b.classList.toggle("active", b === btn));
    state.show = btn.dataset.show;
    if (state.result) renderRoutes();
  })
);

function toggleSourceControls() {
  const buildings = $("source").value === "buildings" && !$("pilotControls").hidden;
  $("syntheticControls").hidden = buildings;
  $("sectorSel").disabled = !buildings;
}
$("source").addEventListener("change", () => { toggleSourceControls(); newScenario().catch((err) => setStatus(`Error: ${err.message}`)); });
$("sectorSel").addEventListener("change", () => newScenario().catch((err) => setStatus(`Error: ${err.message}`)));
