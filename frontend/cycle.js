// Collection cycle: the planner's schedules of when each stream is collected from whom.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SESSION = getSession();
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const STREAM = { wet: "Wet waste", dry: "Dry waste", sanitary: "Sanitary waste", special: "Special care waste" };
const DAY = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

let CFG = null, DATA = null, SECTORS = [], VEHICLES = [], editing = null, CAN_EDIT = false;

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function load() {
  DATA = await apiFetch(`/api/pilots/${PILOT}/cycle`);
  const active = DATA.entries.filter((e) => e.active);
  $("empty").hidden = active.length > 0 || !CAN_EDIT;
  $("checks").innerHTML = DATA.checks.map((c) => `<p class="warn">${esc(c.sector)}: no collection for ${c.missing.map((m) => `${STREAM[m.stream].toLowerCase()} from ${words(m.generator).toLowerCase()}`).join(", ")} (SWM Rules 2026, r. ${esc(c.rule)}: collection at regular intervals).</p>`).join("");
  renderWeek();
  renderEntries();
}

// The entry in force for a sector, stream and generator type: the sector's own, else every sector's.
function inForce(sector, stream, gen) {
  const act = DATA.entries.filter((e) => e.active && e.stream === stream && e.generator === gen);
  return act.find((e) => e.sector === sector) || act.find((e) => !e.sector);
}

function renderWeek() {
  const sector = $("sector").value;
  const rows = [];
  CFG.streams.forEach((s) => CFG.generators.forEach((g) => {
    const e = inForce(sector, s, g);
    rows.push([`<b>${esc(STREAM[s])}</b><br><span class="muted small">${esc(words(g))}</span>`,
      ...CFG.days.map((d) => (e && e.days.includes(d) ? `<span class="tick" title="${e.start}–${e.end}">●</span>` : "")),
      e ? `${e.start}–${e.end}${e.sector ? ' <span class="muted small">(own)</span>' : ""}` : `<span class="warn">Not collected</span>`]);
  }));
  $("weekGrid").innerHTML = table(["", ...CFG.days.map((d) => DAY[d]), "Window"], rows);
}

function renderEntries() {
  const list = DATA.entries;
  if (!list.length) { $("entries").innerHTML = `<p class="hint">None yet.</p>`; return; }
  $("entries").innerHTML = table(["Stream", "Generator type", "Sector", "Days", "Window", "Method", "Vehicles", "Active", ""],
    list.map((e) => [esc(STREAM[e.stream]), esc(words(e.generator)), esc(e.sector || "Every sector"), e.days.map((d) => DAY[d]).join(", "),
      `${e.start}–${e.end} <span class="muted small">(${e.hours} h)</span>`, esc(words(e.method)),
      e.vehicle_types.length ? e.vehicle_types.map((k) => esc(VEHICLES.find((v) => v.key === k)?.label || k)).join(", ") : "Any",
      CAN_EDIT ? `<input type="checkbox" data-active="${e.id}" ${e.active ? "checked" : ""} />` : (e.active ? "Yes" : "No"),
      CAN_EDIT && e.active ? `<button type="button" class="secondary small" data-edit="${e.id}">Edit</button>` : ""]));
  $("entries").querySelectorAll("[data-active]").forEach((el) => el.addEventListener("change", async () => {
    try {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/cycle/${el.dataset.active}`, { active: el.checked });
    } catch (err) { alert(err.message); }
    await load();
  }));
  $("entries").querySelectorAll("[data-edit]").forEach((el) => el.addEventListener("click", () => openForm(DATA.entries.find((e) => e.id === el.dataset.edit))));
}

function openForm(e) {
  editing = e || null;
  $("formTitle").textContent = e ? "Edit entry" : "Add an entry";
  $("fSave").textContent = e ? "Save changes" : "Add entry";
  $("fCancel").hidden = !e;
  $("fStream").value = e?.stream || CFG.streams[0];
  $("fGen").value = e?.generator || CFG.generators[0];
  $("fSector").value = e?.sector || "";
  document.querySelectorAll("#fDays input").forEach((x) => (x.checked = e ? e.days.includes(x.value) : false));
  $("fStart").value = e?.start || "06:00";
  $("fEnd").value = e?.end || "11:00";
  $("fMethod").value = e?.method || "door_to_door";
  document.querySelectorAll("#fVehicles input").forEach((x) => (x.checked = e ? e.vehicle_types.includes(x.value) : false));
  ["fStream", "fGen", "fSector"].forEach((id) => ($(id).disabled = !!e));  // what the entry is about stays fixed
  $("formErr").hidden = true;
  if (e) $("editor").scrollIntoView({ behavior: "smooth" });
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    days: [...document.querySelectorAll("#fDays input:checked")].map((x) => x.value), start: $("fStart").value, end: $("fEnd").value,
    method: $("fMethod").value, vehicle_types: [...document.querySelectorAll("#fVehicles input:checked")].map((x) => x.value),
  };
  try {
    if (editing) await swmWrite("PATCH", `/api/pilots/${PILOT}/cycle/${editing.id}`, body);
    else await swmWrite("POST", `/api/pilots/${PILOT}/cycle`, { ...body, stream: $("fStream").value, generator: $("fGen").value, sector: $("fSector").value || null });
    openForm(null);
    await load();
  } catch (err) {
    $("formErr").textContent = err.message;
    $("formErr").hidden = false;
  }
});
$("fCancel").addEventListener("click", () => openForm(null));
$("sector").addEventListener("change", renderWeek);
$("useTemplate").addEventListener("click", async () => {
  try {
    await swmWrite("POST", `/api/pilots/${PILOT}/cycle/template`);
    await load();
  } catch (err) { alert(err.message); }
});

(async () => {
  await UI_READY;
  try {
    const [cfg, sectors, res] = await Promise.all([apiFetch("/api/cycle/config"), apiFetch(`/api/pilots/${PILOT}/sectors`), apiFetch("/api/resources/config")]);
    CFG = cfg;
    CAN_EDIT = CFG.editors.includes(SESSION?.role);
    SECTORS = sectors.features.map((f) => f.properties.name);
    VEHICLES = res.vehicle_classes;
    $("rules").innerHTML = CFG.rules.map((r) => `SWM Rules 2026, r. ${esc(r.rule)}: ${esc(r.text)}`).join("<br>");
    $("sector").innerHTML = SECTORS.map((s) => `<option>${esc(s)}</option>`).join("");
    $("fStream").innerHTML = CFG.streams.map((s) => `<option value="${s}">${STREAM[s]}</option>`).join("");
    $("fGen").innerHTML = CFG.generators.map((g) => `<option value="${g}">${words(g)}</option>`).join("");
    $("fSector").innerHTML += SECTORS.map((s) => `<option>${esc(s)}</option>`).join("");
    $("fMethod").innerHTML = CFG.methods.map((m) => `<option value="${m}">${words(m)}</option>`).join("");
    $("fDays").innerHTML = CFG.days.map((d) => `<label><input type="checkbox" value="${d}" /> ${DAY[d]}</label>`).join("");
    $("fVehicles").innerHTML = VEHICLES.map((v) => `<label><input type="checkbox" value="${v.key}" /> ${esc(v.label)}</label>`).join("");
    $("editor").hidden = !CAN_EDIT;
    $("readOnly").hidden = CAN_EDIT;
    await load();
  } catch (err) {
    $("checks").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
})();
