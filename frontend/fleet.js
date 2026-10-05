// Fleet inventory: the vehicles that exist, per sector, and how many of each type a plan may use.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SESSION = getSession();
const CAN_EDIT = ["fleet_workforce_manager", "admin"].includes(SESSION?.role);
const SPECS = [["payload_kg", "kg"], ["body_volume_m3", "m³"], ["vehicle_width_m", "m"], ["min_road_width_m", "m"], ["compartments", ""]];
const STATUS_LABEL = { available: "Available", under_repair: "Under repair", off_road: "Off road", retired: "Retired" };
const STREAM_LABEL = { wet: "Wet", dry: "Dry", sanitary: "Sanitary", special: "Special care" };

let CLASSES = [], SECTORS = [], VEHICLES = [], editing = null;

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function load() {
  const d = await apiFetch(`/api/pilots/${PILOT}/fleet`);
  VEHICLES = d.vehicles;
  renderSummary();
  renderList();
}

function renderSummary() {
  if (!VEHICLES.length) {
    $("summary").innerHTML = `<p class="hint">No vehicles entered yet. Until the first one is added, route plans are not capped and are marked as using a hypothetical fleet.</p>`;
    return;
  }
  const types = CLASSES.filter((c) => VEHICLES.some((v) => v.type === c.key));
  const count = (sector, type) => VEHICLES.filter((v) => v.type === type && v.status === "available" && (!v.home_sector || v.home_sector === sector)).length;
  const pool = (type) => VEHICLES.filter((v) => v.type === type && v.status === "available" && !v.home_sector).length;
  $("summary").innerHTML = table(["Sector", ...types.map((c) => esc(c.label))],
    SECTORS.map((s) => [esc(s), ...types.map((c) => `${count(s, c.key)}`)]).concat([[`<span class="muted">of which shared pool</span>`, ...types.map((c) => `<span class="muted">${pool(c.key)}</span>`)]]));
}

function renderList() {
  const fs = $("filterSector").value, st = $("filterStatus").value;
  const rows = VEHICLES.filter((v) => (!fs || v.home_sector === fs || (fs === "_pool" && !v.home_sector)) && (!st || v.status === st));
  if (!rows.length) { $("list").innerHTML = `<p class="hint">No vehicles match.</p>`; return; }
  const spec = (v) => SPECS.map(([k, u]) => {
    const s = v.spec[k];
    return s.value == null ? "" : `<span class="${s.own ? "" : "muted"}" title="${s.own ? "This vehicle's own figure" : "Default for the type"}">${s.value}${u ? ` ${u}` : ""}</span>`;
  }).filter(Boolean).join(" · ");
  $("list").innerHTML = table(["Registration", "Type", "Based in", "Status", "Streams", "Specifications", "Last checked", ""],
    rows.map((v) => [`<b>${esc(v.registration)}</b>`, esc(v.label), esc(v.home_sector || "Shared pool"),
      CAN_EDIT ? `<select data-status="${v.id}">${Object.entries(STATUS_LABEL).map(([k, l]) => `<option value="${k}" ${k === v.status ? "selected" : ""}>${l}</option>`).join("")}</select>` : esc(STATUS_LABEL[v.status]),
      v.streams.map((s) => STREAM_LABEL[s]).join(", "), spec(v),
      v.verified_at ? `${esc(v.verified_at.slice(0, 10))}<br><span class="muted small">${esc(v.verified_by || "")}</span>` : `<span class="warn">Never</span>`,
      CAN_EDIT ? `<button type="button" class="secondary small" data-edit="${v.id}">Edit</button>` : ""]));
  $("list").querySelectorAll("[data-status]").forEach((el) => el.addEventListener("change", async () => {
    try {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/fleet/${el.dataset.status}`, { status: el.value });
      await load();
    } catch (err) { alert(err.message); }
  }));
  $("list").querySelectorAll("[data-edit]").forEach((el) => el.addEventListener("click", () => openForm(VEHICLES.find((v) => v.id === el.dataset.edit))));
}

function openForm(v) {
  editing = v || null;
  $("formTitle").textContent = v ? `Edit ${v.registration}` : "Add a vehicle";
  $("fSave").textContent = v ? "Save changes" : "Add vehicle";
  $("fCancel").hidden = !v;
  $("fType").value = v?.type || CLASSES[0].key;
  $("fReg").value = v?.registration || "";
  $("fHome").value = v?.home_sector || "";
  $("fStatus").value = v?.status || "available";
  document.querySelectorAll("#fStreams input").forEach((x) => (x.checked = v ? v.streams.includes(x.value) : true));
  SPECS.forEach(([k]) => ($(`f_${k}`).value = v && v.spec[k].own ? v.spec[k].value : ""));
  $("fNotes").value = v?.notes || "";
  $("fVerify").checked = false;
  $("formErr").hidden = true;
  showTypeDefaults();
  if (v) $("editor").scrollIntoView({ behavior: "smooth" });
}

function showTypeDefaults() {
  const c = CLASSES.find((x) => x.key === $("fType").value);
  const src = { payload_kg: "payload_kg", body_volume_m3: "body_volume_m3", vehicle_width_m: "vehicle_width_m", min_road_width_m: "min_road_width_m", compartments: "compartments" };
  SPECS.forEach(([k]) => ($(`f_${k}`).placeholder = c?.[src[k]]?.value != null ? `Type default: ${c[src[k]].value}` : ""));
}

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("formErr").hidden = true;
  const body = {
    type: $("fType").value, registration: $("fReg").value, home_sector: $("fHome").value || null, status: $("fStatus").value,
    streams: [...document.querySelectorAll("#fStreams input:checked")].map((x) => x.value), notes: $("fNotes").value,
  };
  SPECS.forEach(([k]) => (body[k] = $(`f_${k}`).value === "" ? null : +$(`f_${k}`).value));
  if (!body.streams.length) { $("formErr").textContent = "Tick at least one waste stream."; $("formErr").hidden = false; return; }
  try {
    if (editing) await swmWrite("PATCH", `/api/pilots/${PILOT}/fleet/${editing.id}`, { ...body, verify: $("fVerify").checked });
    else {
      const v = await swmWrite("POST", `/api/pilots/${PILOT}/fleet`, body);
      if ($("fVerify").checked) await swmWrite("PATCH", `/api/pilots/${PILOT}/fleet/${v.id}`, { verify: true });
    }
    openForm(null);
    await load();
  } catch (err) {
    $("formErr").textContent = err.message;
    $("formErr").hidden = false;
  }
});
$("fCancel").addEventListener("click", () => openForm(null));
$("fType").addEventListener("change", showTypeDefaults);
$("filterSector").addEventListener("change", renderList);
$("filterStatus").addEventListener("change", renderList);

(async () => {
  await UI_READY;
  $("who").textContent = SESSION ? `${SESSION.name} · ${t(`role.${SESSION.role}`)}` : "Not signed in";
  try {
    const [vehicles, sectors] = await Promise.all([apiFetch("/api/reference/vehicles"), apiFetch(`/api/pilots/${PILOT}/sectors`)]);
    CLASSES = vehicles.classes;
    SECTORS = sectors.features.map((f) => f.properties.name);
    $("fType").innerHTML = CLASSES.map((c) => `<option value="${c.key}">${esc(c.label)} (${c.tier === "primary" ? "door to door" : "truck"})</option>`).join("");
    $("fHome").innerHTML += SECTORS.map((s) => `<option>${esc(s)}</option>`).join("");
    $("filterSector").innerHTML += `<option value="_pool">Shared pool</option>` + SECTORS.map((s) => `<option>${esc(s)}</option>`).join("");
    $("fStatus").innerHTML = Object.entries(STATUS_LABEL).map(([k, l]) => `<option value="${k}">${l}</option>`).join("");
    $("filterStatus").innerHTML += Object.entries(STATUS_LABEL).map(([k, l]) => `<option value="${k}">${l}</option>`).join("");
    $("fStreams").innerHTML = Object.entries(STREAM_LABEL).map(([k, l]) => `<label><input type="checkbox" value="${k}" checked /> ${l}</label>`).join("");
    $("editor").hidden = !CAN_EDIT;
    $("readOnly").hidden = CAN_EDIT;
    if (CAN_EDIT) openForm(null);
    await load();
  } catch (err) {
    $("summary").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
})();
