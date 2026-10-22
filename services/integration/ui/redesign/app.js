// HEDIS Ops Console: Prioritization and Evidence Review screens on the integration API.
// Every value comes from the API; text is added as text nodes, never as HTML.
"use strict";

const CHANNELS = {
  abstractor_review: "Abstractor review", chart_review: "Chart review", point_of_care_alert: "Point-of-care alert",
  provider_gap_list: "Provider gap list", member_outreach_call: "Phone", mailed_fit_kit: "Mailed kit",
};
const state = { gaps: [], tier: "", measures: null, health: null, openEvidence: null, scopeGap: null };

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
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
const shortPt = id => (id ? `pt-${id.slice(0, 4)}…` : "pt-?");
const tierTag = t => $("span", { class: `tag tag-${t}` }, t ? t[0].toUpperCase() + t.slice(1) : "–");

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
  return rest ? rest[0].toUpperCase() + rest.slice(1) : "–";
}

function ago(iso) {
  if (!iso) return "not scored yet";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `updated ${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `updated ${hours}h ago` : `updated ${Math.round(hours / 24)}d ago`;
}

function daysUntil(isoDate) {
  if (!isoDate) return "–";
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
  const [measures, gaps, health] = await Promise.all([api("/api/measures"), api("/api/gaps"),
    api("/api/health").catch(() => null)]);
  state.measures = measures;
  state.gaps = gaps;
  state.health = health;
}

// ---------------------------------------------------------------- prioritization

function renderPrioritization() {
  const m = state.measures;
  byId("rules-version").textContent = m ? `rules ${m.rules_version}` : "–";
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
    tabindex: "0", title: "Open on Evidence Review",
    onclick: () => openOnEvidence(g.gap_id),
    onkeydown: e => { if (e.key === "Enter") openOnEvidence(g.gap_id); },
  },
  $("td", {}, $("div", { class: "pt" }, shortPt(g.patient_id)), $("div", {}, g.measure_name || g.measure)),
  $("td", {}, missingText(g)),
  $("td", {}, tierTag(g.tier)),
  $("td", { class: "num" }, fmt(num(g.composite))),
  $("td", {}, CHANNELS[g.channel] || g.channel || "–"),
  $("td", {}, daysUntil(g.next_visit)))));
}

function openOnEvidence(gapId) {
  state.scopeGap = gapId;
  location.hash = "evidence";
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

  await renderQueue();
}

async function renderQueue() {
  const pending = state.gaps.filter(g => g.pending_count > 0);
  const details = await Promise.all(pending.map(g => api(`/api/gaps/${g.gap_id}`).catch(() => null)));
  const items = [];
  details.forEach((d, i) => {
    if (!d) return;
    for (const e of d.evidence) if (e.status === "proposed") items.push({ gap: pending[i], ev: e });
  });
  byId("queue-line").textContent = `${items.length} proposed finding${items.length === 1 ? "" : "s"} waiting for a reviewer`;
  const queue = byId("queue");
  if (!items.length) {
    queue.replaceChildren($("p", { class: "empty" },
      "Nothing to review. Run extraction on a gap to propose findings."));
    return;
  }
  queue.replaceChildren(...items.map(item => queueCard(item)));
}

function queueCard({ gap, ev }) {
  const open = state.openEvidence === ev.evidence_id;
  const card = $("div", {
    class: `qcard${open ? " open" : ""}`, tabindex: open ? null : "0",
    onclick: open ? null : () => { state.openEvidence = ev.evidence_id; renderQueue(); },
    onkeydown: open ? null : e => { if (e.key === "Enter") { state.openEvidence = ev.evidence_id; renderQueue(); } },
  },
  open ? $("span", { class: "open-label" }, "OPEN FOR REVIEW") : null,
  $("div", { class: "qtop" },
    $("span", { class: "qtitle" }, `${shortPt(gap.patient_id)} · ${gap.measure_name || gap.measure}`),
    $("div", { class: "qmodel" }, $("span", { class: "conf" }, `${fmt(num(ev.confidence))} confidence`),
      $("div", {}, ev.model || ""))),
  $("p", { class: "quote" }, `“${ev.excerpt || ""}”`),
  $("p", { class: "finding" }, "Proposed finding: ", $("b", {}, findingText(ev))),
  ev.document_id ? $("a", {
    class: "src-link", target: "_blank", rel: "noopener",
    href: `/api/documents/${encodeURIComponent(ev.document_id)}/pages/${ev.page_no || 1}/image`,
    onclick: e => e.stopPropagation(),
  }, `View source: ${ev.document_id}, page ${ev.page_no || 1}`) : null,
  open ? decisionForm(ev) : null);
  return card;
}

function findingText(ev) {
  const parts = [ev.evidence_type && ev.evidence_type.replaceAll("_", " "), ev.value,
    ev.service_date_raw ? `on ${ev.service_date_raw}` : null].filter(Boolean).join(" ");
  const kind = ev.evidence_kind === "exclusion" ? "excludes the member from the measure" : "meets the numerator";
  return parts ? `${parts}: ${kind}` : (ev.proposed_finding || "–");
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
      toast(decision === "confirm" ? "Finding confirmed. The gap waits for the engine to close it."
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
  return location.hash === "#evidence" ? "evidence" : "prioritization";
}

async function show() {
  const view = currentView();
  for (const v of ["prioritization", "evidence"]) byId(`view-${v}`).hidden = v !== view;
  for (const a of document.querySelectorAll("[data-view]")) a.classList.toggle("active", a.dataset.view === view);
  if (view === "evidence") await renderEvidence(); else renderPrioritization();
}

async function refresh() {
  try {
    await loadAll();
    await show();
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
byId("run-extraction").addEventListener("click", runExtraction);
byId("scope").addEventListener("change", e => { state.scopeGap = e.target.value; byId("run-result").textContent = ""; });
window.addEventListener("hashchange", show);
setAvatar();
refresh();
