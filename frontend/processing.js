// Processing: facility intake, recovery, rejects and disposal against capacity and expected deliveries.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const words = (k) => String(k ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const STREAM = { wet: "Wet waste", dry: "Dry waste", sanitary: "Sanitary waste", special: "Special care waste" };
const KIND = { mrf: "MRF", compost_site: "Compost site", recycler: "Recycler", transfer_station: "Transfer station", disposal_site: "Disposal site", depot: "Depot", truck_yard: "Truck yard" };
let CFG = null;

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

async function load() {
  const end = new Date(), start = new Date();
  start.setDate(end.getDate() - (+$("period").value - 1));
  const s = await apiFetch(`/api/pilots/${PILOT}/processing?start=${iso(start)}&end=${iso(end)}`);
  $("facilities").innerHTML = s.facilities.length ? table(["Facility", "Capacity", "Received", "Average / day", "Use of capacity", "Recovered", "Rejects", "To disposal"],
    s.facilities.map((f) => [`${esc(f.name)} <span class="muted small">${esc(KIND[f.kind] || words(f.kind))}</span>`, f.capacity_t_day ? `${fmt(f.capacity_t_day, 1)} t/day` : `<span class="muted">not set</span>`,
      `${fmt(f.received_kg / 1000, 2)} t`, f.mean_t_day == null ? "–" : `${fmt(f.mean_t_day, 2)} t`,
      f.utilisation_pct == null ? "–" : `<span class="${f.days_over_capacity.length ? "warn" : ""}">${fmt(f.utilisation_pct, 0)}%${f.days_over_capacity.length ? `, over on ${f.days_over_capacity.length} day(s)` : ""}</span>`,
      f.recovery_pct == null ? "–" : `${fmt(f.recovery_pct, 1)}%`, `${fmt(f.rejects_kg)} kg`, `${fmt(f.disposal_kg)} kg`]))
    : `<p class="hint">No MRF or compost site yet. Place the MRF in the <a href="builder.html">route builder</a> and save the places.</p>`;
  const exp = Object.entries(s.expected_to_mrf_kg);
  $("expected").innerHTML = exp.length ? `<h3>Expected at the MRF (from adopted route plans)</h3>` + table(["Day", "Expected"], exp.map(([d, kg]) => [esc(d), `${fmt(kg / 1000, 2)} t`])) : "";
  const obs = s.facilities.find((f) => Object.keys(f.dry_fractions_observed).length);
  $("fractions").innerHTML = `<h3>Dry waste fractions: assumed and observed</h3>` + table(["Material", "Assumed share", "Observed share"],
    Object.entries(s.dry_fractions_assumed).map(([m, v]) => [esc(words(m)), `${fmt(100 * v)}%`, obs && obs.dry_fractions_observed[m] != null ? `${fmt(100 * obs.dry_fractions_observed[m], 1)}%` : "–"]))
    + `<p class="hint small">${esc(s.note)} Observed shares are recovered material over dry waste received; when they differ steadily from the assumed shares, the planning norms should be updated.</p>`;
  $("fac").innerHTML = s.facilities.map((f) => `<option value="${f.facility_id}">${esc(f.name)}</option>`).join("");
}

UI_READY.then(async () => {
  CFG = await apiFetch("/api/processing/config");
  $("stream").innerHTML = CFG.streams.map((x) => `<option value="${x}">${STREAM[x] || x}</option>`).join("");
  $("materials").innerHTML = CFG.materials.map((m) => `<label>${esc(words(m))} (kg) <input type="number" min="0" step="1" data-material="${m}" /></label>`).join("");
  $("day").value = iso(new Date());
  $("recordBox").hidden = !CFG.recorders.includes(getSession()?.role) && getSession()?.role !== "admin";
  $("period").addEventListener("change", load);
  $("intake").addEventListener("submit", async (e) => {
    e.preventDefault();
    const recovered = Object.fromEntries([...document.querySelectorAll("[data-material]")].filter((i) => i.value).map((i) => [i.dataset.material, +i.value]));
    try {
      await swmWrite("POST", `/api/pilots/${PILOT}/facilities/${$("fac").value}/intake`, {
        date: $("day").value, stream: $("stream").value, received_kg: $("received").value, recovered,
        rejects_kg: $("rejects").value || 0, disposal_kg: $("disposal").value || 0, basis: $("basis").value });
      toast("Recorded.");
      e.target.reset();
      $("day").value = iso(new Date());
      load();
    } catch (err) { toast(err.message); }
  });
  load();
});
