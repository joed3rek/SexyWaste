// Resource management: vehicles, people and machinery, and what each sector may plan with.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SESSION = getSession();
let CAN_EDIT = false;  // for the open tab, from the server's editors per inventory
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const STREAM = { wet: "Wet", dry: "Dry", sanitary: "Sanitary", special: "Special care" };

let CFG = null, SECTORS = [], ITEMS = { vehicle: [], staff: [], equipment: [] }, tab = "vehicle", editing = null, GPS = null;

// Form fields per kind: [field, label, input type, options].
function fields(kind) {
  const opt = (list, blank) => (blank ? [["", blank]] : []).concat(list.map((k) => [k, words(k)]));
  const common = [
    ["home_sector", "Based in", "select", [["", "Shared pool (all sectors)"], ...SECTORS.map((s) => [s, s])]],
    ["status", "Status", "select", opt(CFG.statuses)],
    ["shift", "Shift", "select", opt(CFG.shifts, "Not set")],
  ];
  if (kind === "vehicle") return [
    ["type", "Vehicle type", "select", CFG.vehicle_classes.map((c) => [c.key, `${c.label} (${c.tier === "primary" ? "door to door" : "truck"})`])],
    ["registration", "Registration or vehicle ID", "text"], ...common,
    ["streams", "Waste streams it may carry", "streams"],
    ["gps", "Has a working GPS tracker", "check"],
    ["drivers_required", "Drivers needed", "number", "crew.drivers"], ["collectors_required", "Collectors needed", "number", "crew.collectors"],
    ["payload_kg", "Payload (kg)", "number", "specs.payload_kg"], ["body_volume_m3", "Body volume (m³)", "number", "specs.body_volume_m3"],
    ["compartments", "Compartments", "number", "specs.compartments"], ["min_road_width_m", "Narrowest road it can work (m)", "number", "specs.min_road_width_m"],
  ];
  if (kind === "staff") return [
    ["worker_id", "Worker ID", "text"], ["name", "Name", "text"], ["role", "Role", "select", opt(CFG.staff_roles)], ...common,
    ["hours_per_day", "Working hours per day", "number"], ["skills", "Skills (comma separated)", "text"],
    ["supervisor", "Supervisor", "text"], ["assignment", "Current assignment", "text"],
  ];
  return [
    ["equipment_id", "Equipment ID", "text"], ["type", "Type", "select", opt(CFG.equipment_types)], ...common,
    ["capacity", "Capacity", "number"], ["capacity_unit", "Capacity unit (e.g. kg, m³, litres)", "text"],
    ["condition", "Condition", "select", opt(CFG.conditions, "Not checked")],
    ["operator_role", "Operator needed", "select", opt(CFG.staff_roles, "No operator")],
    ["last_service", "Last service", "date"], ["next_service", "Next service due", "date"],
  ];
}

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function load() {
  const [v, s, e] = await Promise.all(["vehicle", "staff", "equipment"].map((k) => apiFetch(`/api/pilots/${PILOT}/resources/${k}`)));
  ITEMS = { vehicle: v.items, staff: s.items, equipment: e.items };
  GPS = v.gps_rule;
  renderSummary();
  renderList();
}

function renderSummary() {
  const usable = (kind, sector) => ITEMS[kind].filter((x) => CFG.plannable.includes(x.status) && (!x.home_sector || x.home_sector === sector));
  const types = CFG.vehicle_classes.filter((c) => ITEMS.vehicle.some((v) => v.type === c.key));
  const roles = CFG.staff_roles.filter((r) => ITEMS.staff.some((s) => s.role === r));
  if (!types.length && !roles.length && !ITEMS.equipment.length) {
    $("summary").innerHTML = `<p class="hint">Nothing entered yet. Plans are not capped until vehicles or people are added.</p>`;
  } else {
    $("summary").innerHTML = table(["Sector", ...types.map((c) => esc(c.label)), ...roles.map((r) => `${esc(words(r))}s`), "Machinery"],
      SECTORS.map((sec) => [esc(sec), ...types.map((c) => usable("vehicle", sec).filter((v) => v.type === c.key).length),
        ...roles.map((r) => { const p = usable("staff", sec).filter((s) => s.role === r); return `${p.length} <span class="muted small">(${p.reduce((a, s) => a + s.hours_per_day, 0)} h)</span>`; }),
        usable("equipment", sec).length]));
  }
  const noGps = ITEMS.vehicle.filter((v) => CFG.plannable.includes(v.status) && !v.gps).length;
  $("gpsNote").innerHTML = GPS && GPS.required && noGps
    ? `<span class="warn">${noGps} working vehicle(s) without GPS. SWM Rules 2026, r. ${esc(GPS.rule)}: ${esc(GPS.text)}</span>` : "";
}

function cell(kind, x) {
  if (kind === "vehicle") return [`<b>${esc(x.registration)}</b>`, esc(x.label), x.streams.map((s) => STREAM[s]).join(", "),
    `${x.crew.drivers} driver, ${x.crew.collectors} collector${x.crew.collectors === 1 ? "" : "s"}`, x.gps ? "Yes" : `<span class="warn">No</span>`];
  if (kind === "staff") return [`<b>${esc(x.worker_id)}</b>`, esc(x.name), esc(words(x.role)), `${x.hours_per_day} h`, esc(x.supervisor || "–")];
  return [`<b>${esc(x.equipment_id)}</b>`, esc(words(x.type)), x.capacity ? `${x.capacity} ${esc(x.capacity_unit || "")}` : "–",
    esc(words(x.condition || "–")), x.next_service ? `${esc(x.next_service)}${x.service_due ? ' <span class="warn">due</span>' : ""}` : "–"];
}

const HEAD = {
  vehicle: ["ID", "Type", "Streams", "Crew", "GPS"],
  staff: ["Worker ID", "Name", "Role", "Hours", "Supervisor"],
  equipment: ["ID", "Type", "Capacity", "Condition", "Next service"],
};

function renderList() {
  const fs = $("filterSector").value, st = $("filterStatus").value;
  const rows = ITEMS[tab].filter((x) => (!fs || x.home_sector === fs || (fs === "_pool" && !x.home_sector)) && (!st || x.status === st));
  if (!rows.length) { $("list").innerHTML = `<p class="hint">None yet.</p>`; return; }
  $("list").innerHTML = table([...HEAD[tab], "Based in", "Shift", "Status", "Last checked", ""],
    rows.map((x) => [...cell(tab, x), esc(x.home_sector || "Shared pool"), esc(words(x.shift || "–")),
      CAN_EDIT ? `<select data-status="${x.id}">${CFG.statuses.map((k) => `<option value="${k}" ${k === x.status ? "selected" : ""}>${words(k)}</option>`).join("")}</select>` : esc(words(x.status)),
      x.verified_at ? `${esc(x.verified_at.slice(0, 10))}<br><span class="muted small">${esc(x.verified_by || "")}</span>` : `<span class="warn">Never</span>`,
      CAN_EDIT ? `<button type="button" class="secondary small" data-edit="${x.id}">Edit</button>` : ""]));
  $("list").querySelectorAll("[data-status]").forEach((el) => el.addEventListener("change", async () => {
    try {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/resources/${tab}/${el.dataset.status}`, { status: el.value });
      await load();
    } catch (err) { alert(err.message); }
  }));
  $("list").querySelectorAll("[data-edit]").forEach((el) => el.addEventListener("click", () => openForm(ITEMS[tab].find((x) => x.id === el.dataset.edit))));
}

function defaultOf(path) {
  const c = CFG.vehicle_classes.find((x) => x.key === $("f_type")?.value);
  if (!c || !path) return null;
  return path.split(".").reduce((o, k) => (o ? o[k] : null), c);
}

function openForm(x) {
  editing = x || null;
  const kind = tab, label = { vehicle: "vehicle", staff: "person", equipment: "machinery" }[kind];
  $("formTitle").textContent = x ? `Edit ${x.code}` : `Add ${label}`;
  $("form").innerHTML = fields(kind).map(([f, lab, type, opts]) => {
    const val = x ? x[f] : undefined;
    if (type === "select") return `<label>${esc(lab)}<select id="f_${f}">${opts.map(([k, l]) => `<option value="${esc(k)}" ${String(val ?? "") === String(k) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    if (type === "check") return `<label class="check"><input type="checkbox" id="f_${f}" ${val ? "checked" : ""} /> ${esc(lab)}</label>`;
    if (type === "streams") return `<fieldset class="streams"><legend>${esc(lab)}</legend><div>${Object.entries(STREAM).map(([k, l]) => `<label><input type="checkbox" name="f_streams" value="${k}" ${!x || x.streams.includes(k) ? "checked" : ""} /> ${l}</label>`).join("")}</div></fieldset>`;
    const shown = f === "skills" && Array.isArray(val) ? val.join(", ") : val ?? "";
    return `<label>${esc(lab)}<input id="f_${f}" type="${type === "number" ? "number" : type === "date" ? "date" : "text"}" ${type === "number" ? 'step="any" min="0"' : ""} value="${esc(shown)}" data-default="${esc(opts || "")}" /></label>`;
  }).join("") + `
    <label>Notes<textarea id="f_notes" rows="2" maxlength="500">${esc(x?.notes || "")}</textarea></label>
    <label class="check"><input type="checkbox" id="fVerify" /> I have checked it today</label>
    <p id="formErr" class="warn" hidden></p>
    <div class="row"><button type="submit">${x ? "Save changes" : "Add"}</button>${x ? '<button type="button" class="secondary" id="fCancel">Cancel</button>' : ""}</div>`;
  if (kind === "vehicle" && x) {  // show the vehicle's own figures, blank where it uses the type's
    ["drivers_required", "collectors_required"].forEach((f) => ($(`f_${f}`).value = x[f] ?? ""));
    ["payload_kg", "body_volume_m3", "compartments", "min_road_width_m"].forEach((f) => ($(`f_${f}`).value = x.spec[f].own ? x.spec[f].value : ""));
  }
  const placeholders = () => document.querySelectorAll("#form [data-default]").forEach((el) => {
    const d = defaultOf(el.dataset.default);
    el.placeholder = d != null && d !== "" ? `Type default: ${d}` : "";
  });
  $("f_type")?.addEventListener("change", placeholders);
  placeholders();
  $("fCancel")?.addEventListener("click", () => openForm(null));
  if (x) $("editor").scrollIntoView({ behavior: "smooth" });
}

$("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("formErr").hidden = true;
  const body = { notes: $("f_notes").value };
  fields(tab).forEach(([f, , type]) => {
    if (type === "streams") body.streams = [...document.querySelectorAll("input[name=f_streams]:checked")].map((x) => x.value);
    else if (type === "check") body[f] = $(`f_${f}`).checked;
    else if (type === "number") body[f] = $(`f_${f}`).value === "" ? null : +$(`f_${f}`).value;
    else if (f === "skills") body[f] = $(`f_${f}`).value.split(",").map((s) => s.trim()).filter(Boolean);
    else body[f] = $(`f_${f}`).value || null;
  });
  if (tab === "staff" && body.hours_per_day == null) delete body.hours_per_day;
  body.verify = $("fVerify").checked;
  try {
    if (editing) await swmWrite("PATCH", `/api/pilots/${PILOT}/resources/${tab}/${editing.id}`, body);
    else await swmWrite("POST", `/api/pilots/${PILOT}/resources/${tab}`, body);
    openForm(null);
    await load();
  } catch (err) {
    $("formErr").textContent = err.message;
    $("formErr").hidden = false;
  }
});

function showTab(name) {
  tab = name;
  document.querySelectorAll("[data-tab]").forEach((x) => x.classList.toggle("on", x.dataset.tab === name));
  CAN_EDIT = (CFG.editors[name] || []).includes(SESSION?.role);
  $("editor").hidden = !CAN_EDIT;
  $("readOnly").hidden = CAN_EDIT;
  $("readOnly").textContent = name === "staff" ? "Only the human resource manager or an admin can change people."
    : "Only the fleet manager or an admin can change vehicles and machinery.";
  renderList();
  if (CAN_EDIT) openForm(null);
}
document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("filterSector").addEventListener("change", renderList);
$("filterStatus").addEventListener("change", renderList);

(async () => {
  await UI_READY;
  $("who").textContent = SESSION ? `${SESSION.name} · ${t(`role.${SESSION.role}`)}` : "";
  try {
    const [cfg, sectors] = await Promise.all([apiFetch("/api/resources/config"), apiFetch(`/api/pilots/${PILOT}/sectors`)]);
    CFG = cfg;
    SECTORS = sectors.features.map((f) => f.properties.name);
    $("filterSector").innerHTML += SECTORS.map((s) => `<option>${esc(s)}</option>`).join("");
    $("filterStatus").innerHTML += CFG.statuses.map((k) => `<option value="${k}">${words(k)}</option>`).join("");
    await load();
    const wanted = new URLSearchParams(location.search).get("tab");
    showTab(["vehicle", "staff", "equipment"].includes(wanted) ? wanted : SESSION?.role === "hr_manager" ? "staff" : "vehicle");
  } catch (err) {
    $("summary").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
})();
