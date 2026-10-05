// Clean City: street classes and cleaning frequency, public bins, GVP clearing and the cleaning workforce.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const SESSION = getSession();
const CLASS_COLOUR = { primary: "#b91c1c", commercial: "#d97706", market: "#7c3aed", residential: "#2563eb", low_intensity: "#9ca3af" };
const BIN_COLOUR = { normal: "#16a34a", near_capacity: "#f59e0b", full: "#ea580c", overflowing: "#b91c1c", damaged: "#6b7280", missing: "#111827" };
const empty = { type: "FeatureCollection", features: [] };

let CFG = null, STREETS = [], BINS = [], adding = false, selected = null;
const isPlanner = () => CFG && CFG.planners.includes(SESSION?.role);

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f4f5f6" } },
      { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.5, "raster-saturation": -1, "raster-contrast": -0.1 } },
    ],
  },
  center: [77.641, 12.9125], zoom: 14.5,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");

function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => ($("toast").hidden = true), 3500);
}

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

// ---------- Loading ----------

async function loadSector() {
  const sector = $("sector").value;
  $("street").innerHTML = `<p class="hint">Tap a street on the map to see and change its cleaning plan.</p>`;
  selected = null;
  const [st, bins, rec, wl, tasks] = await Promise.all([
    apiFetch(`/api/pilots/${PILOT}/cleancity/streets?sector=${encodeURIComponent(sector)}`),
    apiFetch(`/api/pilots/${PILOT}/cleancity/bins?sector=${encodeURIComponent(sector)}`),
    apiFetch(`/api/pilots/${PILOT}/cleancity/bins/recommended?sector=${encodeURIComponent(sector)}`),
    apiFetch(`/api/pilots/${PILOT}/cleancity/workload?sector=${encodeURIComponent(sector)}`),
    apiFetch(`/api/pilots/${PILOT}/cleancity/tasks?sector=${encodeURIComponent(sector)}`),
  ]);
  STREETS = st.streets;
  BINS = bins.bins;
  map.getSource("streets").setData({ type: "FeatureCollection", features: STREETS.map((s) => ({
    type: "Feature", geometry: { type: "LineString", coordinates: s.coords }, properties: { seg_id: s.seg_id, cls: s.class } })) });
  map.getSource("bins").setData({ type: "FeatureCollection", features: BINS.map((b) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [b.lon, b.lat] }, properties: { id: b.id, status: b.status, due: b.needs_collection } })) });
  map.getSource("rec").setData({ type: "FeatureCollection", features: rec.recommended.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: {} })) });
  map.getSource("tasks").setData({ type: "FeatureCollection", features: tasks.tasks.map((x) => ({ type: "Feature", geometry: { type: "Point", coordinates: [x.lon, x.lat] }, properties: { id: x.id } })) });
  const counts = {};
  STREETS.forEach((s) => (counts[s.class] = (counts[s.class] || 0) + 1));
  $("legend").innerHTML = CFG.classes.map((c) => `<span><i style="background:${CLASS_COLOUR[c]}"></i>${esc(CFG.standards.classes[c].label)} (${counts[c] || 0})</span>`).join("") +
    `<span><i class="dot" style="background:#16a34a"></i>Bin</span><span><i class="dot hollow"></i>Suggested bin (${rec.count})</span><span><i class="dot" style="background:#dc2626"></i>GVP to clear</span>`;
  renderWorkload(wl.sectors[0]);
  renderBins();
  renderTasks(tasks.tasks);
  if (STREETS.length) {
    const b = new maplibregl.LngLatBounds();
    STREETS.forEach((s) => s.coords.forEach((c) => b.extend(c)));
    map.fitBounds(b, { padding: { top: 30, bottom: 30, left: window.innerWidth > 800 ? 420 : 30, right: 30 } });
  }
}

function renderWorkload(w) {
  const staff = Object.entries(w.available_staff).filter(([, v]) => v.count).map(([r, v]) => `${v.count} ${words(r).toLowerCase()}${v.count === 1 ? "" : "s"}`).join(", ");
  $("workload").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="k">Required</div><div class="v">${fmt(w.required_hours, 1)} h/day</div><div class="s">about ${fmt(w.workers_needed_at_8h, 1)} workers on 8 h</div></div>
      <div class="kpi ${w.staff_known && w.gap_hours < 0 ? "bad" : ""}"><div class="k">Available</div><div class="v">${fmt(w.available_hours, 1)} h/day</div><div class="s">${w.staff_known ? esc(staff || "no cleaning staff here") : "no staff entered yet"}</div></div>
    </div>
    ${w.staff_known ? `<p class="${w.gap_hours < 0 ? "warn" : "hint"}">${w.gap_hours < 0 ? `Short by ${fmt(-w.gap_hours, 1)} worker-hours a day.` : `${fmt(w.gap_hours, 1)} worker-hours a day spare.`}</p>` : `<p class="hint"><a href="resources.html">Enter cleaning staff</a> to compare.</p>`}
    ${table(["", "Hours/day"], [["Street sweeping", fmt(w.parts.street_sweeping, 1)], ["Public bins", fmt(w.parts.public_bins, 1)], ["GVP clearing (backlog)", fmt(w.parts.gvp_clearing_backlog, 1)]])}
    ${table(["Class", "Streets", "km", "Sweeping h/day"], CFG.classes.map((c) => [esc(CFG.standards.classes[c].label), w.by_class[c].streets, fmt(w.by_class[c].km, 1), fmt(w.by_class[c].hours, 1)]))}
    <p class="hint small">Productivity ${CFG.standards.productivity.street_m_per_worker_hour} m of street per worker-hour and ${CFG.standards.productivity.bin_service_minutes} min per bin service are estimates (reference/clean_city.json). Not yet counted: ${w.not_counted.join("; ")}.</p>`;
}

// ---------- Street ----------

function showStreet(seg) {
  const s = STREETS.find((x) => x.seg_id === seg);
  if (!s) return;
  selected = seg;
  map.setFilter("street-selected", ["==", ["get", "seg_id"], seg]);
  document.querySelector("[data-tab=street]").click();
  const std = CFG.standards.classes;
  $("street").innerHTML = `
    <h3>${esc(s.name || "Unnamed street")}</h3>
    <p class="small">${esc(s.sector)} · ${fmt(s.length_m)} m · ${fmt(s.width_m, 1)} m wide · ${esc(words(s.road_class))} road<br>
      ${s.buildings} buildings (${s.commercial} commercial${s.healthcare ? `, ${s.healthcare} health` : ""}) · ${s.building_density} per 100 m${s.dead_end ? " · dead end" : ""}<br>
      Suggested: <b>${esc(std[s.suggested_class].label)}</b> <span class="muted">(${esc(s.why)})</span></p>
    ${isPlanner() ? `
      <label>Cleaning class <select id="sClass">${CFG.classes.map((c) => `<option value="${c}" ${c === s.class ? "selected" : ""}>${esc(std[c].label)}${c === s.suggested_class ? " (suggested)" : ""}</option>`).join("")}</select></label>
      <label>Cleanings per week <input id="sFreq" type="number" min="0.5" max="42" step="0.5" value="${s.frequency_overridden ? s.cleanings_per_week : ""}" placeholder="Class standard: ${std[s.class].cleanings_per_week}" /></label>
      <label>Cleaning team <input id="sTeam" maxlength="80" value="${esc(s.team || "")}" /></label>
      <label>Equipment <input id="sEquip" maxlength="80" value="${esc(s.equipment || "")}" placeholder="e.g. handcart, mechanical sweeper" /></label>
      <p id="sErr" class="warn" hidden></p>
      <button type="button" id="sSave">Save street plan</button>`
    : `<p>${esc(std[s.class].label)}, ${s.cleanings_per_week} cleanings a week${s.team ? `, ${esc(s.team)}` : ""}.</p>`}`;
  $("sSave")?.addEventListener("click", async () => {
    try {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/cleancity/streets/${encodeURIComponent(seg)}`, {
        class: $("sClass").value === s.suggested_class ? null : $("sClass").value,
        cleanings_per_week: $("sFreq").value === "" ? null : +$("sFreq").value, team: $("sTeam").value, equipment: $("sEquip").value });
      toast("Street plan saved.");
      await loadSector();
      showStreet(seg);
    } catch (err) {
      $("sErr").textContent = err.message;
      $("sErr").hidden = false;
    }
  });
}

// ---------- Bins ----------

function renderBins() {
  $("addBin").hidden = !isPlanner();
  if (!BINS.length) { $("bins").innerHTML = `<p class="hint">No bins recorded in this sector yet.</p>`; return; }
  const canReport = CFG.bin_reporters.includes(SESSION?.role);
  $("bins").innerHTML = table(["Bin", "Street", "Status", ""], BINS.map((b) => [
    `<b>${esc(b.bin_code)}</b><br><span class="muted small">${b.capacity_l} L ${esc(words(b.stream))}</span>`,
    `${esc(b.road_name || "–")}<br><span class="muted small">${b.last_serviced ? `serviced ${esc(b.last_serviced.slice(0, 16).replace("T", " "))}` : "never serviced"}${b.needs_collection ? ' · <span class="warn">needs collection</span>' : ""}</span>`,
    canReport ? `<select data-bin="${b.id}">${CFG.bin_statuses.map((k) => `<option value="${k}" ${k === b.status ? "selected" : ""}>${words(k)}</option>`).join("")}</select>` : esc(words(b.status)),
    canReport ? `<button type="button" class="secondary small" data-serviced="${b.id}">Serviced</button>` : ""]));
  $("bins").querySelectorAll("[data-bin]").forEach((el) => el.addEventListener("change", () => binUpdate(el.dataset.bin, { status: el.value })));
  $("bins").querySelectorAll("[data-serviced]").forEach((el) => el.addEventListener("click", () => binUpdate(el.dataset.serviced, { serviced: true })));
}

async function binUpdate(id, body) {
  try {
    await swmWrite("PATCH", `/api/pilots/${PILOT}/cleancity/bins/${id}`, body);
    await loadSector();
    document.querySelector("[data-tab=bins]").click();
  } catch (err) { toast(err.message); }
}

$("addBin").addEventListener("click", () => {
  adding = !adding;
  $("addBin").classList.toggle("on", adding);
  map.getCanvas().style.cursor = adding ? "crosshair" : "";
  $("binHint").textContent = adding ? "Tap the street where the bin stands; it is placed on the nearest street." : "";
});

// ---------- GVP clearing ----------

function renderTasks(tasks) {
  $("tasks").innerHTML = tasks.length
    ? `<p class="hint small">Verified garbage vulnerable points to clear, most severe first. Clearing one sends its waste to the route builder as a pickup.</p>` +
      table(["Place", "Severity", "Clear by", "Equipment"], tasks.map((x) => [
        `<a href="surveyor.html?gvp=${encodeURIComponent(x.id)}">${esc(x.landmark || x.road_name || "GVP")}</a><br><span class="muted small">${esc(words(x.status))}${x.assigned_to ? `, ${esc(x.assigned_to)}` : ""}</span>`,
        `<span class="sev sev-${x.severity}">${esc(words(x.severity))}</span>`, esc((x.respond_by || "").slice(0, 10)), x.required_equipment.map(words).join(", ")]))
    : `<p class="hint">No GVPs waiting to be cleared in this sector.</p>`;
}

// ---------- Map ----------

map.on("load", async () => {
  await UI_READY;
  try {
    const [cfg, sectors] = await Promise.all([apiFetch("/api/cleancity/config"), apiFetch(`/api/pilots/${PILOT}/sectors`)]);
    CFG = cfg;
    $("rules").innerHTML = CFG.rules.map((r) => `SWM Rules 2026, r. ${esc(r.rule)}: ${esc(r.text)}`).join("<br>") + "<br>Classes, frequencies, bin spacing and productivity are planning standards in reference/clean_city.json.";
    $("sector").innerHTML = sectors.features.map((f) => `<option>${esc(f.properties.name)}</option>`).join("");
    map.addSource("streets", { type: "geojson", data: empty });
    map.addLayer({ id: "streets", type: "line", source: "streets", layout: { "line-cap": "round" },
      paint: { "line-color": ["match", ["get", "cls"], ...Object.entries(CLASS_COLOUR).flat(), "#999"], "line-width": ["interpolate", ["linear"], ["zoom"], 14, 2.5, 18, 7] } });
    map.addLayer({ id: "street-selected", type: "line", source: "streets", filter: ["==", ["get", "seg_id"], ""], paint: { "line-color": "#0b0b0c", "line-width": 9, "line-opacity": 0.35 } });
    map.addSource("rec", { type: "geojson", data: empty });
    map.addLayer({ id: "rec", type: "circle", source: "rec", minzoom: 15, paint: { "circle-radius": 3.5, "circle-color": "#fff", "circle-stroke-color": "#16a34a", "circle-stroke-width": 1.5 } });
    map.addSource("bins", { type: "geojson", data: empty });
    map.addLayer({ id: "bins", type: "circle", source: "bins", paint: {
      "circle-radius": 6, "circle-color": ["match", ["get", "status"], ...Object.entries(BIN_COLOUR).flat(), "#16a34a"],
      "circle-stroke-color": ["case", ["get", "due"], "#0b0b0c", "#fff"], "circle-stroke-width": ["case", ["get", "due"], 3, 1.5] } });
    map.addSource("tasks", { type: "geojson", data: empty });
    map.addLayer({ id: "tasks", type: "circle", source: "tasks", paint: { "circle-radius": 8, "circle-color": "#dc2626", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    map.on("mouseenter", "streets", () => (map.getCanvas().style.cursor = adding ? "crosshair" : "pointer"));
    map.on("mouseleave", "streets", () => (map.getCanvas().style.cursor = adding ? "crosshair" : ""));
    map.on("click", async (e) => {
      if (adding) {
        adding = false;
        $("addBin").classList.remove("on");
        map.getCanvas().style.cursor = "";
        $("binHint").textContent = "";
        try {
          const b = await swmWrite("POST", `/api/pilots/${PILOT}/cleancity/bins`, { lon: e.lngLat.lng, lat: e.lngLat.lat });
          toast(`${b.bin_code} added on ${b.road_name || "the street"}.`);
          await loadSector();
          document.querySelector("[data-tab=bins]").click();
        } catch (err) { toast(err.message); }
        return;
      }
      const { x, y } = e.point;
      const hit = map.queryRenderedFeatures([[x - 6, y - 6], [x + 6, y + 6]], { layers: ["streets"] });
      if (hit.length) showStreet(hit[0].properties.seg_id);
    });
    $("sector").addEventListener("change", loadSector);
    $("showRec").addEventListener("change", () => map.setLayoutProperty("rec", "visibility", $("showRec").checked ? "visible" : "none"));
    await loadSector();
  } catch (err) {
    $("workload").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
});

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.tab));
}));
