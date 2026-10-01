// Survey supervisor: progress, spot-check queue and results, items to review, sector assignments.

const PILOT = "hsr";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (x) => (x == null ? "–" : `${fmt(100 * x, 1)}%`);
const SESSION = getSession();
const OUTCOMES = ["completed", "partial", "refused", "locked", "revisit_requested", "demolished", "not_a_building", "footprint_issue"];
let CONFIG = null;

function table(head, rows) {
  return `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
}

function failed(el, err) {
  $(el).innerHTML = `<p class="warn">${esc(t("common.error", { message: err.message }))}</p>`;
}

async function loadProgress() {
  try {
    const s = (await apiFetch(`/api/pilots/${PILOT}/summary`)).survey;
    const mine = s.coverage_by_sector.filter((r) => SESSION.sectors.includes(r.sector));
    const outcomeCols = OUTCOMES.map((o) => esc(t(`outcome.${o}`)));
    $("bySector").innerHTML = table([esc(t("col.sector")), esc(t("col.buildings")), esc(t("col.visited")), ...outcomeCols, esc(t("col.not_visited"))],
      mine.map((r) => {
        const visited = r.buildings - r.not_visited;
        return [esc(r.sector), fmt(r.buildings), `${fmt(visited)} (${fmt(r.buildings ? (100 * visited) / r.buildings : 0, 1)}%)`,
          ...OUTCOMES.map((o) => fmt(r[o])), fmt(r.not_visited)];
      }));
    $("bySurveyor").innerHTML = s.visits_by_surveyor.length
      ? table([esc(t("col.surveyor")), esc(t("col.visits")), ...outcomeCols], s.visits_by_surveyor.map((r) => [esc(r.surveyor), fmt(r.visits), ...OUTCOMES.map((o) => fmt(r[o]))]))
      : `<p class="hint">${esc(t("sup.queue_empty"))}</p>`;
    $("rates").innerHTML = s.spot_checks_by_surveyor.length
      ? table([esc(t("col.surveyor")), esc(t("col.checks")), esc(t("col.values")), esc(t("col.mismatches")), esc(t("col.use_mismatches")), esc(t("col.rate"))],
        s.spot_checks_by_surveyor.map((r) => [esc(r.surveyor), fmt(r.spot_checks), fmt(r.values_checked), fmt(r.mismatches),
          `<b>${fmt(r.building_use_mismatches)}</b> (${pct(r.building_use_mismatch_rate)})`, pct(r.mismatch_rate)]))
      : `<p class="hint">${esc(t("sup.rates_empty"))}</p>`;
  } catch (err) {
    failed("bySector", err);
  }
}

async function loadQueue() {
  try {
    const { queue } = await apiFetch(`/api/pilots/${PILOT}/spot-checks`);
    $("queue").innerHTML = queue.length ? queue.map((q) => `
      <div class="result row-item"><div><b>${esc(q.building_label || q.building_id)}</b> · ${esc(q.sector)}<br>
        <span class="muted">${esc(q.surveyor)} · ${esc(q.visited_at.slice(0, 10))}</span></div>
        <button type="button" data-check="${esc(q.visit_id)}" data-building="${esc(q.building_id)}" data-name="${esc(q.surveyor)}" data-date="${esc(q.visited_at.slice(0, 10))}">${esc(t("sup.start_check"))}</button></div>`).join("")
      : `<p class="hint">${esc(t("sup.queue_empty"))}</p>`;
    $("queue").querySelectorAll("[data-check]").forEach((b) => b.addEventListener("click", async () => {
      const card = await apiFetch(`/api/pilots/${PILOT}/building?id=${encodeURIComponent(b.dataset.building)}`);
      SurveyFlow.open({ mode: "spot_check", building: card, config: CONFIG, spotCheckOf: b.dataset.check,
        spotInfo: { name: b.dataset.name, date: b.dataset.date }, onDone: () => { loadQueue(); loadProgress(); loadReviews(); } });
    }));
  } catch (err) {
    failed("queue", err);
  }
}

function describe(item) {
  const d = item.detail;
  if (item.kind === "use_mix_contradiction") return esc(d.warnings.map(SurveyFlow.describeWarning).join(" "));
  if (item.kind === "gps_far") return `${fmt(d.distance_m)} m`;
  return `${esc(SurveyFlow.describeMismatch(d))} · ${esc(d.surveyor || "")}`;
}

async function loadReviews() {
  try {
    const { review_items: items, geometry_flags: flags } = await apiFetch(`/api/pilots/${PILOT}/review-items`);
    const link = (bid) => (bid ? `<a href="surveyor.html?building=${encodeURIComponent(bid)}">${esc(bid)}</a>` : "");
    $("reviews").innerHTML = items.length ? items.map((i) => `
      <div class="result row-item"><div><b>${esc(t(`review.${i.kind}`))}</b> · ${link(i.building_id)}<br>
        <span class="muted">${describe(i)}</span></div>
        <button type="button" class="secondary small" data-resolve-item="${esc(i.id)}">${esc(t("sup.resolve"))}</button></div>`).join("")
      : `<p class="hint">${esc(t("sup.review_empty"))}</p>`;
    $("flags").innerHTML = flags.length ? flags.map((f) => `
      <div class="result row-item"><div><b>${esc(t(`flag.${f.kind}`))}</b> · ${f.building_id ? link(f.building_id) : `${fmt(f.lat, 5)}, ${fmt(f.lon, 5)}`}<br>
        <span class="muted">${esc(f.note || "")} · ${esc(f.created_by || "")}</span></div>
        <button type="button" class="secondary small" data-resolve-flag="${esc(f.id)}">${esc(t("sup.resolve"))}</button></div>`).join("")
      : `<p class="hint">${esc(t("sup.flags_empty"))}</p>`;
    document.querySelectorAll("[data-resolve-item]").forEach((b) => b.addEventListener("click", async () => {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/review-items/${b.dataset.resolveItem}`);
      loadReviews();
    }));
    document.querySelectorAll("[data-resolve-flag]").forEach((b) => b.addEventListener("click", async () => {
      await swmWrite("PATCH", `/api/pilots/${PILOT}/geometry-flags/${b.dataset.resolveFlag}`);
      loadReviews();
    }));
  } catch (err) {
    failed("reviews", err);
  }
}

async function loadAssignments() {
  try {
    const { assignments } = await apiFetch(`/api/pilots/${PILOT}/assignments`);
    const mine = assignments.filter((a) => SESSION.sectors.includes(a.sector));
    $("assignments").innerHTML = mine.length ? mine.map((a) => `
      <div class="result row-item"><div><b>${esc(a.surveyor_name)}</b> · ${esc(a.sector)}<br>
        <span class="muted">${esc(a.assigned_by || "")} · ${esc(a.assigned_at.slice(0, 10))}</span></div>
        <button type="button" class="secondary small" data-end="${esc(a.id)}">${esc(t("sup.assign_end"))}</button></div>`).join("")
      : `<p class="hint">${esc(t("sup.assign_empty"))}</p>`;
    document.querySelectorAll("[data-end]").forEach((b) => b.addEventListener("click", async () => {
      await swmWrite("DELETE", `/api/pilots/${PILOT}/assignments/${b.dataset.end}`);
      loadAssignments();
    }));
  } catch (err) {
    failed("assignments", err);
  }
}

$("assignForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("assignErr").hidden = true;
  try {
    await swmWrite("POST", `/api/pilots/${PILOT}/assignments`, { surveyor_name: $("assignName").value.trim(), sector: $("assignSector").value });
    $("assignName").value = "";
    loadAssignments();
  } catch (err) {
    $("assignErr").textContent = err.message;
    $("assignErr").hidden = false;
  }
});

(async () => {
  await UI_READY;
  if (!SESSION || SESSION.role !== "survey_supervisor") {
    $("denied").hidden = false;
    return;
  }
  document.title = t("sup.title");
  $("who").textContent = `${SESSION.name} · ${t("sup.sectors", { sectors: SESSION.sectors.join(", ") })}`;
  $("content").hidden = false;
  $("assignSector").innerHTML = SESSION.sectors.map((s) => `<option>${esc(s)}</option>`).join("");
  try {
    CONFIG = await apiFetch("/api/survey/config");
  } catch (err) {
    failed("queue", err);
  }
  loadProgress();
  loadQueue();
  loadReviews();
  loadAssignments();
})();
