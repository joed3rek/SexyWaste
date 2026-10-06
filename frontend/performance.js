// Performance: required vs delivered, the reasons for the gap, route accuracy, GVP response and planning alerts.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const WORK = { collection: "Collection", cleaning: "Street sweeping", gvp_clearing: "GVP clearing" };
const amount = (v, unit) => (unit === "kg" ? `${fmt(v / 1000, 2)} t` : unit === "m" ? `${fmt(v / 1000, 1)} km` : fmt(v));
const signed = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, 1)}%`);

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3500);
}

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function loadAlerts() {
  const sector = $("sector").value;
  const d = await apiFetch(`/api/pilots/${PILOT}/planning-alerts${sector ? `?sector=${encodeURIComponent(sector)}` : ""}`);
  $("alerts").innerHTML = d.alerts.length ? d.alerts.map((a) => `<div class="row-item op-row" data-alert="${a.id}">
      <p><span class="badge ${a.severity >= 3 ? "bad" : "warn"}">${esc(words(a.kind))}</span> <b>${esc(a.message)}</b> <span class="muted small">${esc(a.sector || "")}</span></p>
      <p class="small muted">Look into: ${a.causes.map(esc).join(" · ")}</p>
      <p><button type="button" class="secondary ack">Acknowledge</button> <button type="button" class="secondary res">Resolved</button></p></div>`).join("")
    : `<p class="hint">No open alerts.</p>`;
  document.querySelectorAll("[data-alert]").forEach((el) => {
    const set = async (status) => {
      try { await swmWrite("PATCH", `/api/pilots/${PILOT}/planning-alerts/${el.dataset.alert}`, { status }); loadAlerts(); } catch (err) { toast(err.message); }
    };
    el.querySelector(".ack").addEventListener("click", () => set("acknowledged"));
    el.querySelector(".res").addEventListener("click", () => set("resolved"));
  });
}

async function load() {
  const end = new Date(), start = new Date();
  start.setDate(end.getDate() - (+$("period").value - 1));
  const sector = $("sector").value;
  try {
    const p = await apiFetch(`/api/pilots/${PILOT}/performance?start=${iso(start)}&end=${iso(end)}${sector ? `&sector=${encodeURIComponent(sector)}` : ""}`);
    const rows = p.service.rows;
    $("service").innerHTML = rows.length ? table(["Sector", "Work", "Required", "Delivered", "Service level", "Gap", "No outcome yet", "Not planned", "Reasons"],
      rows.map((r) => [esc(r.sector), WORK[r.work] || esc(r.work), amount(r.required, r.unit), amount(r.delivered, r.unit),
        r.service_level_pct == null ? `<span class="muted">nothing recorded</span>` : `<b>${fmt(r.service_level_pct, 1)}%</b>`,
        r.gap ? `<span class="warn">${amount(r.gap, r.unit)}</span>` : "–", amount(r.unrecorded + r.pending, r.unit), fmt(r.not_planned),
        Object.entries(r.reasons).map(([k, n]) => `${esc(words(k))} (${n})`).join(", ") || "–"]))
      + `<p class="hint small">${esc(p.service.note)}</p>`
      : `<p class="hint">No service demands in this period. Demands are made when a day is planned (route builder, work plan) or generated.</p>`;
    $("causes").innerHTML = p.service.plans.length ? `<h3>Planning causes</h3><ul class="plan-exceptions">${p.service.plans.map((c) =>
      `<li><b>${esc(c.sector)}, ${esc(c.date)}</b> (${c.plan === "routes" ? "route plan" : "work plan"}): ${esc(c.reason)}</li>`).join("")}</ul>` : "";
    const types = Object.entries(p.routes.by_vehicle_type);
    $("routes").innerHTML = types.length ? table(["Vehicle type", "Routes recorded", "Distance vs plan", "Time vs plan", "Waste vs plan"],
      types.map(([t, v]) => [esc(words(t)), fmt(v.routes), signed(v.km_error_pct), signed(v.min_error_pct), signed(v.kg_error_pct)]))
      + `<p class="hint small">Positive: more than planned. These differences show where the estimates (speeds, service times, quantities) need correcting.</p>`
      : `<p class="hint">No routes recorded in this period. Record them on the <a href="operations.html">Operations</a> page.</p>`;
    const g = p.gvps;
    $("gvps").innerHTML = `<p>${fmt(g.cleared)} cleared in the period${g.cleared ? `, ${fmt(g.on_time_pct, 0)}% within the response target, on average ${fmt(g.mean_hours, 1)} h from verification to clearing` : ""}.</p>`
      + (g.recurring.length ? table(["GVP", "Sector", "Came back", "Status"], g.recurring.slice(0, 10).map((r) => [esc(r.gvp_id.slice(0, 8)), esc(r.sector), `${r.recurrences} time(s)`, esc(words(r.status))])) : "");
  } catch (err) {
    $("service").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
  loadAlerts();
}

UI_READY.then(async () => {
  const s = await apiFetch(`/api/pilots/${PILOT}/sectors`);
  s.features.forEach((f) => $("sector").add(new Option(f.properties.name, f.properties.name)));
  $("sector").addEventListener("change", load);
  $("period").addEventListener("change", load);
  $("scan").addEventListener("click", async () => {
    try {
      const r = await swmWrite("POST", `/api/pilots/${PILOT}/planning-alerts/scan`, { days: 14 });
      const n = Object.values(r.new).reduce((a, b) => a + b, 0);
      toast(n ? `${n} new alert(s).` : "No new repeated failures in the last 14 days.");
      loadAlerts();
    } catch (err) { toast(err.message); }
  });
  load();
});
