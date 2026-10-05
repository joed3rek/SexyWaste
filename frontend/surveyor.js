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

if (!SESSION || !["surveyor", "survey_supervisor"].includes(SESSION.role)) location.replace("index.html");

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
    `<div><span class="swatch dot" style="background:#9ca3af"></span>${esc(t("legend.gvp_off"))}</div>`;
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
      "circle-color": ["case", ["get", "on_routes"], "#dc2626", "#9ca3af"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
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

// ---------- Garbage vulnerable points (SWM Rules 2026, r. 15(1)) ----------

const GVPS = new Map();
const QUANTITY_CHIPS = [5, 20, 50, 100, 250];
const fmtDate = (iso) => (iso || "").slice(0, 10);

async function loadGvps() {
  try {
    const { gvps } = await apiFetch(`/api/pilots/${PILOT}/gvps`);
    GVPS.clear();
    gvps.filter((g) => SESSION.sectors.includes(g.sector)).forEach((g) => GVPS.set(g.id, g));
    map.getSource("gvps").setData({ type: "FeatureCollection", features: [...GVPS.values()].map((g) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] }, properties: { id: g.id, on_routes: g.on_routes } })) });
  } catch (err) {
    toast(err.message);
  }
}

function checks(name, keys, prefix, chosen) {
  return `<div class="chips">${keys.map((k) => `<label class="chip"><input type="checkbox" name="${name}" value="${k}" ${chosen.includes(k) ? "checked" : ""} /><span>${esc(t(`${prefix}.${k}`))}</span></label>`).join("")}</div>`;
}

// New GVP at a pinned place, or a new observation of an existing one.
function gvpForm(g, lngLat) {
  $("tapHint").hidden = true;
  const cfg = CONFIG.gvp;
  const last = g?.observation_log?.[g.observation_log.length - 1];
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `
    <div class="sheet-head"><b>${esc(t(g ? "gvp.obs_title" : "gvp.new_title"))}</b>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    ${g ? "" : `<label>${esc(t("gvp.landmark"))}<input id="gLandmark" maxlength="120" placeholder="${esc(t("gvp.landmark_ph"))}" /></label>`}
    <p class="label">${esc(t("gvp.streams"))}</p>${checks("gStream", ["wet", "dry", "sanitary", "special"], "stream", last?.streams || [])}
    <label>${esc(t("gvp.quantity"))}<input id="gQty" type="number" min="1" max="20000" step="1" inputmode="numeric" value="${last?.quantity_kg ?? ""}" /></label>
    <div class="chips">${QUANTITY_CHIPS.map((q) => `<button type="button" class="chip-btn" data-q="${q}">${q} kg</button>`).join("")}</div>
    <label>${esc(t("gvp.frequency"))}<select id="gFreq">${cfg.frequencies.map((f) => `<option value="${f}" ${f === (last?.frequency || "daily") ? "selected" : ""}>${esc(t(`freq.${f}`))}</option>`).join("")}</select></label>
    <p class="label">${esc(t("gvp.sources"))}</p>${checks("gSource", cfg.sources, "src", last?.sources || [])}
    <label>${esc(t("gvp.photo"))} <span class="muted small">(${esc(t("common.optional"))})</span><input id="gPhoto" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" /></label>
    <label>${esc(t("gvp.note"))} <span class="muted small">(${esc(t("common.optional"))})</span><textarea id="gNote" rows="2" maxlength="500"></textarea></label>
    <p id="gErr" class="warn" hidden></p>
    <button type="button" class="lg block" id="gSave">${esc(t(g ? "common.save" : "gvp.save"))}</button>`;
  $("sheetClose").addEventListener("click", closeSheet);
  sheet.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => ($("gQty").value = b.dataset.q)));
  $("gSave").addEventListener("click", async () => {
    const err = (m) => { $("gErr").textContent = m; $("gErr").hidden = false; };
    const streams = [...sheet.querySelectorAll("input[name=gStream]:checked")].map((x) => x.value);
    const qty = +$("gQty").value;
    if (!streams.length) return err(t("gvp.pick_stream"));
    if (!(qty > 0)) return err(t("gvp.pick_quantity"));
    const body = { streams, quantity_kg: qty, frequency: $("gFreq").value,
      sources: [...sheet.querySelectorAll("input[name=gSource]:checked")].map((x) => x.value), note: $("gNote").value || null };
    $("gSave").disabled = true;
    try {
      const saved = g
        ? await swmWrite("POST", `/api/pilots/${PILOT}/gvps/${g.id}/observations`, body)
        : await swmWrite("POST", `/api/pilots/${PILOT}/gvps`, { ...body, lon: lngLat.lng, lat: lngLat.lat, landmark: $("gLandmark").value || null });
      const file = $("gPhoto").files[0];
      let note = t(g ? "gvp.obs_saved" : "gvp.saved");
      if (file) {
        const obs = saved.observation_log[saved.observation_log.length - 1];
        try {
          await swmWrite("POST", `/api/pilots/${PILOT}/gvp-observations/${obs.id}/photo`, file, file.type || "image/jpeg");
        } catch (e) {
          note = t("gvp.photo_failed", { message: e.message });
        }
      }
      await loadGvps();
      toast(note);
      gvpSheet(saved.id);
    } catch (e) {
      err(e.message);
      $("gSave").disabled = false;
    }
  });
}

function eventText(e) {
  if (e.kind === "status") return t("gvp.event_status", { value: t(`gvp.status.${e.value}`) });
  if (e.kind === "collect") return t(e.value === "1" ? "gvp.event_collect_on" : "gvp.event_collect_off");
  return t(`int.${e.kind}`);
}

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
  const sup = SESSION.role === "survey_supervisor";
  const obs = g.observation_log.slice(-5).reverse();
  sheet.innerHTML = `
    <div class="sheet-head"><div><b>${esc(g.landmark || t("gvp.title"))}</b>
      <br><span class="muted small">${esc(g.sector || "")} · ${esc(t(`gvp.status.${g.status}`))} · ${esc(t(g.on_routes ? "gvp.on_routes" : "gvp.off_routes"))}${g.reported_by_public ? ` · ${esc(t("gvp.reported_by_public"))}` : ""}${g.road_name ? `<br>${esc(g.road_name)}` : ""}</span></div>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    <p><b>${esc(t("gvp.kg_day", { kg: g.kg_per_day }))}</b><br><span class="small">${g.streams.map((s) => esc(t(`stream.${s}`))).join(", ")}</span></p>
    <p class="label">${esc(t("gvp.observations"))}</p>
    <ul class="small gvp-log">${obs.map((o) => `<li>${esc(t("gvp.obs_line", { date: fmtDate(o.at), name: o.user_name || "", kg: o.quantity_kg, freq: t(`freq.${o.frequency}`) }))}${o.note ? ` · ${esc(o.note)}` : ""}</li>`).join("")}</ul>
    <p class="label">${esc(t("gvp.interventions"))}</p>
    <ul class="small gvp-log">${g.events.length ? g.events.slice().reverse().map((e) => `<li>${esc(fmtDate(e.at))} · ${esc(e.user_name || "")}: ${esc(eventText(e))}${e.note ? ` · ${esc(e.note)}` : ""}</li>`).join("") : `<li class="muted">${esc(t("gvp.none_yet"))}</li>`}</ul>
    <button type="button" class="lg block" id="gObserve">${esc(t("gvp.add_obs"))}</button>
    ${sup ? `
      <div class="gvp-manage">
        <label>${esc(t("gvp.record"))}<select id="gInt">${cfg.interventions.map((k) => `<option value="${k}">${esc(t(`int.${k}`))}</option>`).join("")}</select></label>
        <input id="gIntNote" maxlength="500" placeholder="${esc(t("gvp.note"))}" />
        <button type="button" class="secondary block" id="gIntSave">${esc(t("gvp.record"))}</button>
        <label>${esc(t("gvp.set_status"))}<select id="gStatus">${cfg.statuses.map((k) => `<option value="${k}" ${k === g.status ? "selected" : ""}>${esc(t(`gvp.status.${k}`))}</option>`).join("")}</select></label>
        <label class="check"><input type="checkbox" id="gCollect" ${g.collect ? "checked" : ""} /> ${esc(t("gvp.collect"))}</label>
      </div>` : ""}
    <p id="gErr" class="warn" hidden></p>`;
  $("sheetClose").addEventListener("click", closeSheet);
  $("gObserve").addEventListener("click", () => gvpForm(g));
  if (!sup) return;
  const event = async (body) => {
    try {
      await swmWrite("POST", `/api/pilots/${PILOT}/gvps/${g.id}/events`, body);
      await loadGvps();
      gvpSheet(g.id);
    } catch (e) {
      $("gErr").textContent = e.message;
      $("gErr").hidden = false;
    }
  };
  $("gIntSave").addEventListener("click", () => event({ kind: $("gInt").value, note: $("gIntNote").value || null }));
  $("gStatus").addEventListener("change", () => event({ kind: "status", value: $("gStatus").value }));
  $("gCollect").addEventListener("change", () => event({ kind: "collect", value: $("gCollect").checked ? "true" : "false" }));
}
