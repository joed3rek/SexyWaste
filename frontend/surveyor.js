// Surveyor home: map of the signed-in surveyor's sectors, coloured by visit outcome, with the
// building sheet and the Round 1 survey flow (survey_flow.js). Strings come from strings.json.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SESSION = getSession();
const OUTCOME_COLOURS = {
  completed: "#16a34a", partial: "#f59e0b", refused: "#6b7280", locked: "#6b7280", revisit_requested: "#2563eb",
  demolished: "#a855f7", not_a_building: "#a855f7", footprint_issue: "#a855f7",
};
const NOT_VISITED = "#c3c9d2";
const LEGEND = [["legend.completed", "#16a34a"], ["legend.partial", "#f59e0b"], ["legend.refused_locked", "#6b7280"],
  ["legend.revisit", "#2563eb"], ["legend.other", "#a855f7"], ["legend.not_visited", NOT_VISITED]];

let CONFIG = null;
let fc = null;
const byId = new Map();
let pinMode = null; // "missing" or "gvp" while the next tap places a pin

if (!SESSION || !["surveyor", "survey_supervisor", "admin"].includes(SESSION.role)) location.replace("index.html");

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f4f5f6" } },
      { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.55, "raster-saturation": -1, "raster-contrast": -0.1 } },
    ],
  },
  center: [77.641, 12.9125],
  zoom: 15,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: true }), "bottom-right");

function eachCoord(geom, fn) { const w = (c) => (typeof c[0] === "number" ? fn(c) : c.forEach(w)); w(geom.coordinates); }
function boundsOf(geoms) { const b = new maplibregl.LngLatBounds(); geoms.forEach((g) => eachCoord(g, (c) => b.extend(c))); return b; }

function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 3500);
}

function colourExpr() {
  return ["match", ["coalesce", ["get", "visit_outcome"], "none"], ...Object.entries(OUTCOME_COLOURS).flat(), NOT_VISITED];
}

async function refreshProgress() {
  try {
    const h = await apiFetch(`/api/pilots/${PILOT}/surveyor/home`);
    $("progress").innerHTML = `<span class="pill">${esc(t("sv.today", { n: h.today }))}</span>` + h.sectors.map((s) => {
      const visited = s.buildings - s.not_visited;
      const pct = s.buildings ? (100 * visited) / s.buildings : 0;
      const n = (x) => Number(x).toLocaleString("en-IN");
      return `<span class="pill">${esc(t("sv.sector_progress", { sector: s.sector, visited: n(visited), total: n(s.buildings) }))}<i class="bar"><i style="width:${pct}%"></i></i></span>`;
    }).join("");
  } catch (err) {
    $("progress").innerHTML = `<span class="warn">${esc(err.message)}</span>`;
  }
}

async function refreshOverrides() {
  const overrides = await apiFetch(`/api/pilots/${PILOT}/overrides`);
  for (const [id, props] of Object.entries(overrides)) {
    const f = byId.get(id);
    if (f) Object.assign(f.properties, props);
  }
  map.getSource("buildings").setData(fc);
}

map.on("load", async () => {
  await UI_READY;
  document.title = t("sv.title");
  $("who").textContent = `${SESSION.name} · ${t(`role.${SESSION.role}`)}`;
  $("legend").innerHTML = LEGEND.map(([k, c]) => `<div><span class="swatch" style="background:${c}"></span>${esc(t(k))}</div>`).join("") +
    `<div><span class="swatch outline"></span>${esc(t("legend.survey_first"))}</div>` +
    `<div><span class="swatch dot" style="background:#dc2626"></span>${esc(t("legend.gvp"))}</div>` +
    `<div><span class="swatch dot" style="background:#f59e0b"></span>${esc(t("legend.gvp_pickup"))}</div>` +
    `<div><span class="swatch dot" style="background:#9ca3af"></span>${esc(t("legend.gvp_watch"))}</div>`;
  if (!SESSION.sectors?.length) {
    $("progress").innerHTML = `<span class="warn">${esc(t("sv.no_sectors"))}</span>`;
    return;
  }
  toast(t("sv.loading_map"));
  try {
    const [sectors, buildings, config] = await Promise.all([
      apiFetch(`/api/pilots/${PILOT}/sectors`), apiFetch(`/api/pilots/${PILOT}/buildings`), apiFetch("/api/survey/config")]);
    CONFIG = config;
    const mine = sectors.features.filter((f) => SESSION.sectors.includes(f.properties.name));
    fc = { type: "FeatureCollection", features: buildings.features.filter((f) => SESSION.sectors.includes(f.properties.sector)) };
    fc.features.forEach((f) => byId.set(f.properties.id, f));
    map.addSource("sectors", { type: "geojson", data: { type: "FeatureCollection", features: mine } });
    map.addLayer({ id: "sectors", type: "line", source: "sectors", paint: { "line-color": "#0b0b0c", "line-width": 2, "line-dasharray": [3, 2] } });
    map.addSource("buildings", { type: "geojson", data: fc, promoteId: "id" });
    map.addLayer({ id: "buildings", type: "fill", source: "buildings", paint: { "fill-color": colourExpr(), "fill-opacity": 0.9 } });
    map.addLayer({ id: "buildings-line", type: "line", source: "buildings", minzoom: 16, paint: { "line-color": "#475569", "line-width": 0.4 } });
    map.addLayer({ id: "survey-first", type: "line", source: "buildings",
      filter: ["all", ["==", ["get", "bwg_status"], "bwg_likely"], ["!", ["to-boolean", ["get", "visit_outcome"]]]],
      paint: { "line-color": "#dc2626", "line-width": 2.5 } });
    map.addLayer({ id: "selected", type: "line", source: "buildings", filter: ["==", ["get", "id"], ""], paint: { "line-color": "#000", "line-width": 3.5 } });
    map.fitBounds(boundsOf(fc.features.length ? fc.features.map((f) => f.geometry) : mine.map((f) => f.geometry)), { padding: 40 });
    $("tapHint").hidden = false;
    // Buildings are small on a phone: a tap opens the building under the finger or within a few pixels of it.
    map.on("click", (e) => {
      if (pinMode) return;
      const { x, y } = e.point, r = 10;
      const box = [[x - r, y - r], [x + r, y + r]];
      const gvps = map.getLayer("gvps") ? map.queryRenderedFeatures(box, { layers: ["gvps"] }) : [];
      if (gvps.length) return gvpSheet(gvps[0].properties.id);
      const hits = map.queryRenderedFeatures(box, { layers: ["buildings"] });
      if (hits.length) openSheet(hits[0].properties.id);
    });
    map.on("mouseenter", "buildings", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "buildings", () => (map.getCanvas().style.cursor = ""));
    await refreshOverrides();
    map.addSource("gvps", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "gvps", type: "circle", source: "gvps", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 6, 18, 11],
      "circle-color": ["match", ["get", "state"], "pickup", "#f59e0b", "watch", "#9ca3af", "#dc2626"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    await loadGvps();
    $("toast").hidden = true;
    const params = new URLSearchParams(location.search);
    const wanted = params.get("building");
    if (wanted && byId.has(wanted)) { map.fitBounds(boundsOf([byId.get(wanted).geometry]), { padding: 120, maxZoom: 18 }); openSheet(wanted); }
    const wantedGvp = params.get("gvp");
    if (wantedGvp && GVPS.has(wantedGvp)) { const g = GVPS.get(wantedGvp); map.jumpTo({ center: [g.lon, g.lat], zoom: 18 }); gvpSheet(wantedGvp); }
  } catch (err) {
    toast(err.message);
  }
  refreshProgress();
});

// ---------- building sheet ----------

async function openSheet(id) {
  $("tapHint").hidden = true;
  map.setFilter("selected", ["==", ["get", "id"], id]);
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `<p class="hint">${esc(t("common.loading"))}</p>`;
  let card;
  try {
    card = await apiFetch(`/api/pilots/${PILOT}/building?id=${encodeURIComponent(id)}`);
  } catch (err) {
    sheet.innerHTML = `<p class="warn">${esc(err.message)}</p>`;
    return;
  }
  const visits = card.survey_detail.visits.filter((v) => v.purpose === "survey" && v.outcome);
  const last = visits[visits.length - 1];
  const surveyFirst = card.bwg_status === "bwg_likely" && !last;
  const canSurvey = SESSION.role === "surveyor";
  sheet.innerHTML = `
    <div class="sheet-head"><div><b>${esc(card.name || card.address || t("sheet.no_name"))}</b>
      <br><span class="muted small">${esc(card.sector || "")}${card.house_number ? ` · #${esc(card.house_number)}` : ""}</span></div>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    ${card.building_use_hint ? `<p class="hint">${esc(t("sheet.osm_hint", { use: t(`building_use.${card.building_use_hint}`) }))}</p>` : ""}
    <p class="small">${last ? esc(t("sheet.last_visit", { outcome: t(`outcome.${last.outcome}`), name: last.user_name || "" })) : esc(t("sheet.never"))}</p>
    ${surveyFirst ? `<p class="warn">${esc(t("sheet.survey_first"))}</p>` : ""}
    ${canSurvey ? `<button type="button" class="lg block" id="startVisit">${esc(t("sheet.start"))}</button>`
      : `<p class="hint">${esc(t("sheet.supervisor_note"))}</p>`}
    <button type="button" class="secondary block" id="reportFlag">${esc(t("sheet.report"))}</button>`;
  $("sheetClose").addEventListener("click", closeSheet);
  $("startVisit")?.addEventListener("click", () => {
    closeSheet();
    SurveyFlow.open({ mode: "survey", building: card, config: CONFIG, onDone: afterVisit });
  });
  $("reportFlag").addEventListener("click", () => flagForm(card));
}

function closeSheet() {
  $("sheet").hidden = true;
  map.setFilter("selected", ["==", ["get", "id"], ""]);
}

async function afterVisit(result) {
  if (result) toast(t("common.saved"));
  await refreshOverrides();
  refreshProgress();
}

// ---------- map problems ----------

function flagForm(card, lngLat) {
  const kinds = lngLat ? ["missing_from_map"] : CONFIG.geometry_flag_kinds.filter((k) => k !== "missing_from_map");
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `
    <div class="sheet-head"><b>${esc(t("flag.title"))}</b>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    ${lngLat ? "" : `<p class="label">${esc(t("flag.choose"))}</p>`}
    <div class="tiles tiles-2">${kinds.map((k, i) => `<button type="button" class="tile${lngLat && i === 0 ? " on" : ""}" data-kind="${k}">${esc(t(`flag.${k}`))}</button>`).join("")}</div>
    <label>${esc(t(lngLat ? "sv.missing_note" : "flag.note"))}<textarea id="flagNote" rows="2"></textarea></label>
    <p id="flagErr" class="warn" hidden></p>
    <button type="button" class="lg block" id="flagSave">${esc(t("common.save"))}</button>`;
  let kind = lngLat ? "missing_from_map" : null;
  sheet.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
    sheet.querySelectorAll("[data-kind]").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    kind = b.dataset.kind;
  }));
  $("sheetClose").addEventListener("click", closeSheet);
  $("flagSave").addEventListener("click", async () => {
    if (!kind) return;
    try {
      await swmWrite("POST", `/api/pilots/${PILOT}/geometry-flags`, {
        kind, building_id: lngLat ? null : card.id, lon: lngLat?.lng ?? null, lat: lngLat?.lat ?? null, note: $("flagNote").value || null });
      closeSheet();
      toast(t(lngLat ? "sv.missing_saved" : "flag.saved"));
    } catch (err) {
      $("flagErr").textContent = err.message;
      $("flagErr").hidden = false;
    }
  });
}

function togglePin(mode) {
  pinMode = pinMode === mode ? null : mode;
  $("missingBtn").classList.toggle("on", pinMode === "missing");
  $("gvpBtn").classList.toggle("on", pinMode === "gvp");
  map.getCanvas().style.cursor = pinMode ? "crosshair" : "";
  if (pinMode) toast(t(pinMode === "gvp" ? "sv.pin_gvp" : "sv.pin_missing"));
}
$("missingBtn").addEventListener("click", () => togglePin("missing"));
$("gvpBtn").addEventListener("click", () => togglePin("gvp"));
map.on("click", (e) => {
  if (!pinMode) return;
  const mode = pinMode;
  togglePin(mode);
  if (mode === "gvp") gvpForm(null, e.lngLat);
  else flagForm(null, e.lngLat);
});
$("legendBtn").addEventListener("click", () => ($("legend").hidden = !$("legend").hidden));
$("signOut").addEventListener("click", signOut);

// ---------- Garbage mapping: GVPs (SWM Rules 2026, r. 15(1)) ----------
// Reports go on the street network. Supervisors move a GVP through its lifecycle; clearing it
// leaves a pickup for the route builder.

const GVPS = new Map();
const QUANTITY_CHIPS = [5, 20, 50, 100, 250];
const OPEN_STATES = ["reported", "verified", "assigned", "cleaning", "recurred"];
const fmtDate = (iso) => (iso || "").slice(0, 10);

function gvpState(g) {
  if (g.pickup) return "pickup";
  return OPEN_STATES.includes(g.status) ? "open" : "watch";
}

async function loadGvps() {
  try {
    const { gvps } = await apiFetch(`/api/pilots/${PILOT}/gvps`);
    GVPS.clear();
    gvps.filter((g) => SESSION.sectors.includes(g.sector) && g.status !== "rejected").forEach((g) => GVPS.set(g.id, g));
    map.getSource("gvps").setData({ type: "FeatureCollection", features: [...GVPS.values()].map((g) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] }, properties: { id: g.id, state: gvpState(g) } })) });
  } catch (err) {
    toast(err.message);
  }
}

function chipSet(name, keys, label, type, chosen = []) {
  return `<div class="chips">${keys.map((k) => `<label class="chip"><input type="${type}" name="${name}" value="${k}" ${chosen.includes(k) ? "checked" : ""} /><span>${label(k)}</span></label>`).join("")}</div>`;
}

function severityChips(chosen) {
  return chipSet("gSev", CONFIG.gvp.severities, (k) => `${esc(t(`sev.${k}`))} <small class="muted">${esc(t(`sev.${k}.help`))}</small>`, "radio", [chosen]);
}

// Report waste: at a pinned place (a new GVP, or added to one already mapped close by), or again at a GVP.
function gvpForm(g, lngLat) {
  $("tapHint").hidden = true;
  const cfg = CONFIG.gvp;
  const last = g?.report_log?.[g.report_log.length - 1];
  const where = g ? { lng: g.lon, lat: g.lat } : lngLat;
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `
    <div class="sheet-head"><b>${esc(t(g ? "gvp.report_again_title" : "gvp.new_title"))}</b>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    ${g ? "" : `<label>${esc(t("gvp.landmark"))}<input id="gLandmark" maxlength="120" placeholder="${esc(t("gvp.landmark_ph"))}" /></label>`}
    <p class="label">${esc(t("gvp.streams"))}</p>${chipSet("gStream", ["wet", "dry", "sanitary", "special"], (k) => esc(t(`stream.${k}`)), "checkbox", last?.streams || [])}
    <label>${esc(t("gvp.quantity_now"))}<input id="gQty" type="number" min="1" max="20000" step="1" inputmode="numeric" value="${last?.quantity_kg ?? ""}" /></label>
    <div class="chips">${QUANTITY_CHIPS.map((q) => `<button type="button" class="chip-btn" data-q="${q}">${q} kg</button>`).join("")}</div>
    <p class="label">${esc(t("gvp.severity"))}</p>${severityChips(last?.severity || g?.severity || "medium")}
    <label>${esc(t("gvp.frequency"))}<select id="gFreq">${cfg.frequencies.map((f) => `<option value="${f}" ${f === (last?.frequency || "daily") ? "selected" : ""}>${esc(t(`freq.${f}`))}</option>`).join("")}</select></label>
    <p class="label">${esc(t("gvp.sources"))}</p>${chipSet("gSource", cfg.sources, (k) => esc(t(`src.${k}`)), "checkbox", last?.sources || [])}
    <label>${esc(t("gvp.photos", { n: cfg.max_photos }))} <span class="muted small">(${esc(t("common.optional"))})</span><input id="gPhoto" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" multiple /></label>
    <label>${esc(t("gvp.note"))} <span class="muted small">(${esc(t("common.optional"))})</span><textarea id="gNote" rows="2" maxlength="500"></textarea></label>
    <p id="gErr" class="warn" hidden></p>
    <button type="button" class="lg block" id="gSave">${esc(t("gvp.save"))}</button>`;
  $("sheetClose").addEventListener("click", closeSheet);
  sheet.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => ($("gQty").value = b.dataset.q)));
  $("gSave").addEventListener("click", async () => {
    const err = (m) => { $("gErr").textContent = m; $("gErr").hidden = false; };
    const streams = [...sheet.querySelectorAll("input[name=gStream]:checked")].map((x) => x.value);
    const qty = +$("gQty").value;
    const files = [...$("gPhoto").files];
    if (!streams.length) return err(t("gvp.pick_stream"));
    if (!(qty > 0)) return err(t("gvp.pick_quantity"));
    if (files.length > cfg.max_photos) return err(t("gvp.too_many_photos", { n: cfg.max_photos }));
    $("gSave").disabled = true;
    try {
      const saved = await swmWrite("POST", `/api/pilots/${PILOT}/gvps`, {
        lon: where.lng, lat: where.lat, streams, quantity_kg: qty, frequency: $("gFreq").value,
        severity: sheet.querySelector("input[name=gSev]:checked")?.value || "medium",
        sources: [...sheet.querySelectorAll("input[name=gSource]:checked")].map((x) => x.value),
        landmark: g ? null : $("gLandmark").value || null, note: $("gNote").value || null });
      const note = await uploadPhotos(saved.report_id, files, where, t(saved.merged && !g ? "gvp.saved_merged" : "gvp.saved"));
      await loadGvps();
      toast(note);
      gvpSheet(saved.id);
    } catch (e) {
      err(e.message);
      $("gSave").disabled = false;
    }
  });
}

async function uploadPhotos(reportId, files, where, okText) {
  for (const file of files) {
    try {
      await swmWrite("POST", `/api/pilots/${PILOT}/gvp-reports/${reportId}/photos?lon=${where.lng}&lat=${where.lat}`, file, file.type || "image/jpeg");
    } catch (e) {
      return t("gvp.photo_failed", { message: e.message });
    }
  }
  return okText;
}

function eventText(e) {
  if (e.kind === "status") return t("gvp.event_status", { value: t(`gvp.status.${e.value}`) });
  if (e.kind === "severity") return t("gvp.event_severity", { value: t(`sev.${e.value}`) });
  if (e.kind === "collected") return t("gvp.event_collected");
  return t(`int.${e.kind}`);
}

// Which lifecycle actions fit the GVP's status (the server checks again).
const ACTIONS = {
  reported: ["verify", "reject"], recurred: ["verify", "assign", "reject"], verified: ["assign"],
  assigned: ["start", "clear", "assign"], cleaning: ["clear"], cleared: [], monitoring: [], rejected: [],
};

async function gvpSheet(id) {
  closeSheet();
  $("tapHint").hidden = true;
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `<p class="hint">${esc(t("common.loading"))}</p>`;
  let g;
  try {
    g = await apiFetch(`/api/pilots/${PILOT}/gvps/${encodeURIComponent(id)}`);
  } catch (err) {
    sheet.innerHTML = `<p class="warn">${esc(err.message)}</p>`;
    return;
  }
  const cfg = CONFIG.gvp;
  const manager = cfg.managers.includes(SESSION.role);
  const cleaner = cfg.cleaners.includes(SESSION.role);
  const actions = (ACTIONS[g.status] || []).filter((a) => (["start", "clear"].includes(a) ? cleaner : manager));
  if (g.pickup && cleaner) actions.push("collected");
  const reports = g.report_log.slice().reverse();
  sheet.innerHTML = `
    <div class="sheet-head"><div><b>${esc(g.landmark || g.road_name || t("gvp.title"))}</b>
      <br><span class="muted small">${esc(g.sector || "")} · ${esc(t(`gvp.status.${g.status}`))} · <span class="sev sev-${g.severity}">${esc(t(`sev.${g.severity}`))}</span></span></div>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    <p class="small">${esc(t("gvp.street", { street: g.road_name || "–", seg: g.seg_id || "–" }))}
      ${g.assigned_to ? `<br>${esc(t("gvp.assigned_to", { name: g.assigned_to }))}` : ""}
      ${g.respond_by ? `<br><b>${esc(t("gvp.respond_by", { date: fmtDate(g.respond_by) }))}</b>` : ""}
      ${g.pickup ? `<br><b>${esc(t("gvp.pickup", { kg: g.pickup.kg, date: fmtDate(g.pickup.since) }))}</b>` : ""}
      ${g.recurrences ? `<br>${esc(t("gvp.recurrences", { n: g.recurrences }))}` : ""}</p>
    <p class="label">${esc(t("gvp.reports"))}</p>
    <ul class="small gvp-log">${reports.map((o) => `<li>${esc(t("gvp.report_line", { date: fmtDate(o.at), name: o.user_name || "", source: t(`rsrc.${o.source}`),
      kg: o.quantity_kg, freq: t(`freq.${o.frequency}`), severity: t(`sev.${o.severity}`) }))}${o.note ? ` · ${esc(o.note)}` : ""}
      ${o.photos.map((p, i) => ` <a href="/api/pilots/${PILOT}/gvp-photos/${p.id}" target="_blank" rel="noopener">${esc(t("gvp.photo_open"))} ${i + 1}</a>`).join("")}</li>`).join("")}</ul>
    <p class="label">${esc(t("gvp.history"))}</p>
    <ul class="small gvp-log">${g.events.length ? g.events.slice().reverse().map((e) => `<li>${esc(fmtDate(e.at))} · ${esc(e.user_name || "")}: ${esc(eventText(e))}${e.note ? ` · ${esc(e.note)}` : ""}</li>`).join("") : `<li class="muted">${esc(t("gvp.none_yet"))}</li>`}</ul>
    <button type="button" class="secondary block" id="gAgain">${esc(t("gvp.report_again"))}</button>
    ${actions.length || manager ? `<div class="gvp-manage"><p class="label">${esc(t("gvp.manage"))}</p>
      ${actions.includes("verify") ? `<label>${esc(t("gvp.verify_severity"))}<select id="gVerifySev">${cfg.severities.map((k) => `<option value="${k}" ${k === g.severity ? "selected" : ""}>${esc(t(`sev.${k}`))}</option>`).join("")}</select></label>` : ""}
      ${actions.includes("assign") ? `<label>${esc(t("gvp.assign_to"))}<input id="gAssignTo" maxlength="120" value="${esc(g.assigned_to || "")}" /></label>` : ""}
      ${actions.includes("clear") ? `<label>${esc(t("gvp.clear_kg"))}<input id="gClearKg" type="number" min="1" step="1" value="${g.quantity_kg ?? ""}" /></label>` : ""}
      ${actions.map((a) => `<button type="button" class="${a === "reject" ? "secondary" : ""} block" data-act="${a}">${esc(t(`act.${a}`))}</button>`).join("")}
      ${manager ? `
        ${actions.includes("verify") ? "" : `<label>${esc(t("act.severity"))}<select id="gSevChange">${cfg.severities.map((k) => `<option value="${k}" ${k === g.severity ? "selected" : ""}>${esc(t(`sev.${k}`))}</option>`).join("")}</select></label>`}
        <label>${esc(t("gvp.record"))}<select id="gInt">${cfg.interventions.map((k) => `<option value="${k}">${esc(t(`int.${k}`))}</option>`).join("")}</select></label>
        <input id="gIntNote" maxlength="500" placeholder="${esc(t("gvp.note"))}" />
        <button type="button" class="secondary block" id="gIntSave">${esc(t("gvp.record"))}</button>` : ""}
    </div>` : ""}
    <p id="gErr" class="warn" hidden></p>`;
  $("sheetClose").addEventListener("click", closeSheet);
  $("gAgain").addEventListener("click", () => gvpForm(g));
  const act = async (body) => {
    try {
      await swmWrite("POST", `/api/pilots/${PILOT}/gvps/${g.id}/actions`, body);
      await loadGvps();
      gvpSheet(g.id);
    } catch (e) {
      $("gErr").textContent = e.message;
      $("gErr").hidden = false;
    }
  };
  sheet.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
    const a = b.dataset.act;
    if (a === "verify") return act({ action: a, value: $("gVerifySev").value });
    if (a === "assign") return act({ action: a, value: $("gAssignTo").value });
    if (a === "clear") return act({ action: a, kg: +$("gClearKg").value || null });
    return act({ action: a });
  }));
  if (!manager) return;
  $("gSevChange")?.addEventListener("change", () => act({ action: "severity", value: $("gSevChange").value }));
  $("gIntSave").addEventListener("click", () => act({ action: $("gInt").value, note: $("gIntNote").value || null }));
}
