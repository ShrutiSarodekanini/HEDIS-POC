// Data Quality tab: data quality checks and anomalies per loaded batch, plus the coverage report.
// Everything comes from this app's /api/quality endpoints; text from the API is added as text nodes only.
"use strict";

(() => {
  const Q = "/api/quality";
  const REFRESH_MS = 15000;
  const POLL_MS = 3000;
  const ALL = "__all__";
  const SEVERITIES = ["critical", "high", "medium", "low"];
  const TYPES = {
    measure_evidence_missing: ["Measure evidence missing",
      "No records with the codes this measure needs were found, so source data or code mapping may be incomplete."],
    measure_low_patient_coverage: ["Low patient coverage for a measure",
      "Too few patients have evidence for this measure to trust its results."],
    measure_observation_missing: ["Measure observations missing",
      "The observation codes this measure needs are absent from the batch."],
    mapping_error: ["Mapping error", "An observation unit or value did not map as expected."],
    mapping_error_rate: ["Mapping error rate above threshold", "Too many records failed validation during ingestion."],
    coverage_gap_violation: ["Coverage gap over 45 days",
      "Some members have an enrollment gap longer than 45 days, which affects continuous enrollment."],
    required_feed_missing: ["Required feed missing", "No records arrived for a resource type the measures rely on."],
    feed_dropout: ["Feed dropped out", "A feed that normally has data sent none in this batch."],
    volume_drop: ["Feed volume dropped", "This feed sent far fewer records than its usual baseline."],
    volume_spike: ["Feed volume spiked", "This feed sent far more records than its usual baseline."],
  };
  const REASONS = {
    missing_or_invalid_unit: "Unit missing or not the expected one",
    value_out_of_expected_range: "Value outside the expected range",
  };
  const FACT_LABELS = {
    measure_id: "Measure", code: "Code", reason: "Reason", resource_type: "Resource type",
    clinical_evidence_mode: "Evidence needed", max_patient_count: "Patients with evidence",
    min_distinct_patients_with_evidence: "Minimum patients needed", error_count: "Errors", warning_count: "Warnings",
    total_resources: "Total records", error_rate: "Error rate", violation_count: "Members over the limit",
    threshold_gap_days: "Gap limit (days)", record_count: "Records", current_count: "Records now",
    baseline_avg_count: "Usual records", drop_pct: "Drop (%)", spike_pct: "Spike (%)",
    drop_threshold_pct: "Drop limit (%)", spike_threshold_pct: "Spike limit (%)", min_expected: "Lowest expected",
    max_expected: "Highest expected", actual_value: "Actual value", expected_unit: "Expected unit",
    actual_unit: "Actual unit", observation_codes: "Expected codes",
  };
  // Only these string values are shown, and only when they look like short codes, never free text.
  const CODE_KEYS = new Set(["measure_id", "reason", "resource_type", "code", "expected_unit", "actual_unit",
    "clinical_evidence_mode"]);
  const HIDDEN_KEYS = new Set(["patient_id", "observation_id", "check"]);
  const CODE = /^[A-Za-z0-9_.:%/\[\]{}-]{1,40}$/;

  const ui = { built: false };
  const dq = { batches: [], runs: [], measures: {}, notAvailable: [], anomalies: [], checks: [], batchId: "",
    runId: "", measure: ALL, severity: "All", batchPinned: false, runPinned: false, loading: false, rerunning: false,
    token: 0, pollTimer: null };
  const cov = { token: 0 };
  let lastRefresh = 0;

  // ---------------------------------------------------------------- helpers

  const humanize = s => String(s == null ? "" : s).replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
  const fmtNum = v => {
    if (v == null || v === "" || !Number.isFinite(Number(v))) return "-";
    return String(Math.round(Number(v) * 100) / 100);
  };
  const dateOnly = v => (v ? String(v).slice(0, 10) : "-");
  const sevKey = s => { const k = String(s || "").toLowerCase(); return SEVERITIES.includes(k) ? k : "other"; };
  const sevRank = s => { const i = SEVERITIES.indexOf(sevKey(s)); return i < 0 ? SEVERITIES.length : i; };
  const sevStatus = s => ({ critical: "status-bad", high: "status-warn", medium: "status-warn" })[sevKey(s)] || "";
  const statusClass = s => {
    const k = String(s || "").toLowerCase();
    if (["completed", "passed"].includes(k)) return "status-ok";
    if (["not_available", "running"].includes(k)) return "status-warn";
    if (k === "failed") return "status-bad";
    return "";
  };
  const pill = (text, cls) => $("span", { class: `status ${cls || ""}`.trim() }, humanize(text));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const isRunning = run => !!run && String(run.status).toLowerCase() === "running";
  const onTab = () => location.hash === "#quality";

  function showErr(el, err) {
    el.textContent = err ? (err.message || "Request failed") : "";
    el.hidden = !err;
  }

  const errLine = () => $("p", { class: "note small error", role: "status", hidden: true });

  function setOptions(select, items, preferred) {
    const wanted = preferred !== undefined ? preferred : select.value;
    select.replaceChildren(...items.map(([v, l]) => $("option", { value: v }, l)));
    select.value = items.some(([v]) => v === wanted) ? wanted : (items[0] ? items[0][0] : "");
  }

  const field = (label, control, id) => $("div", { class: "field" },
    $("label", { class: "field-label", for: id }, label), control);
  const kpi = (label, value) => $("div", { class: "kpi" }, $("span", {}, label), $("b", {}, String(value)));
  const tile = (cls, label, value) => $("div", { class: `tile ${cls}` }, $("span", {}, label), $("b", {}, String(value)));
  const table = (head, rows, right = []) => $("div", { class: "table-wrap" },
    $("table", { class: "table compact" },
      $("thead", {}, $("tr", {}, ...head.map((h, i) => $("th", { class: right.includes(i) ? "r" : null }, h)))),
      $("tbody", {}, ...rows)));

  // Wraps an event handler so a failure shows in the given error line instead of being thrown.
  const guard = (el, fn) => async (...args) => {
    try { await fn(...args); } catch (err) { showErr(el, err); }
  };

  // ---------------------------------------------------------------- not available note

  function buildNotAvailableCard() {
    ui.naList = $("div", { class: "stack" });
    ui.naCard = $("article", { class: "card", hidden: true },
      $("div", { class: "card-head" }, $("h2", {}, "Not available on the one schema")), ui.naList);
    return ui.naCard;
  }

  function renderNotAvailable() {
    const items = dq.notAvailable;
    ui.naCard.hidden = items.length === 0;
    ui.naList.replaceChildren(...items.map(i => $("p", { class: "note" }, $("b", {}, String(i.name || "")),
      ` ${i.reason || ""}`)));
  }

  // ---------------------------------------------------------------- data quality and anomalies

  function buildDqCard() {
    ui.dqErr = errLine();
    ui.batch = $("select", { id: "dq-batch", class: "input", onchange: guard(ui.dqErr, onBatch) });
    ui.measure = $("select", { id: "dq-measure", class: "input", onchange: onMeasure },
      $("option", { value: ALL }, "All measures"));
    ui.runSel = $("select", { id: "dq-run", class: "input", onchange: guard(ui.dqErr, onRunSelect) });
    ui.severity = $("select", { id: "dq-severity", class: "input", onchange: guard(ui.dqErr, onSeverity) },
      ...["All", "Critical", "High", "Medium", "Low"].map(s => $("option", { value: s }, s)));
    ui.measureHint = $("p", { class: "note small", hidden: true });
    ui.scopeNote = $("p", { class: "note small", hidden: true });
    ui.rerun = $("button", { type: "button", class: "btn btn-secondary", onclick: guard(ui.dqErr, onRerun) },
      "Run checks now");
    ui.rerunNote = $("p", { class: "note small", role: "status" });
    ui.runNote = $("p", { class: "note small", role: "status", hidden: true });
    ui.noRun = $("p", { class: "empty" },
      "No checks have run on this batch yet. They run on their own after loading; use Advanced rerun to start them now.");
    ui.context = $("p", { class: "note small" });
    ui.kpis = $("div", { class: "kpis" });
    ui.sev = $("div", { class: "bars" });
    ui.anoms = $("div", { class: "stack" });
    ui.checks = $("div", { class: "stack" });
    ui.results = $("div", { class: "view-root", hidden: true },
      ui.context, ui.kpis,
      $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Severity breakdown"), ui.sev),
      $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Anomalies"), ui.anoms),
      $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Checks"), ui.checks));
    ui.dqEmpty = $("p", { class: "empty" }, "No batch has been loaded yet. Upload files on ",
      $("a", { href: "#ingestion" }, "Data Ingestion"), "; the checks run on their own after loading.");
    ui.dqMain = $("div", { class: "view-root", hidden: true },
      $("div", { class: "field-row" }, field("Batch", ui.batch, "dq-batch"), field("Measure", ui.measure, "dq-measure"),
        field("Run", ui.runSel, "dq-run"), field("Severity", ui.severity, "dq-severity")),
      ui.measureHint, ui.scopeNote,
      $("details", { class: "dq-advanced" },
        $("summary", {}, "Advanced rerun"),
        $("div", { class: "dq-adv-body" },
          $("p", { class: "note small" }, "The checks run on their own after each batch is loaded on Data Ingestion. "
            + "Run checks now is only needed after the rule set changes."),
          $("div", { class: "btn-row" }, ui.rerun), ui.rerunNote)),
      ui.runNote, ui.noRun, ui.results);

    return $("article", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Data quality and anomaly detection")),
      ui.dqErr, ui.dqEmpty, ui.dqMain);
  }

  const currentRun = () => dq.runs.find(x => String(x.run_id) === dq.runId);

  async function onBatch() {
    dq.batchPinned = true;
    dq.runPinned = false;
    dq.batchId = ui.batch.value;
    await loadRuns();
  }

  function onMeasure() {
    dq.measure = ui.measure.value;
    if (!ui.results.hidden) renderResults();
  }

  async function onRunSelect() {
    dq.runPinned = true;
    dq.runId = ui.runSel.value;
    await loadRunData();
  }

  async function onSeverity() {
    dq.severity = ui.severity.value;
    await loadRunData();
  }

  async function onRerun() {
    if (!dq.batchId) { showErr(ui.dqErr, new Error("No batch has been loaded yet.")); return; }
    dq.rerunning = true;
    ui.rerun.disabled = true;
    ui.rerunNote.textContent = "Running the checks…";
    showErr(ui.dqErr, null);
    try {
      const r = await api(`${Q}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ batch_id: dq.batchId, measure_id: dq.measure === ALL ? null : dq.measure }),
      });
      toast("Data quality checks finished.");
      dq.runId = String(r.run_id);
      dq.runPinned = true;
    } finally {
      dq.rerunning = false;
      ui.rerun.disabled = false;
      ui.rerunNote.textContent = "";
    }
    await refreshDq();
  }

  async function refreshDq() {
    if (dq.loading || dq.rerunning) return;
    dq.loading = true;
    lastRefresh = Date.now();
    try {
      const r = await api(Q);
      dq.batches = (r.batches || []).filter(b => b.loaded);
      dq.notAvailable = r.not_available || [];
      dq.measures = {};
      for (const m of r.measures || []) if (m && m.measure_id) dq.measures[m.measure_id] = m;
      renderNotAvailable();
      setOptions(ui.measure, [[ALL, "All measures"], ...Object.keys(dq.measures).map(id => [id, id])]);
      dq.measure = ui.measure.value;

      const any = dq.batches.length > 0;
      ui.dqEmpty.hidden = any;
      ui.dqMain.hidden = !any;
      if (!any) { dq.batchId = ""; dq.runId = ""; showErr(ui.dqErr, null); return; }
      let sel = dq.batchId;
      if (!dq.batches.some(b => b.batch_id === sel) || !dq.batchPinned) sel = dq.batches[0].batch_id;
      if (sel !== dq.batchId) dq.runPinned = false;
      dq.batchId = sel;
      setOptions(ui.batch, dq.batches.map(b => [b.batch_id, `${String(b.batch_id).slice(0, 8)} · `
        + `${humanize(b.status)} · ${when(b.started_at)} (${plural(Number(b.runs || 0), "run")})`]), sel);
      showErr(ui.dqErr, null);
      await loadRuns();
    } catch (err) {
      showErr(ui.dqErr, err);
    } finally {
      dq.loading = false;
    }
  }

  async function loadRuns() {
    const batchId = dq.batchId;
    if (!batchId) return;
    const r = await api(`${Q}/runs?batch_id=${encodeURIComponent(batchId)}`);
    if (batchId !== dq.batchId) return;
    dq.runs = r.runs || [];
    let sel = dq.runId;
    if (!dq.runs.some(x => String(x.run_id) === sel) || !dq.runPinned) sel = dq.runs[0] ? String(dq.runs[0].run_id) : "";
    setOptions(ui.runSel, dq.runs.map(x => [String(x.run_id), `#${x.run_id} · ${humanize(x.status)} · `
      + `${x.measure_id || "All measures"}`]), sel);
    dq.runId = sel;
    await loadRunData();
  }

  function clearResults() {
    dq.anomalies = [];
    dq.checks = [];
    ui.results.hidden = true;
    ui.runNote.hidden = true;
    ui.noRun.hidden = true;
    ui.scopeNote.hidden = true;
    ui.measureHint.hidden = true;
  }

  async function loadRunData() {
    const token = ++dq.token;
    clearTimeout(dq.pollTimer);
    const run = currentRun();
    clearResults();
    if (!run) { ui.noRun.hidden = false; return; }
    if (String(run.status).toLowerCase() === "failed") {
      showErr(ui.dqErr, new Error(run.error || "The checks failed."));
      return;
    }
    showErr(ui.dqErr, null);
    if (isRunning(run)) {
      ui.runNote.textContent = "Checks are running…";
      ui.runNote.hidden = false;
      watchRun();
      return;
    }
    const q = dq.severity !== "All" ? `?severity=${dq.severity.toLowerCase()}` : "";
    const r = await api(`${Q}/runs/${encodeURIComponent(run.run_id)}${q}`);
    if (token !== dq.token) return;
    dq.anomalies = r.anomalies || [];
    dq.checks = r.checks || [];
    renderResults();
  }

  // While the chosen run is still running, look again every few seconds, and only while this tab is open.
  function watchRun() {
    clearTimeout(dq.pollTimer);
    if (!isRunning(currentRun()) || !onTab()) return;
    dq.pollTimer = setTimeout(() => {
      if (!onTab()) return;
      loadRuns().catch(err => showErr(ui.dqErr, err));
    }, POLL_MS);
  }

  const anomalyInMeasure = a => dq.measure === ALL || a.measure_id === dq.measure;
  const checkInMeasure = c => dq.measure === ALL || c.measure_id === dq.measure;

  function renderResults() {
    const anomalies = dq.anomalies.filter(anomalyInMeasure);
    const checks = dq.checks.filter(checkInMeasure);
    const passed = checks.filter(c => c.status === "passed").length;
    const failed = checks.filter(c => c.status === "failed").length;
    const missing = checks.filter(c => c.status === "not_available").length;
    const run = currentRun() || {};
    const scope = run.measure_id || null;
    const summary = run.summary || {};

    ui.results.hidden = false;
    const meta = dq.measures[dq.measure];
    ui.measureHint.hidden = !(meta && meta.description);
    ui.measureHint.textContent = meta && meta.description ? `${dq.measure}: ${meta.description}` : "";
    ui.scopeNote.hidden = !scope || dq.measure === scope;
    ui.scopeNote.textContent = scope ? (dq.measure === ALL
      ? `This run was executed for measure ${scope} only. To see every measure, run checks with All measures selected.`
      : `This run covers measure ${scope}. With ${dq.measure} selected, results can be empty or partial.`) : "";
    ui.context.textContent = `Batch ${String(run.batch_id || dq.batchId).slice(0, 8)} · run #${run.run_id} · `
      + `${humanize(run.status)} · scope ${scope || "all measures"}`
      + (typeof summary.feed_report === "string" ? ` · Feed report: ${summary.feed_report}` : "");

    ui.kpis.replaceChildren(kpi("CHECKS RUN", checks.length), kpi("PASSED", passed), kpi("FAILED", failed),
      kpi("ANOMALIES RAISED", anomalies.length), missing ? kpi("NOT AVAILABLE", missing) : null);
    renderSeverity(anomalies);
    renderAnomalies(anomalies);
    renderChecks(checks);
  }

  function renderSeverity(anomalies) {
    const counts = {};
    for (const a of anomalies) {
      const k = String(a.severity || "unknown").toLowerCase();
      counts[k] = (counts[k] || 0) + 1;
    }
    const keys = [...SEVERITIES.filter(k => counts[k]), ...Object.keys(counts).filter(k => !SEVERITIES.includes(k)).sort()];
    ui.sev.replaceChildren(...(keys.length ? keys.map(k => {
      const row = bar(humanize(k), counts[k] / anomalies.length, String(counts[k]));
      row.classList.add(`dq-bar-${sevKey(k)}`);
      return row;
    }) : [$("p", { class: "empty" }, "No anomalies for these filters.")]));
  }

  // Shows only numbers, booleans and short codes from an anomaly's details, never free text or a patient id.
  function safeFacts(a) {
    const d = a.details && typeof a.details === "object" ? a.details : {};
    const out = [];
    for (const [key, value] of Object.entries(d)) {
      if (HIDDEN_KEYS.has(key) || value == null) continue;
      let shown = null;
      if (typeof value === "number") shown = fmtNum(value);
      else if (typeof value === "boolean") shown = value ? "Yes" : "No";
      else if (typeof value === "string" && CODE_KEYS.has(key) && CODE.test(value)) {
        shown = key === "reason" ? (REASONS[value] || humanize(value)) : value;
      } else if (key === "observation_codes" && Array.isArray(value) && value.length
        && value.every(v => typeof v === "string" && CODE.test(v))) {
        shown = value.slice(0, 6).join(", ") + (value.length > 6 ? ` and ${value.length - 6} more` : "");
      }
      if (shown != null) out.push([FACT_LABELS[key] || humanize(key), shown]);
    }
    if (Array.isArray(d.evidence_checks)) {
      const missing = d.evidence_checks.filter(e => e && e.row_count === 0 && typeof e.resource_type === "string"
        && CODE.test(e.resource_type)).map(e => e.resource_type);
      out.push(["Missing sources", missing.length ? missing.join(", ") : "None"]);
    }
    return out;
  }

  function anomalyCard(a) {
    const sev = String(a.severity || "unknown").toLowerCase();
    const facts = safeFacts(a);
    const bits = [];
    if (a.resource_type) bits.push(`Resource: ${a.resource_type}`);
    if (a.patient_id) bits.push(shortPt(String(a.patient_id)));
    if (a.detected_at) bits.push(when(a.detected_at));
    return $("article", { class: `dq-card dq-card-${sevKey(sev)}` },
      $("div", { class: "dq-card-head" }, pill(sev, sevStatus(sev)),
        $("span", { class: "muted small" }, bits.join(" · "))),
      facts.length ? $("dl", { class: "facts" }, ...facts.flatMap(([k, v]) => [$("dt", {}, k),
        $("dd", { class: "mono" }, v)])) : null);
  }

  function renderAnomalies(list) {
    if (!list.length) {
      ui.anoms.replaceChildren($("p", { class: "empty" }, "No anomalies for these filters."));
      return;
    }
    const groups = new Map();
    for (const a of list) {
      const type = a.check_type || a.anomaly_type || "unknown";
      if (!groups.has(type)) groups.set(type, []);
      groups.get(type).push(a);
    }
    const ordered = [...groups.entries()].map(([type, items]) => [type, items.sort((x, y) => sevRank(x.severity)
      - sevRank(y.severity))]).sort((x, y) => sevRank(x[1][0].severity) - sevRank(y[1][0].severity)
      || y[1].length - x[1].length);
    ui.anoms.replaceChildren(...ordered.map(([type, items]) => {
      const info = TYPES[type] || [humanize(type), ""];
      const shown = items.slice(0, 20);
      return $("section", { class: "dq-group" },
        $("div", { class: "dq-group-head" }, $("h3", {}, info[0]), $("span", { class: "status" }, String(items.length))),
        info[1] ? $("p", { class: "note small" }, info[1]) : null,
        $("div", { class: "dq-cards" }, ...shown.map(anomalyCard)),
        items.length > shown.length ? $("p", { class: "note small" }, `and ${items.length - shown.length} more`) : null);
    }));
  }

  function renderChecks(checks) {
    if (!checks.length) {
      ui.checks.replaceChildren($("p", { class: "empty" }, "No checks for these filters."));
      return;
    }
    const shown = checks.slice(0, 200);
    ui.checks.replaceChildren(
      $("div", { class: "dq-scroll" }, table(["Check", "Status", "Metric", "Value", "Threshold", "Resource type"],
        shown.map(c => $("tr", {}, $("td", {}, humanize(c.check_name)),
          $("td", {}, pill(c.status || "unknown", statusClass(c.status))),
          $("td", { class: "mono small" }, c.metric_name || "-"),
          $("td", { class: "r" }, fmtNum(c.metric_value)),
          $("td", { class: "r" }, fmtNum(c.threshold_value)),
          $("td", {}, c.resource_type || "-"))), [3, 4])),
      checks.length > shown.length ? $("p", { class: "note small" }, `and ${checks.length - shown.length} more`) : null);
  }

  // ---------------------------------------------------------------- coverage report

  const coverageNote = limit => "Enrollment gaps per member, from the coverage periods (gaps over "
    + `${limit} days break continuous enrollment)`;

  function buildCoverageCard() {
    ui.covErr = errLine();
    const reload = guard(ui.covErr, loadCoverage);
    ui.payer = $("select", { id: "dq-payer", class: "input", onchange: reload }, $("option", { value: "All" }, "All"));
    ui.compliance = $("select", { id: "dq-compliance", class: "input", onchange: reload },
      ...["All", "Compliant", "Violation"].map(v => $("option", { value: v }, v)));
    ui.from = $("input", { id: "dq-from", class: "input", type: "date", onchange: reload });
    ui.to = $("input", { id: "dq-to", class: "input", type: "date", onchange: reload });
    ui.covNote = $("span", { class: "muted small" }, ` ${coverageNote(45)}`);
    ui.covTiles = $("div", { class: "tile-row" });
    ui.covRows = $("div", { class: "stack" });
    ui.cov = $("details", { class: "card dq-coverage", ontoggle: () => { if (ui.cov.open) reload(); } },
      $("summary", {}, "Coverage report", ui.covNote),
      $("div", { class: "dq-cov-body" },
        $("div", { class: "field-row" }, field("Payer", ui.payer, "dq-payer"),
          field("Compliance", ui.compliance, "dq-compliance"), field("Start date", ui.from, "dq-from"),
          field("End date", ui.to, "dq-to")),
        ui.covErr, ui.covTiles, ui.covRows));
    return ui.cov;
  }

  async function loadCoverage() {
    const token = ++cov.token;
    const q = new URLSearchParams();
    if (ui.payer.value !== "All") q.set("payer", ui.payer.value);
    if (ui.compliance.value !== "All") q.set("compliance", ui.compliance.value);
    if (ui.from.value) q.set("start_date", ui.from.value);
    if (ui.to.value) q.set("end_date", ui.to.value);
    const r = await api(`${Q}/coverage?${q}`);
    if (token !== cov.token) return;
    showErr(ui.covErr, null);
    const records = r.coverage_records || [];
    const s = r.summary || {};
    setOptions(ui.payer, [["All", "All"], ...(r.payers || []).map(p => [p, p])]);
    ui.covNote.textContent = ` ${coverageNote(s.gap_limit_days == null ? 45 : s.gap_limit_days)}`;

    ui.covTiles.replaceChildren(tile("tile-low", "TOTAL COVERED", Number(s.total_covered || 0).toLocaleString()),
      tile("tile-high", "CONTINUOUS ENROLLMENT", Number(s.continuous_enrollment || 0).toLocaleString()),
      tile("tile-bad", "VIOLATIONS", Number(s.hedis_violations || 0).toLocaleString()),
      tile("tile-medium", "COMPLIANCE RATE", s.total_covered ? `${s.compliance_rate}%` : "N/A"));
    const shown = records.slice(0, 100);
    ui.covRows.replaceChildren(
      records.length
        ? table(["Patient", "Payer", "Period", "Gap days", "Compliant"], shown.map(c => $("tr", {},
          $("td", {}, $("span", { class: "pt" }, shortPt(String(c.patient_id || "")))),
          $("td", {}, c.payer || "-"),
          $("td", {}, `${dateOnly(c.period_start)} to ${dateOnly(c.period_end)}`),
          $("td", { class: "r" }, c.gap_days ? `${c.gap_days}` : "-"),
          $("td", {}, c.continuous_enrollment_compliant ? pill("Compliant", "status-ok")
            : pill("Violation", "status-bad")))), [3])
        : $("p", { class: "empty" }, "No coverage records found. Load a batch on Data Ingestion first."),
      records.length > shown.length ? $("p", { class: "note small" },
        `Showing ${shown.length} of ${records.length.toLocaleString()} records.`) : null);
  }

  // ---------------------------------------------------------------- tab

  function build() {
    if (ui.built) return;
    ui.built = true;
    byId("quality-root").replaceChildren($("div", { class: "view-root" },
      buildNotAvailableCard(), buildDqCard(), buildCoverageCard()));
  }

  TABS.quality = {
    render: async () => {
      build();
      if (Date.now() - lastRefresh > REFRESH_MS) {
        refreshDq();
        if (ui.cov.open) guard(ui.covErr, loadCoverage)();
      } else {
        watchRun();
      }
    },
  };
})();
