// Renders the machine-readable regulations library served at /api/regulations.

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const table = (head, rows) => `<table><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</table>`;
const cite = (r) => `<span class="cite">r. ${esc(r)}</span>`;

(async () => {
  const index = await (await fetch("/api/regulations")).json();
  document.getElementById("docs").innerHTML = table(
    ["Document", "Instrument", "In force", "Status"],
    index.documents.map((d) => [esc(d.title), esc(d.instrument), esc(d.in_force_from), esc(d.status)]),
  ) + `<p class="hint">Planned additions: ${index.planned.map(esc).join("; ")}.</p>`;

  const d = await (await fetch("/api/regulations/swm_rules_2026")).json();
  const ws = d.waste_streams;
  const bwg = d.generator_types.bulk_waste_generator;
  document.getElementById("content").innerHTML = `
    <h2>${esc(d.title)}</h2>
    <p class="hint">${esc(d.citation)} · in force ${esc(d.in_force_from)} · ${esc(d.text_used)}</p>

    <h3>The four source-segregated streams ${cite(ws.source_segregation_rule)}</h3>
    ${table(["Stream", "Definition", "Public bin", "Goes to"], ws.streams.map((s) => [
      `<b>${esc(s.official_term)}</b> ${cite(s.rule)}`, esc(s.definition), esc(s.public_bin_colour || "–"), esc(s.destination)]))}

    <h3>Dry waste fractions ${cite(ws.dry_waste_fractions.rule)}</h3>
    <p>${esc(ws.dry_waste_fractions.note)} Fractions: ${ws.dry_waste_fractions.fractions.map((f) => esc(f.label)).join(", ")}.</p>

    <h3>Kept separate from the four streams</h3>
    ${table(["Waste", "Rule", "Handling"], [
      ...ws.other_streams_kept_separate.map((o) => [esc(o.official_term), cite(o.rule), esc(o.definition || o.handling || o.note || "")]),
      ...ws.separately_regulated.map((o) => [esc(o.official_term), esc(o.governed_by), esc(o.mrf_role)]),
    ])}

    <h3>Bulk waste generator ${cite(bwg.rule)}</h3>
    <p>Meets <b>at least one</b> of:</p>
    ${table(["Criterion", "Threshold"], bwg.criteria_any_one.map((c) => [esc(c.text), `${esc(c.operator)} ${Number(c.value).toLocaleString("en-IN")}`]))}
    <p class="small">Entities: ${Object.entries(bwg.entity_groups).map(([g, l]) => `<b>${esc(g)}</b>: ${l.map(esc).join(", ")}`).join(". ")}.</p>
    ${table(["Duty", "Rule"], bwg.duties.map((x) => [esc(x.text), cite(x.rule)]))}

    <h3>Key dates</h3>
    ${table(["Date", "What", "Rule"], d.key_deadlines.map((k) => [esc(k.date), esc(k.what), cite(k.rule)]))}

    <h3>Reporting calendar</h3>
    ${table(["What", "By", "Rule"], d.reporting_calendar.map((k) => [esc(k.what), esc(k.by), cite(k.rule)]))}

    <h3>ULB solid waste action plan ${cite(d.ulb_action_plan.rule)}</h3>
    <ul>${d.ulb_action_plan.elements.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>

    <h3>Differences between the Hindi and English texts</h3>
    ${table(["Topic", "English", "Hindi", "Used"], d.text_discrepancies.map((x) => [esc(x.topic), esc(x.english), esc(x.hindi), esc(x.used)]))}`;
})();
