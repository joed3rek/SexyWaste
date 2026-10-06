// Planning agent: status, recommendations awaiting approval, reviews and history.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const hm = (min) => (min == null ? "–" : `${Math.floor(min / 60)} h ${String(Math.round(min % 60)).padStart(2, "0")}`);
const CHECK = { pass: "ok", warn: "warn", fail: "bad", unknown: "" };
const STATUS = { recommended: "Recommended, awaiting approval", no_feasible_plan: "No feasible plan", insufficient_data: "Insufficient data",
  nothing_to_plan: "Nothing to plan", approved: "Approved", rejected: "Rejected", superseded: "Superseded", running: "Running",
  reviewed: "Reviewed", failed: "Failed" };
let timer = null;

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

function table(head, rows) {
  return `<table class="results-table"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

function runCard(r) {
  const rec = r.recommendation || {};
  const checks = r.checks.map((c) => `<li><span class="badge ${CHECK[c.status]}">${esc(c.status)}</span> <b>${esc(c.name)}</b>
    <span class="muted small">(${esc(c.kind)})</span> ${esc(c.detail)}</li>`).join("");
  const sc = r.scenarios.map((s) => {
    const m = s.metrics;
    const mark = s.plan_id && s.plan_id === r.plan_id ? " <span class=\"badge ok\">recommended</span>" : "";
    if (!m) return [`${esc(s.label)}${mark}`, `<span class="muted">${esc(s.not_run || "not run")}</span>`, "", "", "", "", "", "", ""];
    return [`${esc(s.label)}${mark}`, `${fmt(m.coverage_pct, 1)}%`, `${fmt(m.km, 1)} km`, `${hm(m.duration_min)} of ${hm(m.window_min)}`,
      m.overtime_min ? `<span class="warn">${fmt(m.overtime_min)} min</span>` : "–", `${m.vehicles} + ${m.trucks}`,
      m.mean_fill_pct == null ? "–" : `${m.mean_fill_pct}%`, esc(m.risk), m.feasibility === "infeasible" ? `<span class="warn">infeasible</span>` : esc(m.feasibility.replace(/_/g, " "))];
  });
  const comp = rec.comparison ? `<p class="small">Against ${esc(rec.comparison.against)}: ${rec.comparison.km > 0 ? "+" : ""}${fmt(rec.comparison.km, 1)} km,
    ${rec.comparison.duration_min > 0 ? "+" : ""}${fmt(rec.comparison.duration_min)} min, ${rec.comparison.coverage_pct > 0 ? "+" : ""}${fmt(rec.comparison.coverage_pct, 1)}% coverage.</p>` : "";
  const alts = r.scenarios.filter((s) => s.plan_id && s.plan_id !== r.plan_id);
  const decide = r.status === "recommended" || r.status === "no_feasible_plan"
    ? `<div class="op-form">${r.plan_id ? `<button type="button" class="approve" data-plan="${r.plan_id}" data-infeasible="${rec.feasible ? "" : "1"}">${rec.feasible ? "Approve and adopt" : "Adopt the closest anyway"}</button>` : ""}
       ${alts.length ? `<select class="alt"><option value="">…or choose another plan</option>${alts.map((s) => `<option value="${s.plan_id}">${esc(s.label)}</option>`).join("")}</select><button type="button" class="secondary approveAlt">Adopt chosen</button>` : ""}
       <button type="button" class="secondary reject">Reject</button></div>` : "";
  return `<div class="card-box agent-run" data-run="${r.id}">
    <p><b>${esc(r.sector)}, ${esc(r.service_date)}</b> <span class="badge ${r.status === "recommended" ? "ok" : r.status === "no_feasible_plan" ? "bad" : ""}">${esc(STATUS[r.status] || r.status)}</span>
      <span class="muted small">${esc(r.trigger)}${r.trigger_note ? `: ${esc(r.trigger_note)}` : ""} · settings v${esc(r.config_version)}</span></p>
    ${rec.summary ? `<h3>${esc(rec.summary)}</h3>` : ""}
    ${rec.reasons?.length ? `<ul>${rec.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}${comp}
    <details><summary>Checks before routing</summary><ul class="plan-exceptions">${checks}</ul></details>
    ${sc.length ? `<details open><summary>Scenarios tried</summary><div class="scroll">${table(["Scenario", "Coverage", "Distance", "Time", "Overtime", "Vehicles + trucks", "Fill", "Risk", "Feasibility"], sc)}</div></details>` : ""}
    ${rec.risks?.length ? `<details><summary>Risks (${rec.risks.length})</summary><ul class="plan-exceptions">${rec.risks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
    ${rec.alternatives?.length ? `<details><summary>Why not the others</summary><ul class="plan-exceptions">${rec.alternatives.map((a) => `<li><b>${esc(a.label)}</b>: ${esc(a.why_not)}</li>`).join("")}</ul></details>` : ""}
    ${r.error ? `<p class="warn">${esc(r.error)}</p>` : ""}${decide}</div>`;
}

function reviewCard(r) {
  const f = r.findings || {};
  const routes = (f.routes || []).map((x) => [esc(x.sector), esc(x.route), Object.entries(x.deviation_pct).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${fmt(v, 0)}%`).join(", ") || "–",
    x.cause === "UNKNOWN" ? `<b>UNKNOWN</b>` : esc(x.cause), `<span class="muted small">${esc(x.evidence)}</span>`]);
  return `<div class="card-box agent-run"><p><b>Review of ${esc(r.service_date)}</b> <span class="muted small">${esc(r.trigger)}</span></p>
    ${f.data ? `<p class="hint">${esc(f.data)}</p>` : ""}
    ${routes.length ? table(["Sector", "Route", "Against plan", "Cause", "Evidence"], routes) : ""}
    ${f.unrecorded_routes?.length ? `<p class="small muted">${f.unrecorded_routes.length} adopted route(s) not recorded: they cannot be reviewed.</p>` : ""}
    ${Object.keys(f.missed_by_reason || {}).length ? `<p class="small">Missed work by recorded reason: ${Object.entries(f.missed_by_reason).map(([k, n]) => `${esc(k.replace(/_/g, " "))} (${n})`).join(", ")}</p>` : ""}
    ${f.patterns?.length ? `<h3>Patterns in the history</h3><ul class="plan-exceptions">${f.patterns.map((p) => `<li>${esc(p.proposal)} <span class="muted small">(${p.confidence} confidence)</span></li>`).join("")}</ul>` : `<p class="small muted">No systematic pattern yet: not enough recorded routes, or none beyond the threshold.</p>`}</div>`;
}

async function load() {
  const [st, list] = await Promise.all([apiFetch(`/api/pilots/${PILOT}/agent`), apiFetch(`/api/pilots/${PILOT}/agent/runs`)]);
  const s = st.config.schedule;
  const state = !s.enabled ? "Schedule off (reference/agent.json)" : st.loop_running ? "Running on schedule" : "Schedule set, but not running in this server (CITYLOOM_AGENT=off)";
  $("status").innerHTML = `<p><b>${state}</b> · plans the next day at ${esc(s.plan_next_day_at)},
    reviews the day at ${esc(s.review_day_at)}, checks adopted plans every ${s.check_conditions_every_min} min · settings v${esc(st.config.version)}
    (reference/agent.json)</p><p class="small muted">${st.queue.length ? `${st.queue.length} run(s) queued or in progress.` : "Nothing queued."}
    ${st.pending_approval} recommendation(s) waiting for a decision.</p>`;
  const plans = list.runs.filter((r) => r.kind === "plan");
  const open = plans.filter((r) => r.status === "recommended" || r.status === "no_feasible_plan" || r.status === "running" || r.status === "insufficient_data");
  $("pending").innerHTML = open.length ? open.map(runCard).join("") : `<p class="hint">Nothing waiting. Ask the agent to plan a day above.</p>`;
  const reviews = list.runs.filter((r) => r.kind === "review" && r.status === "reviewed").slice(0, 3);
  $("reviews").innerHTML = reviews.length ? reviews.map(reviewCard).join("") : `<p class="hint">No reviews yet.</p>`;
  $("history").innerHTML = table(["Started", "Kind", "Day", "Sector", "Trigger", "Status", "Decided by"],
    list.runs.map((r) => [esc(r.started_at.slice(0, 16).replace("T", " ")), esc(r.kind), esc(r.service_date), esc(r.sector || "all"), esc(r.trigger),
      esc(STATUS[r.status] || r.status), esc(r.decided_by || "")]));
  document.querySelectorAll("[data-run]").forEach((el) => {
    const send = async (path, body, done) => {
      try { await swmWrite("POST", `/api/pilots/${PILOT}/agent/runs/${el.dataset.run}/${path}`, body); toast(done); load(); } catch (err) { toast(err.message); }
    };
    el.querySelector(".approve")?.addEventListener("click", (e) => {
      const inf = e.target.dataset.infeasible === "1";
      if (inf && !confirm("This plan leaves demand unserved or runs past its window. Adopt it anyway?")) return;
      send("approve", { plan_id: e.target.dataset.plan, accept_exceptions: inf }, "Approved: the plan is adopted.");
    });
    el.querySelector(".approveAlt")?.addEventListener("click", () => {
      const pid = el.querySelector(".alt").value;
      if (!pid) return toast("Choose a plan first.");
      send("approve", { plan_id: pid, accept_exceptions: confirm("Adopt this plan even if it has exceptions?") }, "Approved: the chosen plan is adopted.");
    });
    el.querySelector(".reject")?.addEventListener("click", () => send("reject", { note: prompt("Why reject? (optional)") || null }, "Rejected."));
  });
  clearTimeout(timer);
  if (st.queue.length || plans.some((r) => r.status === "running")) timer = setTimeout(load, 5000);
}

UI_READY.then(async () => {
  const s = await apiFetch(`/api/pilots/${PILOT}/sectors`);
  $("sectorChecks").innerHTML = s.features.map((f) => `<label class="chip"><input type="checkbox" value="${esc(f.properties.name)}" checked /><span>${esc(f.properties.name)}</span></label>`).join("");
  const t = new Date();
  $("reviewDate").value = iso(t);
  t.setDate(t.getDate() + 1);
  $("planDate").value = iso(t);
  $("planNow").addEventListener("click", async () => {
    const sectors = [...document.querySelectorAll("#sectorChecks input:checked")].map((i) => i.value);
    if (!sectors.length) return toast("Choose at least one sector.");
    try {
      const r = await swmWrite("POST", `/api/pilots/${PILOT}/agent/plan`, { date: $("planDate").value, sectors });
      toast(`${r.queued} sector(s) queued for ${r.date}. Each takes about a minute.`);
      load();
    } catch (err) { toast(err.message); }
  });
  $("reviewNow").addEventListener("click", async () => {
    try { await swmWrite("POST", `/api/pilots/${PILOT}/agent/review`, { date: $("reviewDate").value }); toast("Review queued."); load(); } catch (err) { toast(err.message); }
  });
  load();
});
