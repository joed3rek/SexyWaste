// Operations: record the day's actual routes and cleaning against the adopted plans.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const hm = (min) => (min == null ? "–" : `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")} min`);
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
let SHEET = null;

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

const reasonSelect = (cls) => `<select class="${cls}"><option value="">Reason (if not done as planned)</option>${SHEET.reasons.map((r) => `<option value="${r}">${esc(words(r))}</option>`).join("")}</select>`;
const recordedText = (r) => r ? `<span class="badge ${r.outcome === "done" ? "ok" : r.outcome === "partial" ? "warn" : "bad"}">${words(r.outcome)}</span>
  <span class="muted small">recorded by ${esc(r.recorded_by || "?")}${r.reason ? `, ${esc(words(r.reason))}` : ""}</span>` : `<span class="muted small">Not recorded yet</span>`;

function renderRoutes() {
  if (!SHEET.routes.length) {
    $("routes").innerHTML = `<p class="hint">No adopted route plan for this sector and day. Plan and adopt one in the <a href="builder.html">route builder</a>.</p>`;
    return;
  }
  $("routes").innerHTML = SHEET.routes.map((r) => `<div class="row-item op-row" data-route="${r.route_id}">
    <p><b>${esc(r.label)}</b> <span class="muted small">${r.tier === "primary" ? "door to door" : "truck to MRF"} · planned ${fmt(r.planned_km, 1)} km, ${hm(r.planned_min)}, ${fmt(r.planned_kg)} kg${r.tier === "primary" ? `, ${r.stops.length} stops` : ""}</span></p>
    <p>${recordedText(r.recorded)}${r.recorded ? ` <span class="muted small">actual ${fmt(r.recorded.actual_km, 1)} km, ${hm(r.recorded.actual_min)}, ${fmt(r.recorded.actual_kg)} kg</span>` : ""}</p>
    <div class="op-form">
      <label>km <input type="number" class="km" min="0" step="0.1" value="${r.planned_km}" /></label>
      <label>Minutes <input type="number" class="min" min="0" step="1" value="${Math.round(r.planned_min)}" /></label>
      <label>kg <input type="number" class="kg" min="0" step="1" value="${Math.round(r.planned_kg)}" /></label>
      ${r.tier === "primary" ? `<label>Stops missed <select class="missed" multiple size="3">${r.stops.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("")}</select></label>` : ""}
      ${reasonSelect("reason")}
      <button type="button" class="save">Record route</button>
    </div></div>`).join("");
  document.querySelectorAll(".op-row[data-route]").forEach((row) => row.querySelector(".save").addEventListener("click", async () => {
    const missed = [...(row.querySelector(".missed")?.selectedOptions || [])].map((o) => o.value);
    const reason = row.querySelector(".reason").value || null;
    try {
      const r = await swmWrite("POST", `/api/pilots/${PILOT}/operations/routes/${row.dataset.route}`, {
        outcome: missed.length ? "partial" : "done", reason, missed_points: missed,
        actual_km: row.querySelector(".km").value, actual_min: row.querySelector(".min").value, actual_kg: row.querySelector(".kg").value,
      });
      toast(r.demands.done + r.demands.missed ? `Recorded: ${r.demands.done} stop demand(s) done, ${r.demands.missed} missed.` : "Recorded.");
      load();
    } catch (err) { toast(err.message); }
  }));
}

function renderCleaning() {
  if (!SHEET.cleaning.length) { $("cleaning").innerHTML = `<p class="hint">No cleaning demands for this sector and day.</p>`; return; }
  $("cleaning").innerHTML = `<div class="scroll"><table class="results-table"><tr><th>Work</th><th>Planned</th><th>Recorded</th><th>Record</th></tr>` +
    SHEET.cleaning.map((c) => `<tr data-demand="${c.demand_id}"><td>${esc(c.label || words(c.source_type))}</td>
      <td>${c.planned_m ? `${fmt(c.planned_m)} m` : c.planned_kg ? `${fmt(c.planned_kg)} kg` : "–"}</td>
      <td>${recordedText(c.recorded)}</td>
      <td class="nowrap"><button type="button" class="secondary done">Done</button> ${reasonSelect("reason")} <button type="button" class="secondary missed">Missed</button></td></tr>`).join("") + `</table></div>`;
  document.querySelectorAll("tr[data-demand]").forEach((tr) => {
    const send = async (outcome) => {
      try {
        await swmWrite("POST", `/api/pilots/${PILOT}/operations/demands/${tr.dataset.demand}`, { outcome, reason: tr.querySelector(".reason").value || null });
        load();
      } catch (err) { toast(err.message); }
    };
    tr.querySelector(".done").addEventListener("click", () => send("done"));
    tr.querySelector(".missed").addEventListener("click", () => send("missed"));
  });
}

async function load() {
  try {
    SHEET = await apiFetch(`/api/pilots/${PILOT}/operations/day?sector=${encodeURIComponent($("sector").value)}&date=${$("day").value}`);
    renderRoutes();
    renderCleaning();
  } catch (err) {
    $("routes").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
}

UI_READY.then(async () => {
  const s = await apiFetch(`/api/pilots/${PILOT}/sectors`);
  $("sector").innerHTML = s.features.map((f) => `<option>${esc(f.properties.name)}</option>`).join("");
  $("day").value = today();
  $("sector").addEventListener("change", load);
  $("day").addEventListener("change", load);
  load();
});
