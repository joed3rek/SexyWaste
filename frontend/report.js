// Public reporting of garbage vulnerable points: tap the road where waste is dumped, answer a few
// questions, send. The server moves the pin onto the nearest road (or refuses it when no road is
// close) and adds the report to an existing point nearby instead of creating a duplicate.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SESSION = getSession();
const STREAM_KEYS = ["wet", "dry", "sanitary", "special"];
let CONFIG = null;
let pin = null;

if (!SESSION || SESSION.role !== "generator") location.replace("index.html");

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f4f5f6" } },
      { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.75, "raster-saturation": -0.6 } },
    ],
  },
  center: [77.641, 12.9125],
  zoom: 15,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: true }), "bottom-right");

function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").hidden = true), 4500);
}

async function loadGvps() {
  const { gvps } = await apiFetch(`/api/pilots/${PILOT}/gvps`);
  map.getSource("gvps").setData({ type: "FeatureCollection", features: gvps.filter((g) => !["rejected", "monitoring"].includes(g.status)).map((g) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] }, properties: { id: g.id } })) });
}

map.on("load", async () => {
  await UI_READY;
  document.title = t("pub.title");
  $("who").textContent = SESSION.name;
  try {
    const [sectors, config] = await Promise.all([apiFetch(`/api/pilots/${PILOT}/sectors`), apiFetch("/api/survey/config")]);
    CONFIG = config;
    map.addSource("sectors", { type: "geojson", data: sectors });
    map.addLayer({ id: "sectors", type: "line", source: "sectors", paint: { "line-color": "#0b0b0c", "line-width": 1.5, "line-dasharray": [3, 2] } });
    map.addSource("gvps", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "gvps", type: "circle", source: "gvps", paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 5, 18, 10], "circle-color": "#dc2626", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    await loadGvps();
  } catch (err) {
    toast(err.message);
  }
});

map.on("click", (e) => {
  if (!CONFIG) return;
  if (pin) pin.remove();
  pin = new maplibregl.Marker({ color: "#0b0b0c" }).setLngLat(e.lngLat).addTo(map);
  form(e.lngLat);
});

function chips(name, keys, label, type = "checkbox") {
  return `<div class="chips">${keys.map((k) => `<label class="chip"><input type="${type}" name="${name}" value="${k}" /><span>${esc(label(k))}</span></label>`).join("")}</div>`;
}

function form(lngLat) {
  const cfg = CONFIG.gvp;
  const sheet = $("sheet");
  sheet.hidden = false;
  sheet.innerHTML = `
    <div class="sheet-head"><b>${esc(t("pub.form_title"))}</b>
      <button type="button" class="close" id="sheetClose" aria-label="${esc(t("common.close"))}">×</button></div>
    <p class="label">${esc(t("pub.what"))}</p>
    ${chips("pStream", [...STREAM_KEYS, "mixed"], (k) => (k === "mixed" ? t("pub.mixed") : t(`stream.${k}`)))}
    <p class="label">${esc(t("pub.size"))}</p>
    ${chips("pSize", cfg.sizes, (k) => t(`pub.size.${k}`), "radio")}
    <p class="label">${esc(t("gvp.severity"))}</p>
    ${chips("pSev", cfg.severities, (k) => `${t(`sev.${k}`)}: ${t(`sev.${k}.help`)}`, "radio")}
    <label>${esc(t("gvp.frequency"))}<select id="pFreq">${cfg.frequencies.map((f) => `<option value="${f}">${esc(t(`freq.${f}`))}</option>`).join("")}</select></label>
    <label>${esc(t("gvp.landmark"))}<input id="pLandmark" maxlength="120" placeholder="${esc(t("gvp.landmark_ph"))}" /></label>
    <label>${esc(t("gvp.photos", { n: cfg.max_photos }))} <span class="muted small">(${esc(t("common.optional"))})</span><input id="pPhoto" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" multiple /></label>
    <label>${esc(t("gvp.note"))} <span class="muted small">(${esc(t("common.optional"))})</span><textarea id="pNote" rows="2" maxlength="500"></textarea></label>
    <p id="pErr" class="warn" hidden></p>
    <button type="button" class="lg block" id="pSend">${esc(t("pub.send"))}</button>`;
  $("sheetClose").addEventListener("click", close);
  $("pSend").addEventListener("click", () => send(lngLat));
}

function close() {
  $("sheet").hidden = true;
  if (pin) { pin.remove(); pin = null; }
}

async function send(lngLat) {
  const err = (m) => { $("pErr").textContent = m; $("pErr").hidden = false; };
  const picked = [...document.querySelectorAll("input[name=pStream]:checked")].map((x) => x.value);
  const streams = [...new Set(picked.flatMap((k) => (k === "mixed" ? ["wet", "dry"] : [k])))];
  const size = document.querySelector("input[name=pSize]:checked")?.value;
  const severity = document.querySelector("input[name=pSev]:checked")?.value || "medium";
  const files = [...$("pPhoto").files];
  if (!streams.length) return err(t("gvp.pick_stream"));
  if (!size) return err(t("pub.pick_size"));
  if (files.length > CONFIG.gvp.max_photos) return err(t("gvp.too_many_photos", { n: CONFIG.gvp.max_photos }));
  $("pSend").disabled = true;
  try {
    const saved = await swmWrite("POST", `/api/pilots/${PILOT}/gvps`, {
      lon: lngLat.lng, lat: lngLat.lat, streams, size, severity, frequency: $("pFreq").value,
      landmark: $("pLandmark").value || null, note: $("pNote").value || null });
    let note = t(saved.merged ? "pub.merged" : "pub.thanks_new");
    for (const file of files) {
      try {
        await swmWrite("POST", `/api/pilots/${PILOT}/gvp-reports/${saved.report_id}/photos?lon=${lngLat.lng}&lat=${lngLat.lat}`, file, file.type || "image/jpeg");
      } catch (e) {
        note = t("gvp.photo_failed", { message: e.message });
        break;
      }
    }
    close();
    await loadGvps();
    toast(note);
  } catch (e) {
    err(e.message);
    $("pSend").disabled = false;
  }
}

$("signOut").addEventListener("click", signOut);
