// Command centre: an exception and decision view over the connected records.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const PLAN = { feasible: ["ok", "feasible"], feasible_with_warnings: ["warn", "with warnings"], infeasible: ["bad", "infeasible"] };

const kpi = (k, v, sub, bad, href) => `<${href ? `a href="${href}"` : "div"} class="kpi${bad ? " bad" : ""}"><div class="k">${k}</div><div class="v">${v}</div>${sub ? `<div class="muted small">${sub}</div>` : ""}</${href ? "a" : "div"}>`;

function render(c) {
  $("today").textContent = `Command centre · ${new Date(`${c.date}T12:00:00`).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}`;
  const lv = c.city.service_level;
  const pct = (x) => (x.pct == null ? "–" : `${fmt(x.pct, 1)}%`);
  const sub = (x) => (x.pct == null ? "nothing recorded yet" : `${fmt(x.recorded_pct, 0)}% of the work recorded`);
  $("headline").innerHTML = kpi("Collection service level", pct(lv.collection), sub(lv.collection), lv.collection.pct != null && lv.collection.pct < c.target_pct, "performance.html")
    + kpi("Street cleaning service level", pct(lv.cleaning), sub(lv.cleaning), lv.cleaning.pct != null && lv.cleaning.pct < c.target_pct, "performance.html");
  const f = c.failures;
  $("failures").innerHTML = kpi("Open planning alerts", fmt(f.alerts), null, f.alerts > 0, "performance.html")
    + kpi("Recurring GVPs", fmt(f.recurring_gvps), "came back 2+ times", f.recurring_gvps > 0, "citymap.html")
    + kpi("Critical GVPs open", fmt(f.critical_gvps), null, f.critical_gvps > 0, "cleancity.html")
    + kpi("Bins full or overflowing", fmt(f.bins_full_or_overflowing), null, f.bins_full_or_overflowing > 0, "cleancity.html")
    + kpi("Sectors below target", fmt(f.sectors_below_target.length), f.sectors_below_target.join(", ") || `target ${c.target_pct}%`, f.sectors_below_target.length > 0)
    + kpi("Workforce short", `${fmt(f.workforce_short_pct, 0)}%`, "cleaning hours, where staff are entered", f.workforce_short_pct > 0, "resources.html?tab=staff");
  $("alerts").innerHTML = c.top_alerts.length ? `<ul class="plan-exceptions">${c.top_alerts.map((a) => `<li><b>${esc(a.message)}</b> <span class="muted small">${esc(a.sector || "")}</span></li>`).join("")}</ul>` : "";
  const t = c.today;
  $("todayKpis").innerHTML = kpi("Urgent work open", fmt(t.urgent_open), "high or critical GVPs, bins to empty", t.urgent_open > 0, "cleancity.html")
    + kpi("Missed today", fmt(t.missed), null, t.missed > 0, "operations.html")
    + kpi("GVPs on the street", fmt(t.open_gvps), null, t.open_gvps > 0, "cleancity.html")
    + kpi("Agent plans to decide", fmt(c.agent_pending), "recommended, awaiting approval", c.agent_pending > 0, "agent.html")
    + kpi("Sectors without a route plan", fmt(t.sectors_without_route_plan.length), t.sectors_without_route_plan.join(", "), t.sectors_without_route_plan.length > 0, "builder.html");
  $("sectors").innerHTML = c.sectors.map((s) => {
    const badge = (p, label) => (p ? `<span class="badge ${PLAN[p][0]}">${label} ${PLAN[p][1]}</span>` : `<span class="badge">${label}: none</span>`);
    const lvl = Object.entries(s.service_level).map(([k, v]) => `${k === "collection" ? "collection" : k === "cleaning" ? "sweeping" : "GVPs"} ${fmt(v, 0)}%`).join(", ");
    return `<div class="card-box cmd-sector">
      <p><b>${esc(s.sector)}</b> ${badge(s.route_plan, "Routes")} ${badge(s.work_plan, "Cleaning")}</p>
      <p class="small">${fmt(s.collection_t, 1)} t to collect at ${fmt(s.collection_demands)} demand(s) · ${fmt(s.sweeping_km, 1)} km to sweep${s.gvps_to_clear ? ` · ${s.gvps_to_clear} GVP(s) to clear` : ""}</p>
      <p class="small muted">Today: ${fmt(s.status.open)} open, ${fmt(s.status.planned)} planned, ${fmt(s.status.done)} done, ${fmt(s.status.missed)} missed${lvl ? ` · last 7 days: ${lvl}` : ""}
        · cleaning needs ${fmt(s.cleaning_hours.required)} worker-hours${s.cleaning_hours.available != null ? `, ${fmt(s.cleaning_hours.available)} available` : ""}</p>
      ${s.why.length ? `<ul class="plan-exceptions">${s.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : `<p class="small">Nothing needs attention.</p>`}
    </div>`;
  }).join("");
}

UI_READY.then(async () => {
  $("sectors").innerHTML = `<p class="hint">Reading today's demands, plans and records…</p>`;
  try {
    render(await apiFetch(`/api/pilots/${PILOT}/command`));
  } catch (err) {
    $("sectors").innerHTML = `<p class="warn">${esc(err.message)}</p>`;
  }
});
