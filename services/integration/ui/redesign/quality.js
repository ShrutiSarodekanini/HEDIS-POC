// Data Quality tab: Pallavi's Run Ingestion, Data Quality and Coverage Report screens in this design.
// Her API runs unchanged on her own schema and is reached through /team/pallavi/api/...; text is added as text nodes.
"use strict";

(() => {
  const P = "/team/pallavi";
  const POLL_MS = 5000;
  const MAX_TRIES = 360;
  const REFRESH_MS = 15000;
  const ALL = "__all__";
  const TERMINAL = new Set(["completed", "partially_completed", "failed", "interrupted"]);
  const SOURCES = [
    ["Synthea CSV", "synthea_csv"],
    ["FHIR R4 bundle", "synthea_csv"],
    ["EHR FHIR R4", "ehr_fhir_r4"],
    ["C-CDA / HIE", "ccda_hie"],
    ["Claims 837", "claims_837"],
    ["NCPDP Pharmacy", "ncpdp_pharmacy"],
  ];
  const STAGE_LABELS = {
    csv_input: "Read the source files", mapping: "Map records", fhir_generation: "Build FHIR resources",
    validation: "Validate FHIR", postgresql_storage: "Store in the database",
  };
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
    min_distinct_patients_with_evidence: "Minimum patients needed", error_count: "Errors",
    total_resources: "Total records", error_rate: "Error rate", violation_count: "Members over the limit",
    threshold_gap_days: "Gap limit (days)", record_count: "Records", current_count: "Records now",
    baseline_avg_count: "Usual records", drop_pct: "Drop (%)", spike_pct: "Spike (%)",
    drop_threshold_pct: "Drop limit (%)", spike_threshold_pct: "Spike limit (%)", min_expected: "Lowest expected",
    max_expected: "Highest expected", actual_value: "Actual value", expected_unit: "Expected unit",
    actual_unit: "Actual unit", observation_codes: "Expected codes", metric: "Metric", threshold: "Threshold",
    expected: "Expected", actual: "Actual", unit: "Unit",
  };
  // Only these string values are shown, and only when they look like short codes, never free text.
  const CODE_KEYS = new Set(["measure_id", "metric", "reason", "resource_type", "threshold", "expected", "actual",
    "unit", "code", "expected_unit", "actual_unit", "clinical_evidence_mode"]);
  const HIDDEN_KEYS = new Set(["patient_id", "observation_pk"]);
  const CODE = /^[A-Za-z0-9_.:%/\[\]{}-]{1,40}$/;

  const ui = { pathTouched: false, built: false };
  const dq = { batches: [], runs: [], measureMeta: {}, anomalies: [], checks: [], summary: null, batchId: "", runId: "",
    measure: ALL, severity: "All", batchPinned: false, runPinned: false, loading: false, rerunning: false, token: 0 };
  const cov = { payers: new Set(), token: 0 };
  const poll = { token: 0, timer: null, id: "", label: "" };
  let lastRefresh = 0;
  let started = false;

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
    if (["completed", "passed", "ok", "validated"].includes(k)) return "status-ok";
    if (["partially_completed", "warning", "partial", "quarantined"].includes(k)) return "status-warn";
    if (["failed", "rejected", "interrupted", "error"].includes(k)) return "status-bad";
    return "";
  };
  const pill = (text, cls) => $("span", { class: `status ${cls || ""}`.trim() }, humanize(text));
  const adapterFor = () => (SOURCES.find(s => s[0] === ui.source.value) || SOURCES[0])[1];

  function detailText(detail, status) {
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) return detail.map(d => (d && d.msg) || "invalid value").join("; ");
    if (detail && Array.isArray(detail.missing_required_files)) {
      return `Missing required files: ${detail.missing_required_files.join(", ")}`;
    }
    if (detail && detail.adapter) return `Unknown adapter ${detail.adapter}`;
    return `Request failed (${status})`;
  }

  // Like api() in app.js, but keeps the HTTP status so a 503 can be told apart, and reads her error shapes.
  async function call(path, options = {}) {
    const res = await fetch(`${P}${path}`, options);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(detailText(body && body.detail, res.status));
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function showErr(el, err) {
    const msg = err ? (err.message || "Request failed") : "";
    el.textContent = err && err.status === 503 ? `Pallavi's API is not answering: ${msg}` : msg;
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
  const kpi = (label, value, note) => $("div", { class: "kpi" }, $("span", {}, label), $("b", {}, String(value)),
    note ? $("small", {}, note) : null);
  const tile = (cls, label, value) => $("div", { class: `tile ${cls}` }, $("span", {}, label), $("b", {}, String(value)));
  const table = (head, rows, right = []) => $("div", { class: "table-wrap" },
    $("table", { class: "table compact" },
      $("thead", {}, $("tr", {}, ...head.map((h, i) => $("th", { class: right.includes(i) ? "r" : null }, h)))),
      $("tbody", {}, ...rows)));

  // Wraps an event handler so a failure shows in the given error line instead of being thrown.
  const guard = (el, fn) => async (...args) => {
    try { await fn(...args); } catch (err) { showErr(el, err); }
  };

  // ---------------------------------------------------------------- shell and banner

  function pallavi() {
    return state.health && state.health.team && state.health.team.pallavi;
  }

  function ensureShell() {
    if (ui.shell) return;
    ui.shell = true;
    ui.root = byId("quality-root");
    ui.banner = $("div", { class: "separate-data" });
    ui.off = $("p", { class: "note", hidden: true });
    ui.body = $("div", { class: "view-root", hidden: true });
    ui.root.replaceChildren(ui.banner, ui.off, ui.body);
  }

  function renderBanner(p) {
    if (!p || !p.enabled) {
      ui.banner.hidden = true;
      ui.off.hidden = false;
      ui.off.textContent = p ? "Pallavi's data quality is switched off in this app (no schema is set), so this screen "
        + "has nothing to show." : "The app status is not available yet, so Pallavi's data quality cannot be shown.";
      return;
    }
    ui.off.hidden = true;
    ui.banner.hidden = false;
    ui.banner.replaceChildren($("span", {}, $("b", {}, "Separate data:"),
      ` Pallavi's ingestion and checks run on her own copy of the data (schema ${p.schema}), not on the team tables `
      + `the other screens use. Her API: ${p.api}, worker: ${p.worker}.`));
  }

  // ---------------------------------------------------------------- section 1: run her ingestion

  function buildRunCard() {
    ui.source = $("select", { id: "dq-source", class: "input", onchange: onSourceChange },
      ...SOURCES.map(([label]) => $("option", { value: label }, label)));
    ui.label = $("input", { id: "dq-label", class: "input", type: "text", maxlength: "120", autocomplete: "off",
      placeholder: "A label for this run (kept on this page only)" });
    ui.prefillNote = $("p", { class: "note small" });
    ui.path = $("input", { id: "dq-path", class: "input mono", type: "text", autocomplete: "off",
      placeholder: "Folder on the server",
      oninput: () => { ui.pathTouched = true; ui.prefillNote.textContent = ""; } });
    ui.files = $("input", { id: "dq-files", class: "input", type: "file", multiple: true, accept: ".csv",
      onchange: onUpload });
    ui.uploadNote = $("p", { class: "note small" });
    ui.adapterNote = $("p", { class: "note small" });
    ui.inspectBtn = $("button", { type: "button", class: "btn btn-secondary", onclick: onInspect }, "Inspect dataset");
    ui.runBtn = $("button", { type: "button", class: "btn btn-primary", onclick: onRun }, "Run ingestion");
    ui.runErr = errLine();
    ui.inspect = $("div", { class: "stack", hidden: true });
    ui.jobPill = $("span", { class: "status", hidden: true });
    ui.job = $("div", { class: "dq-job", hidden: true });
    onSourceChange();

    return $("article", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Run her ingestion on the same files"), ui.jobPill),
      $("p", { class: "note" }, "Uses the files from the latest upload on Data Ingestion, so both copies hold the "
        + "same batch."),
      $("div", { class: "field-row" }, field("Source system", ui.source, "dq-source"),
        field("Source description", ui.label, "dq-label")),
      $("div", { class: "field" }, $("label", { class: "field-label", for: "dq-path" }, "Input path"), ui.path,
        ui.prefillNote),
      $("div", { class: "field" },
        $("label", { class: "field-label", for: "dq-files" }, "Or upload files to her copy"), ui.files, ui.uploadNote),
      ui.adapterNote,
      $("div", { class: "btn-row" }, ui.inspectBtn, ui.runBtn),
      ui.runErr, ui.inspect, ui.job);
  }

  function onSourceChange() {
    const ccda = ui.source.value === "C-CDA / HIE";
    ui.files.setAttribute("accept", ccda ? ".xml,.ccda" : ".csv");
    ui.path.placeholder = ccda ? "Folder of C-CDA files on the server" : "Folder on the server";
    const bundle = ui.source.value === "FHIR R4 bundle" ? " (her code sends this source to the Synthea CSV adapter)" : "";
    ui.adapterNote.textContent = `Adapter used: ${adapterFor()}${bundle}.`;
  }

  async function prefillPath() {
    if (ui.pathTouched) return;
    try {
      const flow = await api("/api/flow");
      const latest = flow.latest_ingest_job;
      if (!latest || !latest.id) {
        ui.prefillNote.textContent = "No upload on Data Ingestion yet. Enter a folder or upload files here.";
        return;
      }
      const job = await api(`/team/jyothi/api/jobs/${encodeURIComponent(latest.id)}`);
      if (job.input_dir && !ui.pathTouched) {
        ui.path.value = job.input_dir;
        ui.prefillNote.textContent = `Filled in from the latest upload on Data Ingestion (job ${latest.id.slice(0, 8)}).`;
      }
    } catch {
      ui.prefillNote.textContent = "Could not read the latest upload from Data Ingestion. Enter the folder by hand.";
    }
  }

  async function onUpload() {
    const files = Array.from(ui.files.files || []);
    if (!files.length) return;
    const ccda = ui.source.value === "C-CDA / HIE";
    ui.uploadNote.textContent = `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`;
    showErr(ui.runErr, null);
    try {
      const form = new FormData();
      files.forEach(f => form.append("files", f, f.name));
      const r = await call(ccda ? "/api/datasets/upload-ccda" : "/api/datasets/upload", { method: "POST", body: form });
      ui.path.value = r.input_dir || "";
      ui.pathTouched = true;
      ui.prefillNote.textContent = "Folder from your upload to her copy.";
      const saved = (r.saved_files || []).length;
      ui.uploadNote.textContent = `Uploaded ${saved} ${ccda ? "C-CDA" : "CSV"} file${saved === 1 ? "" : "s"}.`;
      renderInspect(r);
    } catch (err) {
      ui.uploadNote.textContent = "";
      showErr(ui.runErr, err);
    } finally {
      ui.files.value = "";
    }
  }

  function renderInspect(d) {
    const found = Array.isArray(d.found_files) ? d.found_files : [];
    const counts = d.row_counts && typeof d.row_counts === "object" ? d.row_counts : {};
    const names = [...new Set([...found, ...Object.keys(counts)])];
    const missing = Array.isArray(d.missing_required_files) ? d.missing_required_files : [];
    const total = d.total_rows != null ? d.total_rows : Object.values(counts).reduce((s, v) => s + Number(v || 0), 0);
    ui.inspect.replaceChildren(
      names.length
        ? table(["File", "Rows"], names.map(n => $("tr", {}, $("td", { class: "mono" }, n),
          $("td", { class: "r" }, counts[n] == null ? "-" : Number(counts[n]).toLocaleString()))), [1])
        : $("p", { class: "empty" }, "No supported files were found in that folder."),
      $("p", { class: "note small" }, `${names.length} file${names.length === 1 ? "" : "s"} found, `
        + `${Number(total).toLocaleString()} rows in total.`),
      missing.length ? $("p", { class: "note small error" }, `Missing required files: ${missing.join(", ")}` ) : null);
    ui.inspect.hidden = false;
  }

  async function onInspect() {
    const path = ui.path.value.trim();
    showErr(ui.runErr, null);
    if (!path) { showErr(ui.runErr, new Error("Enter the input path first.")); return; }
    try {
      renderInspect(await call(`/api/datasets/inspect?input_dir=${encodeURIComponent(path)}`));
    } catch (err) {
      showErr(ui.runErr, err);
    }
  }

  async function onRun() {
    const path = ui.path.value.trim();
    showErr(ui.runErr, null);
    if (!path) { showErr(ui.runErr, new Error("Enter the input path first.")); return; }
    ui.runBtn.disabled = true;
    try {
      const r = await call(`/api/jobs/start?input_dir=${encodeURIComponent(path)}`
        + `&adapter_name=${encodeURIComponent(adapterFor())}`, { method: "POST" });
      poll.label = ui.label.value.trim();
      toast("Her ingestion started.");
      trackJob(r.job_id);
    } catch (err) {
      ui.runBtn.disabled = false;
      showErr(ui.runErr, err);
    }
  }

  function trackJob(id) {
    clearTimeout(poll.timer);
    const token = ++poll.token;
    poll.id = id;
    ui.runBtn.disabled = true;
    let tries = 0;
    const tick = async () => {
      if (token !== poll.token) return;
      try {
        const job = await call(`/api/jobs/${encodeURIComponent(id)}`);
        if (token !== poll.token) return;
        showErr(ui.runErr, null);
        renderJob(job);
        if (TERMINAL.has(job.status)) { onJobFinished(job); return; }
      } catch (err) {
        if (token !== poll.token) return;
        showErr(ui.runErr, err);
      }
      tries += 1;
      if (tries >= MAX_TRIES) {
        ui.runBtn.disabled = false;
        showErr(ui.runErr, new Error("Stopped waiting for the job after 30 minutes. Open the tab again to look."));
        return;
      }
      poll.timer = setTimeout(tick, POLL_MS);
    };
    tick();
  }

  function onJobFinished(job) {
    ui.runBtn.disabled = false;
    toast(`Her ingestion ${String(job.status).replaceAll("_", " ")}. Her checks start on their own.`);
    // Her worker needs a few seconds to pick the job up, so look again twice.
    for (const ms of [3000, 15000]) setTimeout(() => { if (ui.built) refreshDq(); }, ms);
  }

  function renderJob(job) {
    const rt = job.runtime || {};
    const stages = Array.isArray(rt.stages) ? rt.stages : [];
    const progress = rt.resource_progress && typeof rt.resource_progress === "object" ? rt.resource_progress : {};
    const entries = Object.entries(progress).sort((a, b) => a[0].localeCompare(b[0]));
    const done = c => Number(c.processed || c.loaded || 0);
    const totalAll = entries.reduce((s, [, c]) => s + Number(c.total || 0), 0);
    const doneAll = entries.reduce((s, [, c]) => s + done(c), 0);
    const timings = (job.summary && job.summary.timings) || {};
    const csvStage = stages.find(s => s.name === "csv_input");
    const secs = v => (v == null ? "-" : `${fmtNum(v)} s`);
    const jobId = String(job.id || job.job_id || poll.id || "");
    const counts = [["resources_generated", "Generated", "tile-low"], ["resources_validated", "Validated", "tile-high"],
      ["resources_persisted", "Stored", "tile-high"], ["warning_count", "Warnings", "tile-medium"],
      ["error_count", "Errors", Number(job.error_count) > 0 ? "tile-bad" : "tile-low"]]
      .filter(([k]) => job[k] != null);

    ui.jobPill.className = `status ${statusClass(job.status)}`;
    ui.jobPill.textContent = humanize(job.status || "unknown");
    ui.jobPill.hidden = false;
    ui.job.replaceChildren(
      $("p", { class: "label-strong" }, `Job ${jobId.slice(0, 8)}`
        + (job.adapter_name ? ` · adapter ${job.adapter_name}` : "")
        + (poll.label && jobId === poll.id ? ` · ${poll.label}` : "")),
      counts.length ? $("div", { class: "tile-row" }, ...counts.map(([k, l, c]) => tile(c, l.toUpperCase(), job[k]))) : null,
      $("div", { class: "grid-2" },
        $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Stages"),
          stages.length
            ? table(["Stage", "Status", "Seconds"], stages.map(s => $("tr", {}, $("td", { title: s.name },
              STAGE_LABELS[s.name] || humanize(s.name)), $("td", {}, pill(s.status, statusClass(s.status))),
            $("td", { class: "r" }, fmtNum(s.elapsed_seconds)))), [2])
            : $("p", { class: "empty" }, "No stage information yet.")),
        $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Resources processed"),
          entries.length
            ? $("div", { class: "bars" }, ...entries.map(([type, c]) => bar(type,
              Number(c.total) ? done(c) / Number(c.total) : 0,
              `${done(c)} / ${Number(c.total || 0)}${c.status ? ` · ${c.status}` : ""}`)))
            : $("p", { class: "empty" }, "Waiting for the first counts."))),
      totalAll
        ? $("div", { class: "stack" },
          $("div", { class: "progress", role: "img", "aria-label": "Overall progress" },
            $("span", { style: `width:${Math.min(100, (doneAll / totalAll) * 100)}%` })),
          $("p", { class: "note small" }, `${doneAll.toLocaleString()} / ${totalAll.toLocaleString()} resources `
            + `processed (${Math.round((doneAll / totalAll) * 100)}%)`)) : null,
      $("div", { class: "stack" }, $("p", { class: "label-strong" }, "Processing time"),
        $("dl", { class: "facts" },
          $("dt", {}, "CSV reading"), $("dd", {}, secs(timings.csv_reading_seconds ?? (csvStage && csvStage.elapsed_seconds))),
          $("dt", {}, "Mapping"), $("dd", {}, secs(timings.mapping_seconds)),
          $("dt", {}, "FHIR validation"), $("dd", {}, secs(timings.validation_seconds)),
          $("dt", {}, "Database insertion"), $("dd", {}, secs(timings.database_insertion_seconds)))),
      $("p", { class: "note small" }, "Validated resources are stored automatically during ingestion."));
    ui.job.hidden = false;
  }

  async function restoreLatestJob() {
    try {
      const list = await call("/api/jobs");
      const latest = (list.jobs || [])[0];
      if (!latest) return;
      const job = await call(`/api/jobs/${encodeURIComponent(latest.id)}`);
      poll.id = latest.id;
      renderJob(job);
      if (!TERMINAL.has(job.status)) trackJob(latest.id);
    } catch {
      // The section stays ready for a new run when no earlier job can be read.
    }
  }

  // ---------------------------------------------------------------- section 2: data quality and anomalies

  function buildDqCard() {
    ui.dqErr = errLine();
    ui.batch = $("select", { id: "dq-batch", class: "input", onchange: guard(ui.dqErr, onBatch) });
    ui.measure = $("select", { id: "dq-measure", class: "input", onchange: onMeasure },
      $("option", { value: ALL }, "All measures"));
    ui.runSel = $("select", { id: "dq-run", class: "input", onchange: guard(ui.dqErr, onRunSelect) });
    ui.severity = $("select", { id: "dq-severity", class: "input", onchange: guard(ui.dqErr, onSeverity) },
      ...["All", "Critical", "High", "Medium", "Low"].map(s => $("option", { value: s }, s)));
    ui.measureNote = $("p", { class: "note small", hidden: true },
      "Her measure list is empty in this checkout (her config path); filtering by measure is not available.");
    ui.measureHint = $("p", { class: "note small", hidden: true });
    ui.scopeNote = $("p", { class: "note small", hidden: true });
    ui.rerun = $("button", { type: "button", class: "btn btn-secondary", onclick: guard(ui.dqErr, onRerun) },
      "Run checks now");
    ui.rerunNote = $("p", { class: "note small", role: "status" });
    ui.noRun = $("p", { class: "empty" },
      "No checks have run on this batch yet. Her worker starts them on its own after ingestion; use Advanced rerun to "
      + "start them now.");
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
    ui.dqEmpty = $("p", { class: "empty" }, "No ingestion has completed on her copy yet. Run one in the section above; "
      + "her checks then start on their own.");
    ui.dqMain = $("div", { class: "view-root", hidden: true },
      $("div", { class: "field-row" }, field("Batch", ui.batch, "dq-batch"), field("Measure", ui.measure, "dq-measure"),
        field("DQ run", ui.runSel, "dq-run"), field("Severity", ui.severity, "dq-severity")),
      ui.measureNote, ui.measureHint, ui.scopeNote,
      $("details", { class: "dq-advanced" },
        $("summary", {}, "Advanced rerun"),
        $("div", { class: "dq-adv-body" },
          $("p", { class: "note small" }, "Her checks run on their own after each ingestion. Run them again only after "
            + "her rules or mapping change. It can take minutes."),
          $("div", { class: "btn-row" }, ui.rerun), ui.rerunNote)),
      ui.noRun, ui.results);

    return $("article", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Data quality and anomaly detection")),
      ui.dqErr, ui.dqEmpty, ui.dqMain);
  }

  async function onBatch() {
    dq.batchPinned = true;
    dq.runPinned = false;
    dq.batchId = ui.batch.value;
    await loadRuns();
  }

  function onMeasure() {
    dq.measure = ui.measure.value;
    renderResults();
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
    const batch = dq.batches.find(b => b.batch_id === dq.batchId);
    const jobId = (batch && batch.job_id) || (dq.runs[0] && dq.runs[0].job_id);
    if (!batch || !jobId) { showErr(ui.dqErr, new Error("No job was found for this batch.")); return; }
    const q = new URLSearchParams({ job_id: jobId, batch_id: dq.batchId, force_rerun: "true" });
    if (dq.measure !== ALL) q.set("measure_id", dq.measure);
    dq.rerunning = true;
    ui.rerun.disabled = true;
    ui.rerunNote.textContent = "Running her checks…";
    showErr(ui.dqErr, null);
    try {
      await call(`/api/run-checks?${q}`, { method: "POST" });
      toast("Her checks finished.");
      dq.runPinned = false;
      await loadRuns();
    } finally {
      dq.rerunning = false;
      ui.rerun.disabled = false;
      ui.rerunNote.textContent = "";
    }
  }

  async function refreshDq() {
    if (dq.loading || dq.rerunning) return;
    dq.loading = true;
    lastRefresh = Date.now();
    try {
      const [b, m] = await Promise.all([call("/api/batches?limit=100"),
        call("/api/config/measures").catch(() => null)]);
      dq.batches = (b.batches || []).filter(x => ["completed", "partially_completed"]
        .includes(String(x.status || "").toLowerCase()));
      dq.measureMeta = {};
      for (const item of (m && m.measures) || []) if (item && item.measure_id) dq.measureMeta[item.measure_id] = item;
      const ids = Object.keys(dq.measureMeta);
      setOptions(ui.measure, [[ALL, "All measures"], ...ids.map(id => [id, id])]);
      dq.measure = ui.measure.value;
      ui.measureNote.hidden = ids.length > 0;

      const any = dq.batches.length > 0;
      ui.dqEmpty.hidden = any;
      ui.dqMain.hidden = !any;
      if (!any) { dq.batchId = ""; dq.runId = ""; showErr(ui.dqErr, null); return; }
      let sel = dq.batchId;
      if (!dq.batches.some(x => x.batch_id === sel) || !dq.batchPinned) sel = dq.batches[0].batch_id;
      if (sel !== dq.batchId) dq.runPinned = false;
      dq.batchId = sel;
      setOptions(ui.batch, dq.batches.map(x => [x.batch_id, `${String(x.batch_id).slice(0, 8)} · `
        + `${String(x.status).replaceAll("_", " ")} · ${when(x.started_at)}`]), sel);
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
    const r = await call(`/api/dq/runs?batch_id=${encodeURIComponent(batchId)}`);
    if (batchId !== dq.batchId) return;
    const all = r.dq_runs || r.runs || [];
    const done = all.filter(x => String(x.status || "").toLowerCase() === "completed");
    dq.runs = done.length ? done : all;
    let sel = dq.runId;
    if (!dq.runs.some(x => String(x.id) === sel) || !dq.runPinned) sel = dq.runs[0] ? String(dq.runs[0].id) : "";
    setOptions(ui.runSel, dq.runs.map(x => [String(x.id), `#${x.id} · ${x.status}`]), sel);
    const run = dq.runs.find(x => String(x.id) === sel);
    const changed = sel !== dq.runId;
    dq.runId = sel;
    if (!sel) { clearResults(); return; }
    if (changed || (run && String(run.status || "").toLowerCase() !== "completed")) await loadRunData();
  }

  function clearResults() {
    dq.anomalies = [];
    dq.checks = [];
    dq.summary = null;
    ui.results.hidden = true;
    ui.noRun.hidden = false;
    ui.scopeNote.hidden = true;
  }

  async function loadRunData() {
    const id = dq.runId;
    if (!id) { clearResults(); return; }
    const token = ++dq.token;
    const q = new URLSearchParams({ dq_run_id: id, limit: "500" });
    if (dq.severity !== "All") q.set("severity", dq.severity.toLowerCase());
    const [summary, an, ch] = await Promise.all([
      call(`/api/summary?dq_run_id=${encodeURIComponent(id)}`),
      call(`/api/anomalies?${q}`),
      call(`/api/dq/runs/${encodeURIComponent(id)}/checks?limit=1000`),
    ]);
    if (token !== dq.token) return;
    dq.summary = summary;
    dq.anomalies = an.anomalies || [];
    dq.checks = ch.checks || [];
    renderResults();
  }

  const anomalyInMeasure = a => dq.measure === ALL || (a.details && a.details.measure_id === dq.measure);
  const checkInMeasure = c => {
    if (dq.measure === ALL) return true;
    if (c.details && c.details.measure_id) return c.details.measure_id === dq.measure;
    return typeof c.metric_name === "string" && c.metric_name.startsWith(`${dq.measure}_`);
  };

  function renderResults() {
    const anomalies = dq.anomalies.filter(anomalyInMeasure);
    const checks = dq.checks.filter(checkInMeasure);
    const passed = checks.filter(c => c.status === "passed").length;
    const failed = checks.filter(c => c.status === "failed").length;
    const run = dq.runs.find(x => String(x.id) === dq.runId);
    const scope = run && run.summary && ((run.summary.trigger && run.summary.trigger.measure_id)
      || run.summary.selected_measure);

    ui.noRun.hidden = true;
    ui.results.hidden = false;
    const meta = dq.measureMeta[dq.measure];
    ui.measureHint.hidden = !(meta && meta.description);
    ui.measureHint.textContent = meta && meta.description ? `${dq.measure}: ${meta.description}` : "";
    ui.scopeNote.hidden = !scope || (dq.measure !== ALL && dq.measure === scope);
    ui.scopeNote.textContent = scope ? (dq.measure === ALL
      ? `This run was executed for measure ${scope} only. To see every measure, run checks with All measures selected.`
      : `This run covers measure ${scope}. With ${dq.measure} selected, results can be empty or partial.`) : "";
    const sd = dq.summary && dq.summary.dq_run;
    ui.context.textContent = sd ? `Batch ${String(sd.batch_id || "-").slice(0, 8)} · run #${sd.id} · `
      + `${humanize(sd.status)} · scope ${scope || "all measures"}` : "";

    ui.kpis.replaceChildren(kpi("CHECKS RUN", checks.length), kpi("PASSED", passed), kpi("FAILED", failed),
      kpi("ANOMALIES RAISED", anomalies.length));
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
      const type = a.anomaly_type || "unknown";
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
      $("div", { class: "dq-scroll" }, table(["Check", "Status", "Metric", "Value", "Resource type"],
        shown.map(c => $("tr", {}, $("td", {}, humanize(c.check_name)),
          $("td", {}, pill(c.status || "unknown", statusClass(c.status))),
          $("td", { class: "mono small" }, c.metric_name || "-"),
          $("td", { class: "r" }, fmtNum(c.metric_value)),
          $("td", {}, c.resource_type || "-"))), [3])),
      checks.length > shown.length ? $("p", { class: "note small" }, `and ${checks.length - shown.length} more`) : null);
  }

  // ---------------------------------------------------------------- section 3: coverage report

  function buildCoverageCard() {
    ui.covErr = errLine();
    const reload = guard(ui.covErr, loadCoverage);
    ui.payer = $("select", { id: "dq-payer", class: "input", onchange: reload }, $("option", { value: "All" }, "All"));
    ui.compliance = $("select", { id: "dq-compliance", class: "input", onchange: reload },
      ...["All", "Compliant", "Violation"].map(v => $("option", { value: v }, v)));
    ui.from = $("input", { id: "dq-from", class: "input", type: "date", onchange: reload });
    ui.to = $("input", { id: "dq-to", class: "input", type: "date", onchange: reload });
    ui.covTiles = $("div", { class: "tile-row" });
    ui.covRows = $("div", { class: "stack" });
    ui.cov = $("details", { class: "card dq-coverage", ontoggle: () => { if (ui.cov.open) reload(); } },
      $("summary", {}, "Coverage report",
        $("span", { class: "muted small" }, " Enrollment gaps per member; it exists in her code but her app does not show it")),
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
    const r = await call(`/api/coverage/report?${q}`);
    if (token !== cov.token) return;
    showErr(ui.covErr, null);
    const records = r.coverage_records || [];
    const s = r.summary || {};
    for (const rec of records) if (rec.payer) cov.payers.add(rec.payer);
    setOptions(ui.payer, [["All", "All"], ...[...cov.payers].sort().map(p => [p, p])]);

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
        : $("p", { class: "empty" }, "No coverage records found. Run an ingestion first."),
      records.length > shown.length ? $("p", { class: "note small" },
        `Showing ${shown.length} of ${records.length.toLocaleString()} records.`) : null);
  }

  // ---------------------------------------------------------------- tab

  function build() {
    if (ui.built) return;
    ui.built = true;
    ui.body.replaceChildren(buildRunCard(), buildDqCard(), buildCoverageCard());
  }

  TABS.quality = {
    render: async () => {
      ensureShell();
      const p = pallavi();
      renderBanner(p);
      if (!p || !p.enabled) { ui.body.hidden = true; return; }
      build();
      ui.body.hidden = false;
      if (!started) {
        started = true;
        restoreLatestJob();
      }
      if (Date.now() - lastRefresh > REFRESH_MS) {
        lastRefresh = Date.now();
        prefillPath();
        refreshDq();
        if (ui.cov.open) guard(ui.covErr, loadCoverage)();
      }
    },
  };
})();
