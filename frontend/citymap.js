// One shared city map: the base every map page uses, and the layer groups any page can switch on.
// Load after ui.js. Pages add their own working layers on top; the shared layers read the same domain
// APIs as everything else (sectors, GVPs, streets, bins, facilities, service demands), so a map never
// keeps its own copy of the city.

const CITY_CENTRE = [77.641, 12.9125];
const OSM_TILES = { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" };

// The base map: grey OSM tiles under the app's layers. opts: container, zoom, center, tileOpacity,
// tileSaturation, controls ("top-right" | "bottom-right"), compass, pitch.
function cityMap(opts = {}) {
  const map = new maplibregl.Map({
    container: opts.container || "map",
    style: {
      version: 8,
      sources: { osm: OSM_TILES },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": "#f4f5f6" } },
        { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": opts.tileOpacity ?? 0.55, "raster-saturation": opts.tileSaturation ?? -1, "raster-contrast": -0.1 } },
      ],
    },
    center: opts.center || CITY_CENTRE,
    zoom: opts.zoom ?? 14.5,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: opts.compass ?? true, visualizePitch: !!opts.pitch }), opts.controls || "top-right");
  return map;
}

const CITY_EMPTY = { type: "FeatureCollection", features: [] };
const cityToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const cityFc = (features) => ({ type: "FeatureCollection", features });
const cityPt = (lon, lat, props) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: props });

// Layer groups (the brief's base, waste intelligence, cleanliness, collection, resources, processing).
// Each layer: id, label, group, load(pilot) -> GeoJSON, and the MapLibre layers that draw it.
const CITY_LAYERS = [
  { id: "sectors", group: "Base", label: "Sector boundaries", on: true,
    load: (pilot) => apiFetch(`/api/pilots/${pilot}/sectors`),
    draw: [{ type: "line", paint: { "line-color": "#18181b", "line-width": 1.5, "line-dasharray": [3, 2] } }] },
  { id: "streets", group: "Cleanliness", label: "Street cleaning classes",
    load: async (pilot) => cityFc((await apiFetch(`/api/pilots/${pilot}/cleancity/streets`)).streets.map((s) => ({ type: "Feature", geometry: { type: "LineString", coordinates: s.coords }, properties: { class: s.class, name: s.name, per_week: s.cleanings_per_week } }))),
    draw: [{ type: "line", paint: { "line-width": 2.5, "line-color": ["match", ["get", "class"], "primary", "#b91c1c", "commercial", "#d97706", "market", "#7c3aed", "residential", "#2563eb", "#9ca3af"] } }],
    popup: (p) => `${p.name || "Unnamed street"}<br><span class="muted small">${String(p.class).replace("_", " ")}, swept ${p.per_week}× a week</span>` },
  { id: "bins", group: "Cleanliness", label: "Public bins",
    load: async (pilot) => cityFc((await apiFetch(`/api/pilots/${pilot}/cleancity/bins`)).bins.map((b) => cityPt(b.lon, b.lat, { code: b.bin_code, status: b.status, due: b.needs_collection }))),
    draw: [{ type: "circle", paint: { "circle-radius": 5, "circle-color": ["case", ["get", "due"], "#ea580c", "#16a34a"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }],
    popup: (p) => `Bin ${p.code}<br><span class="muted small">${String(p.status).replace("_", " ")}${p.due ? ", needs collection" : ""}</span>` },
  { id: "gvps", group: "Waste intelligence", label: "Garbage vulnerable points", on: true,
    load: (pilot) => apiFetch(`/api/pilots/${pilot}/gvps.geojson`),
    draw: [{ type: "circle", paint: { "circle-radius": ["match", ["get", "severity"], "critical", 9, "high", 7.5, "medium", 6, 5], "circle-color": ["match", ["get", "status"], "cleared", "#16a34a", "monitoring", "#16a34a", "#b91c1c"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }],
    popup: (p) => `GVP${p.road_name ? `, ${p.road_name}` : ""}<br><span class="muted small">${p.severity}, ${p.status}${p.recurrences ? `, came back ${p.recurrences}×` : ""}</span>` },
  { id: "demands", group: "Collection", label: "Today's collection demands",
    load: async (pilot) => {
      const d = await apiFetch(`/api/pilots/${pilot}/service-demands?date=${cityToday()}&kind=collection`);
      const by = new Map();
      d.demands.forEach((x) => {
        if (x.detail.lon == null) return;
        const k = `${x.source_type}:${x.source_id}`;
        const f = by.get(k) || cityPt(x.detail.lon, x.detail.lat, { label: x.detail.label || k, kg: 0, status: x.status, source: x.source_type });
        f.properties.kg += x.quantity_kg || 0;
        by.set(k, f);
      });
      return cityFc([...by.values()]);
    },
    draw: [{ type: "circle", paint: { "circle-radius": ["interpolate", ["linear"], ["get", "kg"], 0, 3, 200, 9], "circle-color": ["match", ["get", "status"], "done", "#16a34a", "missed", "#b91c1c", "planned", "#2563eb", "#71717a"], "circle-opacity": 0.8 } }],
    popup: (p) => `${p.label}<br><span class="muted small">${Math.round(p.kg)} kg, ${p.status}</span>` },
  { id: "facilities", group: "Resources and processing", label: "Depots, transfer stations, MRFs", on: true,
    load: async (pilot) => cityFc((await apiFetch(`/api/pilots/${pilot}/facilities`)).facilities.map((f) => cityPt(f.lon, f.lat, { name: f.name, kind: f.kind, cap: f.capacity_t_day }))),
    draw: [{ type: "circle", paint: { "circle-radius": 8, "circle-color": ["match", ["get", "kind"], "mrf", "#7c2d12", "transfer_station", "#f59e0b", "depot", "#18181b", "#52525b"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } }],
    popup: (p) => `${p.name}<br><span class="muted small">${String(p.kind).replace("_", " ")}${p.cap ? `, ${p.cap} t/day` : ""}</span>` },
];

// Add shared layers to a map (all of them, or the ids given). Returns {setVisible(id, on), reload(id)}.
function addCityLayers(map, pilot, ids) {
  const chosen = CITY_LAYERS.filter((l) => !ids || ids.includes(l.id));
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
  const loaded = new Set();
  async function load(l) {
    try {
      map.getSource(`city-${l.id}`).setData(await l.load(pilot));
      loaded.add(l.id);
    } catch { /* the layer stays empty; the page still works */ }
  }
  chosen.forEach((l) => {
    map.addSource(`city-${l.id}`, { type: "geojson", data: CITY_EMPTY });
    l.draw.forEach((d, i) => {
      const id = `city-${l.id}-${i}`;
      map.addLayer({ id, source: `city-${l.id}`, type: d.type, paint: d.paint, ...(d.layout ? { layout: { ...d.layout, visibility: l.on ? "visible" : "none" } } : { layout: { visibility: l.on ? "visible" : "none" } }) });
      if (l.popup) {
        map.on("mousemove", id, (e) => { map.getCanvas().style.cursor = "pointer"; popup.setLngLat(e.lngLat).setHTML(l.popup(e.features[0].properties)).addTo(map); });
        map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
      }
    });
    if (l.on) load(l);
  });
  return {
    layers: chosen,
    setVisible(id, on) {
      const l = chosen.find((x) => x.id === id);
      l.draw.forEach((_, i) => map.setLayoutProperty(`city-${id}-${i}`, "visibility", on ? "visible" : "none"));
      if (on && !loaded.has(id)) load(l);
    },
    reload: (id) => load(chosen.find((x) => x.id === id)),
  };
}

// A panel of checkboxes, by group, to switch shared layers on and off.
function cityLayerPanel(el, control) {
  const groups = [...new Set(control.layers.map((l) => l.group))];
  el.innerHTML = groups.map((g) => `<fieldset class="city-layers"><legend>${g}</legend>${control.layers.filter((l) => l.group === g).map((l) =>
    `<label class="check"><input type="checkbox" data-layer="${l.id}" ${l.on ? "checked" : ""} /> ${l.label}</label>`).join("")}</fieldset>`).join("");
  el.querySelectorAll("[data-layer]").forEach((c) => c.addEventListener("change", () => control.setVisible(c.dataset.layer, c.checked)));
}
