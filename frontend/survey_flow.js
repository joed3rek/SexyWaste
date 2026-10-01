// Round 1 quick survey flow, shared by the surveyor (visits) and the survey supervisor (spot-checks).
// Steps: outcome -> building use -> use mix -> collection -> check and finish. Each step saves when
// the person moves on; every save goes through swmWrite() (ui.js). Needs ui.js and its strings.

const SurveyFlow = (() => {
  const PILOT = "hsr";
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
  let S = null; // state of the open flow

  // ---------- small pieces ----------

  function chip(source) {
    return source ? `<span class="src src-${esc(source)}">${esc(t(`source.${source}`))}</span>` : "";
  }

  function tiles(name, keys, selected, prefix, cls = "") {
    return `<div class="tiles ${cls}" role="radiogroup">${keys.map((k) => `
      <button type="button" class="tile${k === selected ? " on" : ""}" data-name="${name}" data-value="${esc(k)}" role="radio" aria-checked="${k === selected}">
        ${esc(t(`${prefix}.${k}`))}</button>`).join("")}</div>`;
  }

  function stepper(key, value, placeholder = "") {
    return `<div class="stepper" data-key="${esc(key)}">
      <button type="button" class="step-btn" data-d="-1" aria-label="−">−</button>
      <input type="number" inputmode="numeric" min="0" step="1" value="${value ?? ""}" placeholder="${esc(placeholder)}" />
      <button type="button" class="step-btn" data-d="1" aria-label="+">+</button></div>`;
  }

  function wireTiles(root, onPick) {
    root.querySelectorAll(".tile").forEach((b) => b.addEventListener("click", () => {
      root.querySelectorAll(`.tile[data-name="${b.dataset.name}"]`).forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-checked", "false"); });
      b.classList.add("on");
      b.setAttribute("aria-checked", "true");
      onPick(b.dataset.name, b.dataset.value);
    }));
  }

  function wireSteppers(root, onChange) {
    root.querySelectorAll(".stepper").forEach((st) => {
      const input = st.querySelector("input");
      const set = (v) => { input.value = v; onChange(st.dataset.key, v === "" ? null : Number(v)); };
      st.querySelectorAll(".step-btn").forEach((b) => b.addEventListener("click", () => {
        const cur = input.value === "" ? 0 : Number(input.value);
        set(Math.max(0, cur + Number(b.dataset.d)));
      }));
      input.addEventListener("input", () => set(input.value === "" ? "" : Math.max(0, Number(input.value))));
    });
  }

  function messages(list, cls = "warn") {
    return list.length ? `<div class="notes">${list.map((m) => `<p class="${cls}">${esc(m)}</p>`).join("")}</div>` : "";
  }

  // Server warnings and mismatches carry codes and details; they are worded here, in the user's language.
  function valueLabel(field, v) {
    if (v === null || v === undefined) return t("mismatch.blank");
    if (field === "building_use") return t(`building_use.${v}`);
    if (STRINGS[`value.${field}.${v}`]) return t(`value.${field}.${v}`);
    return String(v);
  }

  function describeWarning(w) {
    const bu = t(`building_use.${w.building_use}`);
    if (w.code === "expects_any") return t("contra.expects_any", { building_use: bu, uses: w.uses.map((u) => t(`use.${u}`)).join(t("contra.or")) });
    if (w.code === "warn_over") return t("contra.warn_over", { building_use: bu, total: fmt(w.total), field: t(`noun.${w.field}`), use: t(`use.${w.use}`), max: fmt(w.max) });
    if (w.code === "no_occupied_uses") return t("contra.no_occupied_uses", { building_use: bu, uses: w.uses.map((u) => t(`use.${u}`)).join(", ") });
    return w.message || String(w);
  }

  function describeMismatch(m) {
    if (m.field === "use_mix") return t(m.checked ? "mismatch.use_added" : "mismatch.use_missing", { use: t(`use.${m.use}`) });
    if (m.use) return t("mismatch.use_field", { use: t(`use.${m.use}`), field: t(`field.${m.field}`), surveyed: valueLabel(m.field, m.surveyed), checked: valueLabel(m.field, m.checked) });
    return t("mismatch.field", { field: t(`field.${m.field}`), surveyed: valueLabel(m.field, m.surveyed), checked: valueLabel(m.field, m.checked) });
  }

  // ---------- opening ----------

  function position() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null);
      navigator.geolocation.getCurrentPosition(
        (p) => resolve({ lon: p.coords.longitude, lat: p.coords.latitude, accuracy_m: p.coords.accuracy }),
        () => resolve(null), { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
    });
  }

  async function open(opts) {
    // opts: {mode: "survey" | "spot_check", building: card from /building, config, spotCheckOf, spotInfo, onDone}
    const spot = opts.mode === "spot_check";
    S = {
      ...opts, spot, step: 0, warnings: [], mismatches: [], ack: false, photo: null,
      steps: spot ? ["use", "mix", "collection", "confirm"] : ["outcome", "use", "mix", "collection", "confirm"],
      values: {}, sources: {}, rows: [], existing: [],
    };
    const d = opts.building.survey_detail || { fields: {}, use_mix: [] };
    S.existing = d.use_mix.map((r) => ({ id: r.id, use: r.use }));
    if (!spot) { // a revisit starts from what is already recorded
      for (const [k, f] of Object.entries(d.fields)) {
        if (["building_use", "floors", "society_name", "collection_arrangement", "segregation_reported", "home_compost", "home_compost_method"].includes(k)) {
          S.values[k] = f.value;
          S.sources[k] = f.source;
        }
      }
      S.rows = d.use_mix.map((r) => ({ id: r.id, use: r.use, values: Object.fromEntries(Object.entries(r.fields).map(([k, f]) => [k, f.value])),
        sources: Object.fromEntries(Object.entries(r.fields).map(([k, f]) => [k, f.source])), dirty: false, removed: false }));
    }
    shell();
    render(`<p class="hint">${esc(spot ? t("common.loading") : t("flow.gps_wait"))}</p>`);
    try {
      const gps = spot ? null : await position();
      const body = spot ? { purpose: "spot_check", spot_check_of: opts.spotCheckOf }
        : { building_id: opts.building.id, ...(gps || {}) };
      const res = await swmWrite("POST", `/api/pilots/${PILOT}/visits`, body);
      S.visit = res.visit;
      S.notice = [...res.warnings, ...(!spot && !gps ? [t("flow.gps_none")] : [])];
      draw();
    } catch (err) {
      render(`<p class="warn">${esc(err.message)}</p>`, `<button type="button" class="secondary block" data-act="quit">${esc(t("common.close"))}</button>`);
    }
  }

  // ---------- layout ----------

  function shell() {
    let el = document.getElementById("flow");
    if (!el) {
      el = document.createElement("div");
      el.id = "flow";
      el.className = "flow";
      el.setAttribute("role", "dialog");
      el.setAttribute("aria-modal", "true");
      document.body.append(el);
    }
    el.hidden = false;
    document.body.classList.add("flow-open");
  }

  function render(body, foot = "") {
    const b = S.building;
    const name = b.name || b.address || t("sheet.no_name");
    const head = S.spot ? `${esc(t("spot.title"))} · ${esc(name)}` : esc(name);
    const step = S.visit ? `<span class="muted small">${esc(t("flow.step", { n: S.step + 1, total: S.steps.length }))}</span>` : "";
    document.getElementById("flow").innerHTML = `
      <header class="flow-head"><div><b>${head}</b><br>${step}</div>
        <button type="button" class="close" data-act="exit" aria-label="${esc(t("common.close"))}">×</button></header>
      <div class="flow-body">${body}</div>
      <footer class="flow-foot">${foot}</footer>`;
    const root = document.getElementById("flow");
    root.querySelector('[data-act="exit"]').addEventListener("click", exit);
    root.querySelector('[data-act="quit"]')?.addEventListener("click", finishUi);
    root.querySelector('[data-act="back"]')?.addEventListener("click", () => { S.step -= 1; S.ack = false; draw(); });
    return root;
  }

  function nav(nextLabel = t("common.next")) {
    return `${S.step > 0 ? `<button type="button" class="secondary" data-act="back">${esc(t("common.back"))}</button>` : ""}
      <button type="button" class="lg grow" data-act="next">${esc(nextLabel)}</button>`;
  }

  function draw() {
    ({ outcome: drawOutcome, use: drawUse, mix: drawMix, collection: drawCollection, confirm: drawConfirm })[S.steps[S.step]]();
  }

  async function advance(save) {
    const btn = document.querySelector('#flow [data-act="next"]');
    if (btn) btn.disabled = true;
    try {
      const ok = await save();
      if (ok === false) return;
      S.step += 1;
      S.ack = false;
      draw();
    } catch (err) {
      S.error = err.message;
      draw();
      S.error = null;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function errorLine() {
    return S.error ? `<p class="warn">${esc(S.error)}</p>` : "";
  }

  // ---------- step 1: outcome ----------

  function drawOutcome() {
    const ends = S.config.ends_visit;
    const root = render(`
      ${messages(S.notice || [])}
      <h2>${esc(t("flow.s1.title"))}</h2>
      <button type="button" class="lg block" data-act="survey">${esc(t("flow.s1.survey"))}</button>
      <p class="label">${esc(t("flow.s1.other"))}</p>
      ${tiles("outcome", ends, S.endOutcome, "outcome")}
      <div id="endBox" ${S.endOutcome ? "" : "hidden"}>
        <label>${esc(t("flow.s1.note"))}<textarea id="endNote" rows="2"></textarea></label>
        <button type="button" class="block lg" data-act="end">${esc(t("flow.s1.end"))}</button>
      </div>${errorLine()}`);
    root.querySelector('[data-act="survey"]').addEventListener("click", () => { S.step = 1; draw(); });
    wireTiles(root, (_n, v) => { S.endOutcome = v; root.querySelector("#endBox").hidden = false; });
    root.querySelector('[data-act="end"]')?.addEventListener("click", async () => {
      try {
        await swmWrite("PATCH", `/api/pilots/${PILOT}/visits/${S.visit.id}`, { outcome: S.endOutcome, notes: root.querySelector("#endNote").value || null });
        finishUi({ outcome: S.endOutcome });
      } catch (err) {
        S.error = err.message; draw(); S.error = null;
      }
    });
  }

  // ---------- step 2: building use ----------

  function drawUse() {
    const cfg = S.config;
    const b = S.building;
    const hint = b.building_use_hint ? t("flow.s2.hint", { use: t(`building_use.${b.building_use_hint}`) }) : "";
    const bu = S.values.building_use;
    const floorsPh = b.levels_source === "from OSM" ? t("flow.s2.floors_osm", { n: fmt(b.levels) })
      : t("flow.s2.floors_assumed", { n: fmt(b.levels) });
    const askSociety = bu && cfg.building_uses[bu].ask_society_name;
    const root = render(`
      ${S.spot ? `<p class="hint">${esc(t("spot.of", S.spotInfo))}</p><p class="hint">${esc(t("spot.blind"))}</p>` : ""}
      <h2>${esc(t("flow.s2.title"))} ${chip(S.sources.building_use)}</h2>
      ${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
      ${tiles("building_use", Object.keys(cfg.building_uses), bu, "building_use", "tiles-2")}
      <div class="field-row"><span class="label">${esc(t("flow.s2.floors"))} ${chip(S.sources.floors)}</span>
        ${stepper("floors", S.values.floors, floorsPh)}</div>
      <label id="societyBox" ${askSociety ? "" : "hidden"}>${esc(t("flow.s2.society"))} ${chip(S.sources.society_name)}
        <input id="society" value="${esc(S.values.society_name || "")}" /></label>
      ${errorLine()}`, nav());
    wireTiles(root, (_n, v) => {
      S.values.building_use = v;
      S.sources.building_use = null;
      root.querySelector("#societyBox").hidden = !cfg.building_uses[v].ask_society_name;
    });
    wireSteppers(root, (k, v) => { S.values[k] = v; S.sources[k] = null; });
    root.querySelector("#society").addEventListener("input", (e) => { S.values.society_name = e.target.value; S.sources.society_name = null; });
    root.querySelector('[data-act="next"]').addEventListener("click", () => advance(async () => {
      if (!S.values.building_use) { S.error = t("flow.s2.need_use"); draw(); S.error = null; return false; }
      const fields = { building_use: S.values.building_use };
      if (S.values.floors) fields.floors = S.values.floors;
      if (S.values.society_name && cfg.building_uses[S.values.building_use].ask_society_name) fields.society_name = S.values.society_name;
      const res = await swmWrite("POST", `/api/pilots/${PILOT}/visits/${S.visit.id}/building-fields`, { fields });
      S.mismatches.push(...res.mismatches);
      for (const k of Object.keys(fields)) S.sources[k] = "surveyed";
      return true;
    }));
  }

  // ---------- step 3: use mix ----------

  function drawMix() {
    const cfg = S.config;
    const bu = cfg.building_uses[S.values.building_use];
    if (!S.rows.some((r) => !r.removed) && !S.mixSeeded) {
      S.rows.push(...bu.default_use_mix.map((u) => ({ use: u, values: {}, sources: {}, dirty: true, removed: false })));
      S.mixSeeded = true;
    }
    const live = S.rows.filter((r) => !r.removed);
    const present = new Set(live.map((r) => r.use));
    const order = [...bu.default_use_mix, ...Object.keys(cfg.uses).filter((u) => !bu.default_use_mix.includes(u))];
    const rowsHtml = live.map((r) => {
      const i = S.rows.indexOf(r);
      return `<div class="mix-row" data-i="${i}"><div class="mix-head"><b>${esc(t(`use.${r.use}`))}</b>
          <button type="button" class="ghost small" data-remove="${i}">${esc(t("flow.s3.remove"))}</button></div>
        ${cfg.uses[r.use].fields.map((f) => `<div class="field-row"><span class="label">${esc(t(`field.${f}`))} ${chip(r.dirty ? null : r.sources[f])}</span>
          ${stepper(`${i}:${f}`, r.values[f])}</div>`).join("")}</div>`;
    }).join("") || `<p class="hint">${esc(t("flow.s3.empty"))}</p>`;
    const root = render(`
      <h2>${esc(t("flow.s3.title"))}</h2>
      ${S.warnings.length ? `<p class="label">${esc(t("flow.s3.check"))}</p>${messages(S.warnings.map(describeWarning))}` : ""}
      <p class="hint">${esc(t("flow.s3.help"))}</p>
      ${rowsHtml}
      <p class="label">${esc(t("flow.s3.add"))}</p>
      <div class="chips">${order.filter((u) => !present.has(u)).map((u) => `<button type="button" class="chip-btn${bu.default_use_mix.includes(u) ? " suggested" : ""}" data-add="${u}">${esc(t(`use.${u}`))}</button>`).join("")}</div>
      ${errorLine()}`, nav(S.ack ? t("flow.s3.continue_anyway") : t("common.next")));
    root.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
      const back = S.rows.find((r) => r.use === b.dataset.add && r.removed);
      if (back) { back.removed = false; back.dirty = true; } else S.rows.push({ use: b.dataset.add, values: {}, sources: {}, dirty: true, removed: false });
      S.ack = false;
      drawMix();
    }));
    root.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
      S.rows[Number(b.dataset.remove)].removed = true;
      S.ack = false;
      drawMix();
    }));
    wireSteppers(root, (key, v) => {
      const [i, f] = key.split(":");
      const r = S.rows[Number(i)];
      r.values[f] = v;
      r.dirty = true;
    });
    root.querySelector('[data-act="next"]').addEventListener("click", () => {
      if (S.ack) { S.step += 1; S.ack = false; S.warnings = []; draw(); return; }
      advance(saveMix);
    });
  }

  async function saveMix() {
    const live = S.rows.filter((r) => !r.removed);
    const missing = live.find((r) => !r.values.count && r.values.count !== 0 && !r.values.beds_total);
    if (missing) { S.error = t("flow.s3.need_count", { use: t(`use.${missing.use}`) }); drawMix(); S.error = null; return false; }
    const base = `/api/pilots/${PILOT}/visits/${S.visit.id}/use-mix`;
    let warnings = [];
    const vals = (r) => Object.fromEntries(Object.entries(r.values).filter(([, v]) => v !== null && v !== undefined));
    for (const r of S.rows) {
      let res = null;
      if (S.spot) {
        if (!r.removed) res = await swmWrite("POST", base, { use: r.use, ...vals(r) });
      } else if (r.removed && r.id) {
        res = await swmWrite("PATCH", `${base}/${r.id}`, { remove: true });
      } else if (!r.removed && !r.id) {
        res = await swmWrite("POST", base, { use: r.use, ...vals(r) });
        r.id = res.id;
      } else if (!r.removed && r.dirty) {
        res = await swmWrite("PATCH", `${base}/${r.id}`, vals(r));
      }
      if (res) { warnings = res.warnings; S.mismatches.push(...(res.mismatches || [])); r.dirty = false; Object.keys(r.values).forEach((k) => (r.sources[k] = "surveyed")); }
    }
    if (S.spot) { // survey rows the spot-check did not find
      const found = new Set(S.rows.filter((r) => !r.removed).map((r) => r.use));
      for (const e of S.existing.filter((x) => !found.has(x.use))) {
        const res = await swmWrite("PATCH", `${base}/${e.id}`, { remove: true });
        warnings = res.warnings;
        S.mismatches.push(...res.mismatches);
      }
      S.existing = [];
    }
    S.rows = S.rows.filter((r) => !(r.removed && !r.id));
    if (warnings.length) {
      S.warnings = warnings;
      S.ack = true;
      drawMix();
      document.querySelector("#flow .flow-body").scrollTop = 0;
      return false;
    }
    S.warnings = [];
    return true;
  }

  // ---------- step 4: collection and composting ----------

  function drawCollection() {
    const f = S.config.building_fields;
    const v = S.values;
    const composts = ["all", "most", "some"].includes(v.home_compost);
    const root = render(`
      <h2>${esc(t("flow.s4.title"))}</h2>
      <p class="label">${esc(t("field.collection_arrangement"))} ${chip(S.sources.collection_arrangement)}</p>
      ${tiles("collection_arrangement", f.collection_arrangement, v.collection_arrangement, "value.collection_arrangement", "tiles-2")}
      <p class="label">${esc(t("field.segregation_reported"))} ${chip(S.sources.segregation_reported)}</p>
      ${tiles("segregation_reported", f.segregation_reported, v.segregation_reported, "value.segregation_reported", "tiles-2")}
      <p class="label">${esc(t("field.home_compost"))} ${chip(S.sources.home_compost)}</p>
      ${tiles("home_compost", f.home_compost, v.home_compost, "value.home_compost", "tiles-2")}
      <div id="methodBox" ${composts ? "" : "hidden"}><p class="label">${esc(t("field.home_compost_method"))} ${chip(S.sources.home_compost_method)}</p>
        ${tiles("home_compost_method", f.home_compost_method, v.home_compost_method, "value.home_compost_method", "tiles-2")}</div>
      <p class="label">${esc(t("flow.s4.photo"))}</p>
      <p class="hint">${esc(t("flow.s4.photo_guidance"))}</p>
      <input id="photo" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" />
      ${S.photoDone ? `<p class="hint good">${esc(t("flow.s4.photo_added"))}</p>` : ""}
      ${errorLine()}`, nav());
    wireTiles(root, (name, value) => {
      v[name] = value;
      S.sources[name] = null;
      if (name === "home_compost") root.querySelector("#methodBox").hidden = !["all", "most", "some"].includes(value);
    });
    root.querySelector("#photo").addEventListener("change", (e) => { S.photo = e.target.files[0] || null; });
    root.querySelector('[data-act="next"]').addEventListener("click", () => advance(async () => {
      const fields = {};
      for (const k of ["collection_arrangement", "segregation_reported", "home_compost"]) if (v[k]) fields[k] = v[k];
      if (["all", "most", "some"].includes(v.home_compost) && v.home_compost_method) fields.home_compost_method = v.home_compost_method;
      if (Object.keys(fields).length) {
        const res = await swmWrite("POST", `/api/pilots/${PILOT}/visits/${S.visit.id}/building-fields`, { fields });
        S.mismatches.push(...res.mismatches);
        for (const k of Object.keys(fields)) S.sources[k] = "surveyed";
      }
      if (S.photo && !S.photoDone) {
        const gps = S.visit.gps_lon != null ? `?lon=${S.visit.gps_lon}&lat=${S.visit.gps_lat}` : "";
        await swmWrite("POST", `/api/pilots/${PILOT}/visits/${S.visit.id}/photo${gps}`, S.photo, S.photo.type || "image/jpeg");
        S.photoDone = true;
      }
      return true;
    }));
  }

  // ---------- step 5: check and finish ----------

  async function drawConfirm() {
    render(`<p class="hint">${esc(t("common.loading"))}</p>`);
    let card;
    try {
      card = await apiFetch(`/api/pilots/${PILOT}/building?id=${encodeURIComponent(S.building.id)}`);
    } catch (err) {
      render(`<p class="warn">${esc(err.message)}</p>`, nav());
      return;
    }
    const bwg = card.bwg_status === "bwg_likely" ? `<p class="warn">${esc(t("flow.s5.bwg_likely"))}</p>`
      : card.bwg_status === "bwg_confirmed" ? `<p class="warn">${esc(t("flow.s5.bwg_confirmed"))}</p>` : "";
    const root = render(`
      <h2>${esc(t("flow.s5.title"))}</h2>
      <div class="kpis"><div class="kpi wide"><div class="k">${esc(t("flow.s5.estimate"))} ${card.quantity_is_estimate ? `<span class="src src-assumed">${esc(t("source.estimate"))}</span>` : chip("weighed")}</div>
        <div class="v">${esc(t("flow.s5.kg", { kg: fmt(card.kg_day, 1) }))}</div>
        <div class="s">${esc(t(`basis.${card.quantity_basis}`))} · ${esc(t(`source.${card.quantity_input_source}`))}</div></div></div>
      ${bwg}
      ${messages(card.survey_detail.contradictions.map(describeWarning))}
      ${S.mismatches.length ? `<p class="label">${esc(t("spot.title"))}</p>${messages(S.mismatches.map(describeMismatch), "hint")}` : ""}
      ${errorLine()}`,
      `${`<button type="button" class="secondary" data-act="back">${esc(t("common.back"))}</button>`}
       <button type="button" class="secondary grow" data-act="partial">${esc(t("flow.s5.partial"))}</button>
       <button type="button" class="lg grow" data-act="done">${esc(t("flow.s5.finish"))}</button>`);
    const close = async (outcome) => {
      try {
        await swmWrite("PATCH", `/api/pilots/${PILOT}/visits/${S.visit.id}`, { outcome });
        finishUi({ outcome, card });
      } catch (err) {
        S.error = err.message; drawConfirm(); S.error = null;
      }
    };
    root.querySelector('[data-act="partial"]').addEventListener("click", () => close("partial"));
    root.querySelector('[data-act="done"]').addEventListener("click", () => close("completed"));
  }

  // ---------- leaving ----------

  async function exit() {
    if (S.visit && !S.visit.ended_at) {
      // Leaving early keeps what was saved and marks the visit partly done.
      try { await swmWrite("PATCH", `/api/pilots/${PILOT}/visits/${S.visit.id}`, { outcome: "partial" }); } catch { /* already closed */ }
      return finishUi({ outcome: "partial" });
    }
    finishUi();
  }

  function finishUi(result) {
    const el = document.getElementById("flow");
    if (el) el.hidden = true;
    document.body.classList.remove("flow-open");
    const done = S && S.onDone;
    S = null;
    if (done) done(result || null);
  }

  return { open, describeWarning, describeMismatch };
})();
