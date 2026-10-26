// HEDIS Ops Console: the shell (sidebar, routing, shared helpers), Dashboard, Prioritization and Evidence Review.
// Data Ingestion, HEDIS Calculation, Data Quality and Star Rating live in their own files and register in TABS.
// Every value comes from the API; text is added as text nodes, never as HTML.
"use strict";

const VIEWS = ["dashboard", "ingestion", "calculation", "quality", "stars", "prioritization", "evidence"];
// Each tab file adds { render: async () => {} } under its view name; render runs every time the tab is shown.
const TABS = {};
const STEPS = [
  ["received", "Ingestion job persisted"],
  ["converting", "Convert CSV to FHIR and validate"],
  ["persisting", "Approve records (persist stage)"],
  ["waiting", "Waiting for the previous batch"],
  ["loading", "Load the patient tables"],
  ["checking", "Run the data quality checks"],
  ["measuring", "Run the measure engine"],
  ["prioritizing", "Score and rank open gaps"],
  ["attaching", "Attach stored evidence and read it"],
];

const CHANNELS = {
  abstractor_review: "Abstractor review", chart_review: "Chart review", point_of_care_alert: "Point-of-care alert",
  provider_gap_list: "Provider gap list", member_outreach_call: "Phone", mailed_fit_kit: "Mailed kit",
};
const state = { gaps: [], tier: "", measures: null, health: null, openEvidence: null, scopeGap: null, batch: null,
  polling: null };

const $ = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
};
const byId = id => document.getElementById(id);
const num = v => (v == null ? null : Number(v));
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "-" : Number(v).toFixed(d));
const shortPt = id => (id ? `pt-${id.slice(0, 4)}…` : "pt-?");
const tierTag = t => $("span", { class: `tag tag-${t}` }, t ? t[0].toUpperCase() + t.slice(1) : "-");

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}

function toast(text) {
  const t = byId("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* storage unavailable: the page works without it */ }
  return null;
}

function missingText(row) {
  const text = row.missing_element || "";
  const rest = text.includes(": ") ? text.split(": ").slice(1).join(": ") : text;
  return rest ? rest[0].toUpperCase() + rest.slice(1) : "-";
}

function ago(iso) {
  if (!iso) return "not scored yet";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `updated ${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `updated ${hours}h ago` : `updated ${Math.round(hours / 24)}d ago`;
}

function daysUntil(isoDate) {
  if (!isoDate) return "-";
  const days = Math.round((new Date(isoDate) - new Date(new Date().toDateString())) / 86400000);
  return days === 0 ? "today" : `${days} day${days === 1 ? "" : "s"}`;
}

function bar(label, value, shown) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return $("div", { class: "factor-row" }, $("span", {}, label), $("b", {}, shown),
    $("div", { class: "track" }, $("span", { style: `width:${pct}%` })));
}

// ---------------------------------------------------------------- data

async function loadAll() {
  const [measures, gaps, health, batch] = await Promise.all([api("/api/measures"), api("/api/gaps"),
    api("/api/health").catch(() => null), loadBatch()]);
  state.measures = measures;
  state.gaps = gaps;
  state.health = health;
  state.batch = batch;
}

async function loadBatch() {
  const id = store("hedis.batch") || "latest";
  try {
    return await api(`/api/batches/${encodeURIComponent(id)}`);
  } catch {
    return id === "latest" ? null : api("/api/batches/latest").catch(() => null);
  }
}

const counts = () => (state.batch && state.batch.counts) || {};
const when = iso => (iso ? new Date(iso).toLocaleString() : "-");

// ---------------------------------------------------------------- dashboard

function starRow(stars) {
  const n = Math.max(0, Math.min(5, stars || 0));
  return $("span", { class: "stars", title: stars ? `${stars} of 5 stars` : "No star rating" },
    "★".repeat(n), $("span", { class: "off" }, "★".repeat(5 - n)));
}

function cutChips(m) {
  const rounded = m.rounded || 0;
  return $("div", { class: "cuts" }, ...(m.cut_points || []).map(c => {
    const star = c.stars ?? c.star;
    const low = Number(c.minimum_value ?? c.min ?? c.rate_low_pct);
    return $("span", { class: `cut${rounded >= low ? " reached" : ""}`, title: `${star} stars from ${low}%` },
      `${star}★ ${low}%`);
  }));
}

function renderDashboard() {
  const ms = (state.measures && state.measures.measures) || [];
  const c = counts();
  const ev = (state.measures && state.measures.evidence) || {};
  const openGaps = ms.reduce((s, m) => s + (m.open_gaps || 0), 0);
  const kpi = (label, value, note) => $("div", { class: "kpi" }, $("span", {}, label), $("b", {}, String(value)),
    note ? $("small", {}, note) : null);
  byId("kpis").replaceChildren(
    kpi("PATIENTS LOADED", (c.load && c.load.patients) ?? "-", "in the latest batch"),
    kpi("OPEN GAPS", openGaps, `${(state.measures && state.measures.tiers && state.measures.tiers.high) || 0} high tier`),
    kpi("AWAITING REVIEW", ev.proposed ?? 0, "AI findings proposed"),
    kpi("DOCUMENTS", (c.documents && c.documents.attached) ?? "-", "stored evidence attached"));

  byId("star-year").textContent = state.measures ? `CMS ${state.measures.cms_rating_year} cut points` : "";
  byId("measure-cards").replaceChildren(...(ms.length ? ms.map(m => $("div", { class: "mcard" },
    $("div", { class: "mcard-top" }, $("span", { class: "mcard-name" }, m.name || m.measure),
      $("span", { class: "mcard-code" }, m.measure)),
    $("div", { class: "mcard-top" }, $("span", { class: "mcard-rate" }, m.rate == null ? "-" : `${fmt(m.rate, 1)}%`),
      starRow(m.stars)),
    $("span", { class: "mcard-line" }, `${m.numerator} of ${m.denominator} met · ${m.open_gaps} open gaps`),
    cutChips(m),
    $("span", { class: "mcard-line" }, m.value_reason || ""),
    m.confirmed_pending_engine || m.excluded_pending_engine
      ? $("span", { class: "mcard-line" }, `Projected ${fmt(m.projected_rate, 1)}% · ${m.projected_stars ?? "-"}★ ` +
        `(${m.confirmed_pending_engine} confirmed, ${m.excluded_pending_engine} excluded)`)
      : null))
    : [$("p", { class: "empty" }, "No measure results yet. Ingest and persist a batch on Data Ingestion.")]));

  renderFlow();
}

// The latest batch across the screens: each tab's step, its state, and the rows it stored (GET /api/flow).
async function renderFlow() {
  const box = byId("dash-flow");
  let f;
  try {
    f = await api("/api/flow");
  } catch (err) {
    box.replaceChildren($("p", { class: "empty error" }, `Could not load the batch: ${err.message}`));
    return;
  }
  state.flow = f;
  const b = f.batch;
  const t = f.tables || {};
  const c = (b && b.counts) || {};
  const job = f.ingest_job || f.latest_ingest_job;
  const n = v => Number(v || 0).toLocaleString();
  const ourStep = b ? (b.status === "prioritized" ? "done" : b.status === "failed" ? "failed" : "running") : "pending";
  const rows = [
    ["Data Ingestion", "#ingestion", job ? (job.resources_persisted ? "done" : job.status === "failed" ? "failed"
      : "running") : "pending",
    job ? `job ${job.id.slice(0, 8)} · ${job.status} · ${n(job.resources_generated)} FHIR records, ` +
      `${n(job.resources_persisted)} persisted` : "No ingestion job yet",
    `conversion_jobs ${n(t.conversion_jobs)} · fhir_resources ${n(t.fhir_resources)} ` +
      `(${n(t.fhir_resources_persisted)} persisted) · validation_issues ${n(t.validation_issues)}`],
    ["Our steps after persist", "#ingestion", ourStep,
      b ? `batch ${b.batch_id.slice(0, 8)} · ${statusText(b)}` : "Starts on its own when a job is persisted",
      `patient ${n(t.patient)} · encounter ${n(t.encounter)} · observation ${n(t.observation)} · ` +
      `fhir_resource_raw ${n(t.fhir_resource_raw)}`],
    ["HEDIS Calculation", "#calculation", t.engine_runs ? "done" : ourStep === "running" ? "running" : "pending",
      c.measures ? `${Object.keys(c.measures.measures || {}).length} measures · ${n(c.measures.gaps_open)} open gaps`
        : "Runs after the patient tables are loaded",
      `engine_runs ${n(t.engine_runs)} · measure_result ${n(t.measure_result)} · care_gap ${n(t.care_gap)} ` +
      `(${n(t.care_gap_open)} open) · care_gap_detail ${n(t.care_gap_detail)}`],
    ["Data Quality", "#quality", c.quality && c.quality.status === "failed" ? "failed" : t.quality_run > 0 ? "done"
      : ourStep === "running" ? "running" : "pending",
      c.quality && c.quality.status === "completed"
        ? `run ${c.quality.run_id} · ${c.quality.checks} checks, ${c.quality.failed} failed · ` +
          `${c.quality.anomalies} anomalies`
        : c.quality && c.quality.status === "failed" ? `Checks failed: ${c.quality.reason}`
          : "Runs after the patient tables are loaded",
      `quality_run ${n(t.quality_run)} · quality_check_result ${n(t.quality_check_result)} · ` +
      `data_anomaly ${n(t.data_anomaly)} · feed_completeness_report ${n(t.feed_completeness_report)}`],
    ["Star Rating", "#stars", t.measure_result ? "done" : "pending",
      "Star rating functions on measure_result and care_gap (read only)",
      `star_scenario ${n(t.star_scenario)} (written when a reviewer confirms evidence)`],
    ["Prioritization", "#prioritization", t.gap_priority_score ? "done" : "pending",
      c.prioritization ? `${n(c.prioritization.gaps_scored)} gaps scored (rules ${c.prioritization.rules_version})`
        : "Scores open gaps after the engine run",
      `gap_priority_score ${n(t.gap_priority_score)}`],
    ["Evidence Review", "#evidence", t.document_reference ? "done" : "pending",
      c.documents ? `${n(c.documents.attached)} documents attached` : "Stored documents attach after scoring",
      `document_reference ${n(t.document_reference)} · evidence_proposal ${n(t.evidence_proposal)} · ` +
      `review_decisions ${n(t.review_decisions)} · supplemental_records ${n(t.supplemental_records)}`],
  ];
  const mark = { done: "✓", running: "…", failed: "!", pending: "·" };
  box.replaceChildren(...[
    $("ol", { class: "steps flow" }, ...rows.map(([name, href, cls, line, tables]) =>
      $("li", { class: `step ${cls}` }, $("span", { class: "dot" }, mark[cls]),
        $("span", { class: "step-name" }, $("a", { class: "link", href }, name), $("div", { class: "note small" }, line),
          $("div", { class: "pt" }, tables)),
        $("span", { class: "step-detail" }, cls)))),
    b && b.error ? $("p", { class: "note small error" }, b.error) : null].filter(Boolean));
}

function statusText(b) {
  if (b.status === "failed") return `Failed at ${b.step}`;
  if (b.status === "running") return `Running: ${(STEPS.find(s => s[0] === b.step) || [b.step, b.step])[1]}`;
  return "Finished";
}

// Shared with ingestion.js: one line of detail per step from the batch counts.
function stepDetail(key, c) {
  switch (key) {
    case "converting": return c.conversion ? `${c.conversion.resources_generated} records, ${c.conversion.errors} errors` : "";
    case "persisting": return c.persisted != null ? `${c.persisted} persisted` : "";
    case "loading": return c.load ? `${c.load.patients} patients` : "";
    case "checking":
      if (!c.quality) return "";
      if (c.quality.status === "failed") return "checks failed";
      return `${c.quality.checks} checks, ${c.quality.failed} failed, ${c.quality.anomalies} anomalies`;
    case "measuring": return c.measures ? `${c.measures.gaps_open} open gaps` : "";
    case "prioritizing": return c.prioritization ? `${c.prioritization.gaps_scored} scored` : "";
    case "attaching":
      if (c.documents) return `${c.documents.attached} attached, ${c.documents.failed.length} failed`;
      return c.attaching ? String(c.attaching) : "";
    default: return "";
  }
}

function renderReset() {
  const h = state.health;
  byId("reset-card").hidden = !(h && h.reset_allowed);
  byId("reset-schema").textContent = (h && h.schema) || "";
}

async function startOver() {
  const msg = byId("reset-msg");
  const typed = byId("reset-confirm").value.trim();
  msg.classList.remove("error");
  if (!state.health || typed !== state.health.schema) {
    msg.textContent = `Type ${state.health ? state.health.schema : "the schema name"} exactly to confirm.`;
    msg.classList.add("error");
    return;
  }
  byId("reset").disabled = true;
  msg.textContent = "Clearing data…";
  try {
    const r = await api("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: typed }) });
    store("hedis.batch", "latest");
    store("hedis.job", "");
    state.openEvidence = null;
    state.scopeGap = null;
    byId("reset-confirm").value = "";
    msg.textContent = `Done in ${r.seconds} s. ${r.tables_emptied} data tables in ${r.schema} are empty: ingest the files again to run everything.`;
    toast("Data cleared. Ingest the files again to start over.");
    await refresh();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  } finally {
    byId("reset").disabled = false;
  }
}

// While our steps run, the batch is reloaded every 3 seconds and the open tab redrawn.
function poll() {
  clearTimeout(state.polling);
  if (!state.batch || state.batch.status !== "running") return;
  state.polling = setTimeout(async () => {
    state.batch = await loadBatch();
    if (state.batch && state.batch.status !== "running") {
      toast(state.batch.status === "failed" ? `Batch failed at ${state.batch.step}.` : "Batch finished.");
      await refresh();
      return;
    }
    const view = currentView();
    if (view === "dashboard") renderFlow();
    else if (TABS[view]) await TABS[view].render();
    poll();
  }, 3000);
}

// ---------------------------------------------------------------- prioritization

function renderPrioritization() {
  const m = state.measures;
  byId("rules-version").textContent = m ? `rules ${m.rules_version}` : "-";
  const gaps = state.gaps;
  const avg = key => (gaps.length ? gaps.reduce((s, g) => s + num(g[key]), 0) / gaps.length : null);
  const factors = byId("factors");
  factors.replaceChildren(
    bar("Closure likelihood", avg("likelihood") ?? 0, fmt(avg("likelihood"))),
    bar("Financial value", avg("value") ?? 0, fmt(avg("value"))),
    bar("Engagement", avg("engagement") ?? 0, fmt(avg("engagement"))));

  const tiers = (m && m.tiers) || {};
  byId("summary-line").textContent =
    `${gaps.length.toLocaleString()} open gaps scored · ${ago(m && m.last_scored_at)}`;
  byId("tiers").replaceChildren(
    $("div", { class: "tile tile-high" }, $("span", {}, "HIGH TIER"), $("b", {}, String(tiers.high || 0))),
    $("div", { class: "tile tile-medium" }, $("span", {}, "MEDIUM TIER"), $("b", {}, String(tiers.medium || 0))),
    $("div", { class: "tile tile-low" }, $("span", {}, "LOW TIER"), $("b", {}, String(tiers.low || 0))));

  const counts = {};
  for (const g of gaps) counts[g.channel] = (counts[g.channel] || 0) + 1;
  const mix = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  byId("channels").replaceChildren(...(mix.length ? mix.map(([ch, n]) => {
    const share = n / gaps.length;
    return bar(CHANNELS[ch] || ch, share, `${Math.round(share * 100)}%`);
  }) : [$("p", { class: "empty" }, "No open gaps yet. Upload a batch to score gaps.")]));

  renderWorklist();
}

function renderWorklist() {
  const rows = state.gaps.filter(g => !state.tier || g.tier === state.tier);
  const body = byId("worklist");
  if (!rows.length) {
    body.replaceChildren($("tr", {}, $("td", { colspan: "6", class: "empty" },
      state.gaps.length ? "No gaps in this tier." : "No open gaps yet.")));
    return;
  }
  body.replaceChildren(...rows.map(g => $("tr", {
    tabindex: "0", title: "Show the gap panel", class: state.prioGap === g.gap_id ? "selected" : null,
    onclick: () => showPrioPanel(g.gap_id),
    onkeydown: e => { if (e.key === "Enter") showPrioPanel(g.gap_id); },
  },
  $("td", {}, $("div", { class: "pt" }, shortPt(g.patient_id)), $("div", {}, g.measure_name || g.measure)),
  $("td", {}, missingText(g)),
  $("td", {}, tierTag(g.tier)),
  $("td", { class: "num" }, fmt(num(g.composite))),
  $("td", {}, CHANNELS[g.channel] || g.channel || "-"),
  $("td", {}, daysUntil(g.next_visit)))));
}

function openOnEvidence(gapId) {
  state.scopeGap = gapId;
  location.hash = "evidence";
}

async function showPrioPanel(gapId) {
  state.prioGap = gapId;
  renderWorklist();
  byId("prio-panel-card").hidden = false;
  await renderGapPanel(gapId, "prio-panel");
  byId("prio-panel").prepend($("button", { type: "button", class: "btn btn-primary",
    onclick: () => openOnEvidence(gapId) }, "Open on Evidence Review"));
  byId("prio-panel-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- gap panel (as on the PR #9 demo page)

async function renderGapPanel(gapId, containerId) {
  const box = byId(containerId);
  if (!gapId) { box.replaceChildren($("p", { class: "empty" }, "Choose a gap.")); return; }
  const d = await api(`/api/gaps/${encodeURIComponent(gapId)}`).catch(err => ({ error: err.message }));
  if (d.error) { box.replaceChildren($("p", { class: "empty error" }, d.error)); return; }
  const g = d.gap;
  const b = d.breakdown;
  const sec = (n, title, ...kids) => $("section", { class: "psec" }, $("h3", {}, `${n} · ${title}`), ...kids);
  const kv = rows => $("dl", { class: "facts" }, ...rows.flatMap(([k, v]) => [$("dt", {}, k), $("dd", {}, v ?? "-")]));
  const steps = (list, total, label) => $("div", { class: "calc" }, ...list.flatMap((s, i) => [
    $("span", {}, s.label), $("span", { class: "v" }, `${i && s.change >= 0 ? "+" : ""}${fmt(s.change)}`)]),
  $("span", { class: "tot" }, label), $("span", { class: "v tot" }, fmt(total)));
  const parts = [sec(1, "Gap", kv([
    ["Measure", `${g.measure_name || g.measure_id} (${g.measure_id})`], ["Patient", shortPt(g.patient_id)],
    ["Missing", g.missing_element], ["Deadline", niceDate(g.deadline_date)],
    ["Last event", g.last_relevant_event_date
      ? `${niceDate(g.last_relevant_event_date)} ${g.last_relevant_event_description || ""}` : "none recorded"],
    ["Review status", (g.review_status || "").replaceAll("_", " ")]]))];
  if (!b) {
    parts.push($("p", { class: "note" }, "This gap has no score (it is off the work list or not scored yet)."));
  } else {
    const L = b.likelihood, V = b.value, E = b.engagement;
    parts.push(
      sec(2, "How likely it closes", steps(L.steps, L.result, "Closure likelihood"),
        L.raw !== L.result ? $("p", { class: "note small" }, `Raw ${fmt(L.raw)}, kept between ${L.min} and ${L.max}.`) : null),
      sec(3, "How much it helps the stars", V.weight ? kv([
        ["Done / eligible", `${V.numerator} / ${V.denominator}`],
        ["Rate (whole percent, as the score uses)", `${fmt(V.rate)}% → ${V.rounded}%`],
        ["Stars now", `${V.stars ?? "-"}`],
        ["Next star", V.next_cut != null ? `${V.next_cut}% (${V.target} done, ${V.needed} more)` : "already at the top"],
        ["Measure weight", String(V.weight)]]) : null,
      $("p", { class: "formula" }, V.needed
        ? `${V.reason} → ${V.weight} ÷ ${V.needed} ÷ ${V.divisor} = ${fmt(V.result, 3)}` : `${V.reason} → ${fmt(V.result, 3)}`)),
      sec(4, "How reachable", steps(E.steps, E.result, `Engagement (max ${E.cap})`)),
      sec(5, "Priority", $("p", { class: "formula" },
        `composite = ${fmt(L.result)} × ${fmt(V.result, 3)} = ${fmt(b.composite, 4)}`),
      kv([["Tier", g.tier || "-"], ["Next action", CHANNELS[b.channel] || b.channel],
        ["Why", Array.isArray(g.reasons) ? g.reasons.join(" · ") : g.reasons], ["Action reason", g.action_reason]])));
  }
  parts.push(sec(6, "AI details", ...aiDetails(gapId, d.ai_runs || [])));
  box.replaceChildren(...parts);
}

const block = (label, text, open = false) => $("details", open ? { open: "" } : {}, $("summary", {}, label),
  $("pre", { class: "code wrap" }, text || "-"));
const pretty = text => { try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; } };
const pageList = pages => (pages || []).map(p => `${p.document_id} p${p.page_no} (${p.method}` +
  `${p.handwritten ? ", handwritten" : ""})`).join(" · ") || "none";

function aiDetails(gapId, runs) {
  const out = [];
  const last = state.lastRun && state.lastRun.gapId === gapId ? state.lastRun.r : null;
  if (last) {
    out.push($("p", { class: "label-strong" }, `The run just made (run ${last.run_id})`),
      $("p", { class: "note small" }, `Pages sent: ${last.pages.map(p => `${p.document_id} p${p.page_no} (${p.method})`).join(" · ") || "none"}`));
    last.calls.forEach((c, i) => {
      const msgs = (c.request && c.request.messages) || [];
      out.push(block(`Call ${i + 1}: prompt sent (system and user)`,
        msgs.map(m => `[${m.role}]\n${m.content}`).join("\n\n")),
      c.error ? $("p", { class: "note small error" }, `Call ${i + 1} failed: ${c.error}`)
        : block(`Call ${i + 1}: the model's answer (HTTP ${c.status ?? "?"}, ${c.seconds ?? "?"} s)`, pretty(c.answer), true));
    });
    out.push(...last.kept.map(k => $("p", { class: "found kept" }, $("b", {}, "Kept: "),
      `${typeName(k.evidence_type)}, ${k.document_id} p${k.page_no}: “${k.excerpt}”`)),
    ...last.dropped.map(x => $("p", { class: "found dropped" }, $("b", {}, "Dropped: "),
      `${typeName(x.evidence_type || "?")}, ${x.document_id || "?"}: ${x.reason}`)));
  }
  const earlier = last ? runs.filter(a => a.run_id !== last.run_id) : runs;
  if (earlier.length) out.push($("p", { class: "label-strong" }, last ? "Earlier runs" : "Runs on this gap"));
  for (const a of earlier) {
    out.push($("div", { class: `ai-run ai-${a.status}` },
      $("div", {}, `Run ${a.run_id} · ${when(a.started_at)} · ${a.model_name} · ${a.status.replace("_", " ")} · ` +
        `${a.kept} kept, ${a.dropped_count} dropped · prompt ${a.prompt_version || "?"}`),
      $("p", { class: "note small" }, `Pages sent: ${pageList(a.pages_sent)}`),
      a.error ? $("p", { class: "note small error" }, a.error) : null,
      block("Raw answer", pretty(a.raw_response)),
      ...(a.dropped || []).map(x => $("p", { class: "note small" }, $("b", {}, `Dropped ${typeName(x.evidence_type || "?")}: `),
        x.reason + (x.evidence_id ? ` (evidence ${x.evidence_id.slice(0, 8)})` : "")))));
  }
  if (!out.length) out.push($("p", { class: "empty" }, "No AI runs on this gap yet."));
  return out;
}

// ---------------------------------------------------------------- evidence review

async function renderEvidence() {
  const ev = (state.measures && state.measures.evidence) || { proposed: 0, confirmed: 0, rejected: 0, by_model: [] };
  byId("n-proposed").textContent = ev.proposed;
  byId("n-confirmed").textContent = ev.confirmed;
  byId("n-rejected").textContent = ev.rejected;

  const ai = state.health && state.health.openrouter;
  const model = (ai && ai.model) || "unknown model";
  byId("model").replaceChildren($("option", {}, model));
  byId("model").title = ai && ai.key_valid && ai.model_listed ? "AI connected" : "AI not connected: check the key";

  const candidates = state.gaps.filter(g => g.has_documents);
  const scope = byId("scope");
  scope.replaceChildren(...(candidates.length ? candidates.map(g => $("option", {
    value: g.gap_id, selected: g.gap_id === state.scopeGap,
  }, `${shortPt(g.patient_id)} · ${g.measure_name || g.measure}${g.pending_count ? " · in review" : ""}`))
    : [$("option", { value: "" }, "No open gaps with documents")]));
  if (state.scopeGap && !candidates.some(g => g.gap_id === state.scopeGap)) {
    byId("run-result").textContent = "The chosen gap's patient has no documents yet, so it cannot be reviewed with AI.";
  }
  byId("run-extraction").disabled = !candidates.length;

  byId("acceptance").replaceChildren(...(ev.by_model.length ? ev.by_model.map(r => $("div", { class: "model-row" },
    $("span", {}, r.model), $("b", {}, r.acceptance_rate == null ? "no decisions" : `${fmt(r.acceptance_rate, 1)}%`)))
    : [$("p", { class: "empty" }, "No findings decided yet.")]));

  renderExports(ev);
  await Promise.all([renderQueue(), renderAiHistory(scope.value), renderGapPanel(scope.value, "gap-panel")]);
}

async function renderAiHistory(gapId) {
  const box = byId("ai-history");
  if (!gapId) { box.replaceChildren($("p", { class: "empty" }, "Choose a gap to see its AI runs.")); return; }
  const d = await api(`/api/gaps/${encodeURIComponent(gapId)}`).catch(() => null);
  const runs = (d && d.ai_runs) || [];
  if (!runs.length) { box.replaceChildren($("p", { class: "empty" }, "No AI runs for this gap yet.")); return; }
  box.replaceChildren(...runs.map(a => {
    const tokens = a.prompt_tokens == null ? "tokens n/a" : `${a.prompt_tokens} + ${a.output_tokens ?? 0} tokens`;
    const line = `${when(a.started_at)} · ${a.model_name} · ${a.status.replace("_", " ")} · ${a.kept} kept, ` +
      `${a.dropped_count} dropped · ${tokens} · ${a.duration_ms == null ? "n/a" : (a.duration_ms / 1000).toFixed(1)} s`;
    return $("div", { class: `ai-run ai-${a.status}` }, $("div", {}, line),
      a.error ? $("p", { class: "note small error" }, a.error) : null,
      a.dropped_count ? $("details", {}, $("summary", {}, `Dropped findings (${a.dropped_count})`),
        ...(a.dropped || []).map(x => $("p", { class: "note small" },
          $("b", {}, `${(x.evidence_type || "?").replaceAll("_", " ")}: `), x.reason))) : null);
  }));
}

function renderExports(ev) {
  const unexported = ev.supplemental_unexported || 0;
  const waiting = ev.supplemental_waiting_for_parts || 0;
  byId("export").textContent = `Export approved evidence (${unexported} ready` +
    `${waiting ? `, ${waiting} waiting for the other part of a two-part rule` : ""})`;
  byId("export").disabled = !unexported;
  const list = ev.exports || [];
  byId("exports").replaceChildren(...(list.length ? list.map(x => $("div", { class: "model-row" },
    $("span", {}, `#${x.id} · ${when(x.run_at)}`), $("b", { class: "mono" }, `${x.row_count} rows · ${x.file_sha256}`)))
    : [$("p", { class: "empty" }, `No exports yet. ${ev.supplemental_total || 0} supplemental records in total.`)]));
}

async function exportEvidence() {
  const out = byId("export-result");
  byId("export").disabled = true;
  out.classList.remove("error");
  out.textContent = "Exporting…";
  try {
    const r = await api("/api/supplemental/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer: store("hedis.reviewer") || null }) });
    out.textContent = r.rows ? `Exported ${r.rows} record${r.rows === 1 ? "" : "s"} to ` +
      `${r.file_path.split(/[\\/]/).pop()} (sha256 ${r.sha256.slice(0, 12)})` : "Nothing new to export.";
    await refresh();
  } catch (err) {
    out.textContent = err.message;
    out.classList.add("error");
    byId("export").disabled = false;
  }
}

async function renderQueue() {
  const pending = state.gaps.filter(g => g.pending_count > 0);
  const details = await Promise.all(pending.map(g => api(`/api/gaps/${g.gap_id}`).catch(() => null)));
  const items = [];
  for (const d of details) {
    if (!d) continue;
    for (const e of d.evidence) if (e.status === "proposed") items.push({ gap: d.gap, ev: e, impact: d.impact });
  }
  byId("queue-line").textContent = `${items.length} proposed finding${items.length === 1 ? "" : "s"} waiting for a reviewer`;
  const queue = byId("queue");
  if (!items.length) {
    queue.replaceChildren($("p", { class: "empty" },
      "Nothing to review. Run extraction on a gap to propose findings."));
    return;
  }
  queue.replaceChildren(...items.map(item => queueCard(item)));
}

function queueCard({ gap, ev, impact }) {
  const open = state.openEvidence === ev.evidence_id;
  const sec = (label, ...kids) => $("div", { class: "fsec" }, $("span", { class: "fsec-label" }, label),
    $("div", { class: "fsec-body" }, ...kids));
  const a = ev.attempt;
  const card = $("div", {
    class: `qcard${open ? " open" : ""}`, tabindex: open ? null : "0",
    onclick: open ? null : () => { state.openEvidence = ev.evidence_id; renderQueue(); },
    onkeydown: open ? null : e => { if (e.key === "Enter") { state.openEvidence = ev.evidence_id; renderQueue(); } },
  },
  open ? $("span", { class: "open-label" }, "OPEN FOR REVIEW") : null,
  $("div", { class: "qtop" },
    $("span", { class: "qtitle" }, `${shortPt(gap.patient_id)} · ${gap.measure_name || gap.measure_id}`),
    $("div", { class: "qmodel" }, $("span", { class: "conf" }, `${fmt(num(ev.confidence))} confidence`))),
  sec("GAP", `${gap.measure_name || gap.measure_id} · ${gap.missing_element} · deadline ${niceDate(gap.deadline_date)}` +
    ` · `, tierTag(gap.tier)),
  sec("FINDING", $("b", {}, findingSentence(ev, gap))),
  sec("WHY", ev.reason ? `${ev.reason}` : "The model gave no reason.",
    $("span", { class: "note small" }, " (the model's explanation, not a fact)")),
  sec("CHECK", checkLine(ev)),
  sec("QUOTE", quoteBlock(ev)),
  sec("IMPACT", impactLine(ev, impact) || "Not available."),
  sec("AI RUN", a ? `${a.model} · ${when(a.started_at)} · ${a.duration_ms == null ? "n/a" : (a.duration_ms / 1000).toFixed(1)} s` +
    ` · ${a.kept} kept, ${a.dropped} dropped` : `${ev.model || "model unknown"} (no attempt recorded)`),
  open ? decisionForm(ev) : null);
  return card;
}

const TYPE_NAMES = {
  colonoscopy: "Colonoscopy", fobt_or_fit: "Stool blood test (FIT)", hba1c: "HbA1c blood test",
  egfr: "Kidney blood test (eGFR)", uacr_ratio: "Kidney urine test (uACR)", mammogram: "Mammogram",
  retinal_or_dilated_eye_exam: "Diabetic eye exam", blood_pressure_reading: "Blood pressure", hospice: "Hospice care",
  esrd_or_dialysis: "Dialysis for kidney failure",
  frailty_and_advanced_illness_age_66_plus: "Frailty and serious illness",
  frailty_age_66_80_with_advanced_illness_or_age_81_plus_frailty_alone: "Frailty and serious illness",
};
const typeName = t => TYPE_NAMES[t] || (t || "").replaceAll("_", " ");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function niceDate(iso) {
  const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(iso || "");
  if (!m) return iso || "-";
  if (!m[2]) return m[1];
  if (!m[3]) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

// Built only from stored fields: the type's plain name, the service date as written, the value and the outcome.
function findingSentence(ev, gap) {
  const measure = gap.measure_name || gap.measure_id;
  const part = ev.part ? ` (${ev.part.replace("_", " ")} part)` : "";
  const when = ev.service_date_raw ? ` on ${niceDate(ev.service_date_raw)}` : "";
  const value = ev.value ? ` (${ev.value})` : "";
  const head = `${typeName(ev.evidence_type)}${part}${when}${value}.`;
  const rc = ev.rule_check || {};
  if (ev.evidence_kind === "exclusion") {
    return `${head} This removes the patient from ${measure}, so they no longer count against the rate.`;
  }
  const n = rc.part_label && /part (\d+) of (\d+)/.exec(rc.part_label);
  if (n) return `${head} This is ${n[1]} of ${n[2]} tests ${measure} needs.`;
  return `${head} This counts for ${measure}, so the gap can be closed once you confirm it.`;
}

function checkLine(ev) {
  const rc = ev.rule_check || {};
  const lines = [];
  if (rc.window_to || rc.window_from) {
    const span = rc.window_from ? `${niceDate(rc.window_from)} to ${niceDate(rc.window_to)}` : `any time up to ${niceDate(rc.window_to)}`;
    const served = rc.service_date_from === rc.service_date_to ? niceDate(rc.service_date_from)
      : `${niceDate(rc.service_date_from)} to ${niceDate(rc.service_date_to)}`;
    lines.push($("div", {}, `Window ${span}${rc.window_text ? ` (${rc.window_text})` : ""}: service date ${served} is `,
      $("b", { class: rc.inside_window ? "ok" : "error" }, rc.inside_window ? "inside" : "outside")));
  } else {
    lines.push($("div", {}, "No date window in the rule for this type."));
  }
  if (rc.part_label) {
    lines.push($("div", {}, $("b", {}, rc.part_label), ": ", ...(rc.parts || []).flatMap((p, i) => [i ? " · " : "",
      `${p.name} `, $("span", { class: `pstat pstat-${p.status}` }, p.status)])));
  }
  return $("div", {}, ...lines);
}

function quoteBlock(ev) {
  const c = ev.context;
  const text = c ? $("p", { class: "ctx" }, c.starts_mid_page ? "…" : "", c.text.slice(0, c.quote_start),
    $("mark", {}, c.text.slice(c.quote_start, c.quote_end)), c.text.slice(c.quote_end), c.ends_mid_page ? "…" : "")
    : $("p", { class: "quote" }, `“${ev.excerpt || ""}”`);
  return $("div", {}, text, ev.document_id ? $("a", {
    class: "src-link", target: "_blank", rel: "noopener",
    href: `/api/documents/${encodeURIComponent(ev.document_id)}/pages/${ev.page_no || 1}/image`,
    onclick: e => e.stopPropagation(),
  }, `${ev.document_id}, page ${ev.page_no || 1}: view the page image`) : null);
}

// Rates and stars from the star rating what-if (compare_measure); an exclusion uses the star rating rate and star functions directly.
function impactLine(ev, impact) {
  if (!impact) return null;
  if (!impact.available) return $("p", { class: "note small" }, `Impact if confirmed: ${impact.note}`);
  const pts = v => (v == null ? "n/a" : `${v >= 0 ? "+" : ""}${fmt(v)} points`);
  const pct = v => (v == null ? "n/a" : `${fmt(v)}%`);
  const star = v => (v == null ? "?" : v);
  if (ev.evidence_kind === "exclusion") {
    const x = impact.exclusion;
    return $("p", { class: "note small impact" }, "Impact if confirmed (patient leaves the denominator): ",
      $("b", {}, `rate ${pct(impact.rate_now)} → ${pct(x.rate_after)} (${pts(x.change_points)})`),
      ` · stars ${star(impact.stars_now)} → ${star(x.stars_after)}`);
  }
  const next = impact.closures_for_next_star == null
    ? (impact.stars_after >= 5 ? "already 5 stars" : "the next star is out of reach with the open gaps")
    : `${impact.closures_for_next_star} more closure${impact.closures_for_next_star === 1 ? "" : "s"} for the next star`;
  return $("p", { class: "note small impact" }, "Impact if confirmed: ",
    $("b", {}, `rate ${pct(impact.rate_now)} → ${pct(impact.rate_after)} (${pts(impact.change_points)})`),
    ` · stars ${star(impact.stars_now)} → ${star(impact.stars_after)} · ${next}`);
}

function decisionForm(ev) {
  const reviewer = $("input", { class: "input mono", id: `rv-${ev.evidence_id}`, value: store("hedis.reviewer") || "",
    placeholder: "e.g. abstractor.jsomanna", maxlength: "64", autocomplete: "off" });
  const notes = $("input", { class: "input", id: `nt-${ev.evidence_id}`, placeholder: "Required to reject",
    maxlength: "500" });
  const msg = $("p", { class: "note small full", role: "status" });
  const send = async decision => {
    const rid = reviewer.value.trim();
    if (!rid) { msg.textContent = "Enter your reviewer ID first."; msg.classList.add("error"); return; }
    if (decision === "reject" && !notes.value.trim()) {
      msg.textContent = "Add a note saying why the finding is rejected."; msg.classList.add("error"); return;
    }
    store("hedis.reviewer", rid);
    setAvatar();
    msg.classList.remove("error");
    msg.textContent = "Saving and re-scoring…";
    try {
      await api(`/api/evidence/${ev.evidence_id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, reason: notes.value.trim() || null, reviewer: rid }),
      });
      toast(decision === "confirm" ? "Finding confirmed. The gap waits for its other parts or for the engine."
        : "Finding rejected.");
      state.openEvidence = null;
      await refresh();
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    }
  };
  return $("div", { class: "decision", onclick: e => e.stopPropagation() },
    $("p", { class: "muted full" }, "Reviewer decision: a reviewer ID is required to leave \"proposed\""),
    $("div", {}, $("label", { class: "field-label", for: reviewer.id }, "Reviewer ID"), reviewer),
    $("div", {}, $("label", { class: "field-label", for: notes.id }, "Decision notes"), notes),
    $("button", { type: "button", class: "btn btn-confirm", onclick: () => send("confirm") }, "Confirm finding"),
    $("button", { type: "button", class: "btn btn-reject", onclick: () => send("reject") }, "Reject finding"),
    msg);
}

async function runExtraction() {
  const gapId = byId("scope").value;
  if (!gapId) return;
  const btn = byId("run-extraction");
  const out = byId("run-result");
  btn.disabled = true;
  out.classList.remove("error");
  out.textContent = "Reading this patient's documents with the model…";
  try {
    const r = await api(`/api/gaps/${gapId}/evidence`, { method: "POST" });
    const failed = (r.calls || []).filter(c => c.error).length;
    out.textContent = r.note || `${r.saved} finding${r.saved === 1 ? "" : "s"} proposed, ${r.dropped.length} dropped by ` +
      `the checks${r.skipped_already_saved ? `, ${r.skipped_already_saved} already proposed` : ""}` +
      `${failed ? `, ${failed} model call${failed === 1 ? "" : "s"} failed` : ""}.`;
    if (failed) out.classList.add("error");
    state.lastRun = { gapId, r };
    state.scopeGap = gapId;
    await refresh();
  } catch (err) {
    out.textContent = err.message;
    out.classList.add("error");
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- shell

function setAvatar() {
  const rid = store("hedis.reviewer") || "";
  const initials = rid.split(/[._@-]/).filter(Boolean).slice(-2).map(s => s[0].toUpperCase()).join("");
  byId("avatar").textContent = initials || "?";
}

function currentView() {
  const v = location.hash.slice(1);
  return VIEWS.includes(v) ? v : "dashboard";
}

async function show() {
  const view = currentView();
  for (const v of VIEWS) byId(`view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll("[data-view]")) a.classList.toggle("active", a.dataset.view === view);
  if (view === "ingestion") renderReset();
  if (view === "dashboard") renderDashboard();
  else if (view === "evidence") await renderEvidence();
  else if (view === "prioritization") renderPrioritization();
  else if (TABS[view]) {
    try {
      await TABS[view].render();
    } catch (err) {
      byId(`${view}-root`).replaceChildren($("p", { class: "empty error" }, `Could not load this screen: ${err.message}`));
    }
  }
}

async function refresh() {
  try {
    await loadAll();
    await show();
    poll();
  } catch (err) {
    toast(`Could not load data: ${err.message}`);
  }
}

document.querySelectorAll(".pill").forEach(p => p.addEventListener("click", () => {
  state.tier = p.dataset.tier;
  document.querySelectorAll(".pill").forEach(x => x.classList.toggle("active", x === p));
  renderWorklist();
}));
byId("refresh").addEventListener("click", refresh);
byId("reset").addEventListener("click", startOver);
byId("run-extraction").addEventListener("click", runExtraction);
byId("export").addEventListener("click", exportEvidence);
byId("scope").addEventListener("change", e => {
  state.scopeGap = e.target.value; byId("run-result").textContent = ""; renderAiHistory(e.target.value);
  renderGapPanel(e.target.value, "gap-panel");
});
window.addEventListener("hashchange", show);
setAvatar();
// The tab scripts load after this one; start once they have registered.
window.addEventListener("DOMContentLoaded", refresh);
