// Data Ingestion tab: the ingestion endpoints under /api/ingestion, then our steps from the latest batch.
// The page is built once; render() only repaints what changes, so chosen files and typed values stay.
"use strict";
(() => {
  const API = "/api/ingestion";
  const ACTIVE = ["running", "pending", "started"];
  const DONE = ["completed", "partially_completed"];
  const MAX_ISSUES = 200;
  const MAX_PATIENT_ROWS = 100;
  const MAX_FEED = 20;
  const STAGE_LABELS = {
    csv_input: "CSV input", mapping: "Mapping", fhir_generation: "FHIR generation", validation: "Validation",
    postgresql_storage: "Database storage",
  };
  const MARKS = { done: "✓", running: "…", failed: "!", pending: "·" };

  const ing = {
    built: false, first: null, el: null, inited: false, busy: new Set(),
    jobId: "", job: null, history: [], inputDir: "", meta: null,
    validation: null, patients: null, patientId: "", resources: null,
    feed: [], lastStages: null, lastJobStatus: null, persistedSeen: null,
    timer: null, watch: null, pollFails: 0,
  };

  const jy = path => `${API}${path}`;
  const enc = encodeURIComponent;
  const cnt = v => Number(v || 0).toLocaleString();
  const nice = s => {
    const t = String(s || "-").replaceAll("_", " ");
    return t[0].toUpperCase() + t.slice(1);
  };
  const pillClass = s => ({
    completed: "status-ok", validated: "status-ok", failed: "status-bad", error: "status-bad",
    partially_completed: "status-warn", running: "status-warn", pending: "status-warn", started: "status-warn",
    interrupted: "status-warn", unknown: "status-warn", warning: "status-warn",
  }[s] || "");
  const pill = s => $("span", { class: `status ${pillClass(s)}`.trim() }, nice(s));
  const isActive = job => ACTIVE.includes(effective(job));

  // The runtime status is "unknown" when the server has no memory of the job (for example after a restart).
  function effective(job) {
    const rt = job.runtime || {};
    if (rt.status && rt.status !== "unknown") return rt.status;
    return ACTIVE.includes(job.status) ? "interrupted" : job.status;
  }

  function say(key, text, bad = false) {
    const el = ing.el && ing.el.msg[key];
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("error", bad);
  }

  async function guard(key, msgKey, fn) {
    if (ing.busy.has(key)) return;
    ing.busy.add(key);
    paintButtons();
    say(msgKey, "");
    try {
      await fn();
    } catch (err) {
      say(msgKey, err.message, true);
    } finally {
      ing.busy.delete(key);
      paintButtons();
    }
  }

  async function pool(items, limit, fn) {
    let next = 0;
    const worker = async () => {
      while (next < items.length) {
        const i = next++;
        await fn(items[i], i);
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  }

  function saveFile(name, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, name.includes("-job-") ? 0 : 2)],
      { type: "application/json" }));
    const a = $("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function bundleOf(jobId, patientId, resources) {
    const entry = resources.filter(r => r.resource_json && typeof r.resource_json === "object")
      .map(r => ({ resource: r.resource_json }));
    return { resourceType: "Bundle", type: "collection", id: `${jobId}-${patientId}`, total: entry.length, entry };
  }

  const table = (heads, rows, rightFrom = heads.length) => $("div", { class: "table-wrap" },
    $("table", { class: "table compact" },
      $("thead", {}, $("tr", {}, ...heads.map((h, i) => $("th", i >= rightFrom ? { class: "r" } : {}, h)))),
      $("tbody", {}, ...rows)));

  // ------------------------------------------------------------ build (once)

  function build() {
    const root = byId("ingestion-root");
    const e = { btn: {}, msg: {} };
    ing.el = e;
    const button = (key, label, cls, onclick) => {
      e.btn[key] = $("button", { type: "button", class: `btn ${cls}`, onclick }, label);
      return e.btn[key];
    };
    const msg = key => {
      e.msg[key] = $("p", { class: "note small", role: "status" });
      return e.msg[key];
    };

    e.files = $("input", { id: "ing-files", class: "input", type: "file", accept: ".zip,.csv", multiple: true,
      onchange: () => say("upload", "") });
    e.dir = $("input", { id: "ing-dir", class: "input mono", autocomplete: "off", spellcheck: "false",
      value: ing.inputDir, oninput: ev => { ing.inputDir = ev.target.value; } });
    e.meta = $("div", { class: "stack" });

    const uploadCard = $("article", { class: "card" },
      $("h2", {}, "Upload and inspect"),
      $("label", { class: "field-label", for: "ing-files" }, "Synthea CSV files, or one zip file"),
      e.files,
      $("p", { class: "note small" }, "Choose one .zip file or several .csv files. Only the Synthea file names are " +
        "kept, and patients.csv is required."),
      $("div", { class: "btn-row" }, button("upload", "Upload files", "btn-primary", () => guard("upload", "upload", upload))),
      $("label", { class: "field-label", for: "ing-dir" }, "Input folder on the server"),
      e.dir,
      $("div", { class: "btn-row" }, button("inspect", "Inspect dataset", "btn-secondary", () => guard("inspect", "upload", inspect))),
      msg("upload"),
      $("p", { class: "ing-sub" }, "Dataset file summary"),
      e.meta);

    e.facts = $("dl", { class: "facts" });
    e.tiles = $("div", { class: "tile-row" });
    e.jobNote = $("p", { class: "note small" });
    const jobCard = $("article", { class: "card" },
      $("h2", {}, "Ingestion job"),
      e.facts,
      $("div", { class: "btn-row" },
        button("start", "Start ingestion", "btn-primary", () => guard("start", "job", startJob)),
        button("refresh", "Refresh status", "btn-secondary", () => guard("refresh", "job", refreshJob)),
        button("persist", "Persist resources", "btn-secondary", () => guard("persist", "job", persist))),
      $("p", { class: "note small" }, "Persisting approves the records. Our steps then start on their own: load the " +
        "patient tables, run the measure engine, score the gaps and attach the stored documents."),
      msg("job"),
      e.jobNote,
      e.tiles);

    e.stages = $("div", { class: "stack" });
    e.feed = $("div", { class: "stack" });
    const stageCard = $("article", { class: "card" },
      $("h2", {}, "Pipeline stage progress"), e.stages,
      $("p", { class: "ing-sub" }, "Live activity"), e.feed);
    e.resources = $("div", { class: "ing-res-list" });
    const resourceCard = $("article", { class: "card" }, $("h2", {}, "Resource progress"), e.resources);

    e.ours = $("div", { class: "stack" });
    const oursCard = $("article", { class: "card" }, $("h2", {}, "Our steps after persist"), e.ours);

    e.issues = $("div", { class: "stack" });
    const validationCard = $("article", { class: "card" },
      $("h2", {}, "Validation issues"),
      $("div", { class: "btn-row" }, button("validation", "Load validation", "btn-secondary",
        () => guard("validation", "validation", loadValidation))),
      msg("validation"), e.issues);

    e.select = $("select", { id: "ing-patient", class: "input", onchange: ev => pickPatient(ev.target.value) });
    e.ptable = $("div", { class: "stack" });
    e.pdetail = $("div", { class: "stack" });
    const patientCard = $("article", { class: "card" },
      $("h2", {}, "Patients and FHIR"),
      $("div", { class: "btn-row" },
        button("patients", "Load patients", "btn-secondary", () => guard("patients", "patients", loadPatients)),
        button("dlOne", "Download selected FHIR", "btn-secondary", () => guard("dlOne", "patients", downloadOne)),
        button("dlAll", "Download all FHIR", "btn-secondary", () => guard("dlAll", "patients", downloadAll))),
      $("p", { class: "note small" }, "The older screen packs the all-patient download into a zip. This app has no zip " +
        "library, so it saves one JSON file with every bundle instead."),
      msg("patients"),
      $("label", { class: "field-label", for: "ing-patient" }, "Patient"), e.select,
      e.pdetail, e.ptable);

    e.history = $("div", { class: "stack" });
    const historyCard = $("article", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Job history"),
        button("history", "Reload list", "btn-secondary", () => guard("history", "history", loadHistory))),
      msg("history"), e.history);

    const nodes = [
      $("div", { class: "grid-2" }, uploadCard, jobCard),
      $("div", { class: "grid-2" }, stageCard, resourceCard),
      oursCard,
      $("div", { class: "grid-2" }, validationCard, patientCard),
      historyCard,
    ];
    root.replaceChildren(...nodes);
    ing.first = nodes[0];
    ing.built = true;
    paintAll();
  }

  function ensureBuilt() {
    const root = byId("ingestion-root");
    if (!ing.built || !ing.first || ing.first.parentNode !== root) build();
  }

  // ------------------------------------------------------------ paint

  function paintAll() {
    paintMeta();
    paintJob();
    paintValidation();
    paintPatients();
    paintHistory();
    paintOurSteps();
  }

  function paintButtons() {
    if (!ing.el) return;
    const job = ing.job;
    const off = {
      persist: !job || !DONE.includes(effective(job)),
      refresh: !ing.jobId, validation: !ing.jobId, patients: !ing.jobId, dlAll: !ing.jobId, dlOne: !ing.patientId,
    };
    for (const [key, b] of Object.entries(ing.el.btn)) b.disabled = ing.busy.has(key) || !!off[key];
  }

  function setDir(value) {
    ing.inputDir = value || "";
    if (ing.el) ing.el.dir.value = ing.inputDir;
  }

  function paintMeta() {
    const m = ing.meta;
    if (!m) {
      ing.el.meta.replaceChildren($("p", { class: "empty" }, "Nothing inspected yet. Upload files or inspect a folder."));
      return;
    }
    const rows = Object.entries(m.row_counts || {}).sort((a, b) => a[0].localeCompare(b[0]));
    const missing = m.missing_required_files || [];
    ing.el.meta.replaceChildren(
      rows.length ? table(["CSV file", "Rows"], [
        ...rows.map(([name, rows]) => $("tr", {}, $("td", { class: "mono" }, name), $("td", { class: "r" }, cnt(rows)))),
        $("tr", {}, $("td", {}, $("b", {}, "Total")), $("td", { class: "r" }, $("b", {}, cnt(m.total_rows))))], 1)
        : $("p", { class: "empty" }, "No dataset files were found in that folder."),
      missing.length ? $("p", { class: "note small error" }, `Missing required files: ${missing.join(", ")}`)
        : $("p", { class: "note small" }, "All required files are present."));
  }

  function paintJob() {
    const e = ing.el;
    const j = ing.job;
    const eff = j ? effective(j) : "";
    e.facts.replaceChildren(...[
      ["Job", $("span", { class: "mono" }, ing.jobId || "none yet")],
      ["Status", j ? pill(eff) : "No job selected"],
      ["Started", when(j && j.started_at)],
      ["Finished", when(j && j.completed_at)],
      ["Source records", j ? cnt(j.total_source_records) : "-"],
      ["Input folder", $("span", { class: "mono" }, (j && j.input_dir) || "-")],
    ].flatMap(([k, v]) => [$("dt", {}, k), $("dd", {}, v)]));
    const tile = (cls, label, v) => $("div", { class: `tile ${cls}` }, $("span", {}, label),
      $("b", {}, j ? cnt(v) : "-"));
    e.tiles.replaceChildren(
      tile("tile-low", "GENERATED", j && j.resources_generated),
      tile("tile-low", "VALIDATED", j && j.resources_validated),
      tile("tile-high", "PERSISTED", j && j.resources_persisted),
      tile(j && j.warning_count ? "tile-medium" : "tile-low", "WARNINGS", j && j.warning_count),
      tile(j && j.error_count ? "tile-bad" : "tile-low", "ERRORS", j && j.error_count));
    e.jobNote.textContent = eff === "interrupted"
      ? "This job was interrupted, most likely by a server restart. Start it again to finish it." : "";
    e.jobNote.classList.toggle("error", eff === "interrupted");
    paintStages();
    paintFeed();
    paintButtons();
  }

  function paintStages() {
    const rt = (ing.job && ing.job.runtime) || {};
    const stages = rt.stages || [];
    ing.el.stages.replaceChildren(stages.length
      ? table(["Stage", "Status", "Details", "Elapsed (s)"], stages.map(s => $("tr", {},
        $("td", {}, STAGE_LABELS[s.name] || nice(s.name)), $("td", {}, pill(s.status)),
        $("td", { class: "ing-wrap" }, s.details || "-"),
        $("td", { class: "r" }, typeof s.elapsed_seconds === "number" ? s.elapsed_seconds.toFixed(2) : "-"))), 3)
      : $("p", { class: "empty" }, "No stage information yet."));

    const prog = Object.entries(rt.resource_progress || {}).sort((a, b) => a[0].localeCompare(b[0]));
    ing.el.resources.replaceChildren(...(prog.length ? prog.map(([type, p]) => {
      const pct = p.total ? Math.min(100, (p.processed / p.total) * 100) : 0;
      const extra = p.warnings || p.rejected ? ` · ${cnt(p.warnings)} warnings, ${cnt(p.rejected)} rejected` : "";
      return $("div", { class: "ing-res" }, $("span", {}, type),
        $("b", {}, `${cnt(p.processed)} of ${cnt(p.total)}${extra}`),
        $("div", { class: "progress" }, $("span", { style: `width:${pct.toFixed(1)}%` })));
    }) : [$("p", { class: "empty" }, "No resources generated yet.")]));
  }

  function paintFeed() {
    ing.el.feed.replaceChildren(ing.feed.length
      ? $("ul", { class: "ing-feed" }, ...ing.feed.slice(-MAX_FEED).reverse().map(line => $("li", {}, line)))
      : $("p", { class: "empty" }, "No events yet. Start ingestion to see updates here."));
  }

  function paintValidation() {
    const box = ing.el.issues;
    const list = ing.validation;
    if (!list) {
      box.replaceChildren($("p", { class: "empty" }, "No validation issues loaded yet."));
      return;
    }
    if (!list.length) {
      box.replaceChildren($("p", { class: "empty" }, "This job has no validation issues."));
      return;
    }
    box.replaceChildren(
      $("p", { class: "note small" }, list.length > MAX_ISSUES
        ? `Showing the first ${MAX_ISSUES} of ${cnt(list.length)} issues.` : `${cnt(list.length)} issues.`),
      $("div", { class: "ing-scroll" }, table(["Severity", "Category", "Source file", "Source record", "Diagnostic"],
        list.slice(0, MAX_ISSUES).map(i => $("tr", {},
          $("td", {}, $("span", { class: `status ${pillClass(i.severity)}`.trim() }, nice(i.severity))),
          $("td", {}, i.issue_category || "-"), $("td", { class: "mono" }, i.source_file || "-"),
          $("td", { class: "mono" }, i.source_record_id || "-"),
          $("td", { class: "ing-wrap" }, i.diagnostic || "-"))))));
  }

  function paintPatients() {
    const e = ing.el;
    const list = ing.patients;
    e.select.replaceChildren($("option", { value: "" }, list ? "Choose a patient" : "Load patients first"),
      ...(list || []).map(p => $("option", { value: p.patient_id },
        `${shortPt(p.patient_id)} · ${cnt(p.resource_count)} resources`)));
    e.select.value = ing.patientId;
    e.select.disabled = !list || !list.length;
    if (!list) {
      e.ptable.replaceChildren($("p", { class: "empty" }, "No patients loaded yet."));
    } else if (!list.length) {
      e.ptable.replaceChildren($("p", { class: "empty" }, "No patients with FHIR records yet for this job."));
    } else {
      e.ptable.replaceChildren(
        $("p", { class: "note small" }, list.length > MAX_PATIENT_ROWS
          ? `${cnt(list.length)} patients. The table shows the first ${MAX_PATIENT_ROWS}; use the list above for the rest.`
          : `${cnt(list.length)} patients. Click a row to open it.`),
        $("div", { class: "ing-scroll" }, table(["Patient", "Resources", "Size (KB)", "Saved"],
          list.slice(0, MAX_PATIENT_ROWS).map(p => $("tr", {
            class: `ing-click${p.patient_id === ing.patientId ? " selected" : ""}`, tabindex: "0",
            onclick: () => pickPatient(p.patient_id),
            onkeydown: ev => { if (ev.key === "Enter") pickPatient(p.patient_id); },
          }, $("td", { class: "mono" }, shortPt(p.patient_id)), $("td", { class: "r" }, cnt(p.resource_count)),
          $("td", { class: "r" }, p.estimated_size_bytes == null ? "-" : (p.estimated_size_bytes / 1024).toFixed(1)),
          $("td", {}, when(p.saved_at)))), 1)));
    }
    paintPatientDetail();
    paintButtons();
  }

  function paintPatientDetail() {
    const box = ing.el.pdetail;
    const res = ing.resources;
    if (!ing.patientId || !res) {
      box.replaceChildren(...(ing.patientId ? [$("p", { class: "empty" }, "Loading this patient's records…")] : []));
      return;
    }
    const byType = {};
    for (const r of res) {
      const t = byType[r.resource_type] || (byType[r.resource_type] = { n: 0, persisted: 0 });
      t.n += 1;
      if (r.persisted) t.persisted += 1;
    }
    const pre = $("pre", { class: "code wrap" });
    const det = $("details", { class: "ing-bundle", ontoggle: () => {
      if (det.open && !pre.textContent) pre.textContent = JSON.stringify(bundleOf(ing.jobId, ing.patientId, res), null, 2);
    } }, $("summary", {}, "Bundle JSON"), pre);
    box.replaceChildren(
      $("p", { class: "ing-sub" }, `${shortPt(ing.patientId)}: ${cnt(res.length)} resources`),
      table(["Resource type", "Records", "Persisted"], Object.entries(byType).sort((a, b) => b[1].n - a[1].n)
        .map(([type, t]) => $("tr", {}, $("td", {}, type), $("td", { class: "r" }, cnt(t.n)),
          $("td", { class: "r" }, cnt(t.persisted)))), 1),
      $("p", { class: "note small" }, "The bundle holds FHIR records with patient details. Open it only when you need it."),
      det);
  }

  function paintHistory() {
    const jobs = ing.history;
    ing.el.history.replaceChildren(jobs.length
      ? table(["Job", "Status", "Started", "Generated", "Persisted"], jobs.slice(0, 50).map(j => $("tr", {
        class: `ing-click${j.id === ing.jobId ? " selected" : ""}`, tabindex: "0", title: "Make this the current job",
        onclick: () => selectJob(j.id), onkeydown: ev => { if (ev.key === "Enter") selectJob(j.id); },
      }, $("td", { class: "mono" }, j.id.slice(0, 8)), $("td", {}, pill(j.status)), $("td", {}, when(j.started_at)),
      $("td", { class: "r" }, cnt(j.resources_generated)), $("td", { class: "r" }, cnt(j.resources_persisted)))), 3)
      : $("p", { class: "empty" }, "No jobs yet. Upload files and start ingestion."));
  }

  function paintOurSteps() {
    const box = ing.el.ours;
    const b = state.batch;
    if (!ing.jobId || !b || b.ingest_job_id !== ing.jobId) {
      box.replaceChildren($("p", { class: "empty" }, "Not started yet: persist this job to start our steps."));
      return;
    }
    const c = b.counts || {};
    const idx = STEPS.findIndex(([key]) => key === b.step);
    const cls = i => {
      if (b.status === "prioritized") return "done";
      if (idx < 0) return "pending";
      if (i < idx) return "done";
      return i > idx ? "pending" : b.status === "failed" ? "failed" : "running";
    };
    const countTable = (head, obj) => {
      const rows = obj && typeof obj === "object" ? Object.entries(obj) : [];
      return $("div", { class: "stack" }, $("p", { class: "ing-sub" }, head),
        rows.length ? table(["Name", "Rows"], rows.sort((x, y) => Number(y[1]) - Number(x[1])).map(([k, v]) =>
          $("tr", {}, $("td", {}, k), $("td", { class: "r" }, cnt(v)))), 1)
          : $("p", { class: "empty" }, "Not loaded yet."));
    };
    const load = c.load || {};
    box.replaceChildren(...[
      $("p", { class: "note" }, `${statusText(b)} · batch ${b.batch_id.slice(0, 8)} · updated ${when(b.updated_at)}`),
      $("ol", { class: "steps" }, ...STEPS.map(([key, label], i) => {
        const k = cls(i);
        return $("li", { class: `step ${k}` }, $("span", { class: "dot" }, MARKS[k]), $("span", { class: "step-name" }, label),
          $("span", { class: "step-detail" }, stepDetail(key, c)));
      })),
      b.error ? $("p", { class: "note small error" }, b.error) : null,
      $("div", { class: "grid-2" }, countTable("FHIR records loaded", load.resource_types),
        countTable("Team table rows added", load.inserted)),
      b.status === "prioritized" ? $("div", { class: "ing-links" },
        $("a", { class: "link", href: "#calculation" }, "Open HEDIS Calculation"),
        $("a", { class: "link", href: "#prioritization" }, "Open Prioritization")) : null].filter(Boolean));
  }

  // ------------------------------------------------------------ data

  async function refreshBatch() {
    state.batch = await loadBatch();
    if (state.batch && state.batch.status === "running") poll();
  }

  // After persist our app starts the batch on its own; look for it every 2 s for about a minute.
  function watchBatch() {
    clearTimeout(ing.watch);
    const id = ing.jobId;
    let tries = 0;
    const step = async () => {
      if (id !== ing.jobId || byId("view-ingestion").hidden) return;
      await refreshBatch().catch(() => {});
      paintOurSteps();
      if (state.batch && state.batch.ingest_job_id === id) return;
      if (++tries < 30) ing.watch = setTimeout(step, 2000);
    };
    step();
  }

  function setJobId(id) {
    clearTimeout(ing.timer);
    clearTimeout(ing.watch);
    ing.timer = null;
    ing.jobId = id;
    if (id) store("hedis.job", id);
    Object.assign(ing, { job: null, validation: null, patients: null, patientId: "", resources: null, feed: [],
      lastStages: null, lastJobStatus: null, persistedSeen: null });
    paintJob();
    paintValidation();
    paintPatients();
    paintHistory();
    paintOurSteps();
  }

  function noteJob(job) {
    const eff = effective(job);
    const silent = ing.lastStages === null && !isActive(job);
    const seen = ing.lastStages || {};
    const at = new Date().toLocaleTimeString();
    const push = text => { if (!silent) ing.feed.push(`${at}  ${text}`); };
    if (ing.lastJobStatus !== eff) push(`Job: ${nice(eff).toLowerCase()}`);
    for (const s of (job.runtime && job.runtime.stages) || []) {
      const sig = `${s.status}|${s.details || ""}`;
      if (seen[s.name] !== sig && !(s.status === "pending" && seen[s.name] === undefined)) {
        push(`${STAGE_LABELS[s.name] || nice(s.name)}: ${s.status}${s.details ? `, ${s.details}` : ""}`);
      }
      seen[s.name] = sig;
    }
    ing.lastStages = seen;
    ing.feed = ing.feed.slice(-MAX_FEED);
  }

  async function loadJob() {
    const id = ing.jobId;
    if (!id) return null;
    const job = await api(jy(`/jobs/${enc(id)}`));
    if (id !== ing.jobId) return null;
    const wasActive = ing.job ? isActive(ing.job) : false;
    noteJob(job);
    const prevPersisted = ing.persistedSeen;
    ing.persistedSeen = job.resources_persisted || 0;
    ing.lastJobStatus = effective(job);
    ing.job = job;
    if (!ing.inputDir && job.input_dir) setDir(job.input_dir);
    paintJob();
    if (wasActive && !isActive(job)) loadHistory().catch(() => {});
    if (prevPersisted !== null && ing.persistedSeen > prevPersisted) watchBatch();
    return job;
  }

  function schedulePoll() {
    clearTimeout(ing.timer);
    ing.timer = null;
    if (!ing.job || !isActive(ing.job)) return;
    ing.timer = setTimeout(tick, 1500);
  }

  async function tick() {
    ing.timer = null;
    if (byId("view-ingestion").hidden || !ing.jobId) return;
    try {
      await loadJob();
      ing.pollFails = 0;
    } catch (err) {
      say("job", `Could not refresh the job: ${err.message}`, true);
      if (++ing.pollFails >= 5) return;
    }
    schedulePoll();
  }

  async function loadHistory() {
    const r = await api(jy("/jobs"));
    ing.history = (r.jobs || []).slice().sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));
    paintHistory();
  }

  async function selectJob(id) {
    if (id === ing.jobId && ing.job) return;
    setJobId(id);
    await guard("refresh", "job", async () => {
      await loadJob();
      schedulePoll();
    });
  }

  async function refreshJob() {
    await loadJob();
    await loadHistory();
    schedulePoll();
  }

  // ------------------------------------------------------------ actions

  async function upload() {
    const files = Array.from(ing.el.files.files);
    if (!files.length) throw new Error("Choose one .zip file or several .csv files first.");
    const zips = files.filter(f => /\.zip$/i.test(f.name));
    const csvs = files.filter(f => /\.csv$/i.test(f.name));
    if (zips.length && csvs.length) throw new Error("Choose either one .zip file or several .csv files, not both.");
    if (zips.length > 1) throw new Error("Choose only one .zip file.");
    if (zips.length + csvs.length !== files.length) throw new Error("Only .zip and .csv files can be uploaded.");
    const form = new FormData();
    let path;
    if (zips.length) {
      form.append("file", zips[0]);
      path = "/datasets/upload-zip";
    } else {
      csvs.forEach(f => form.append("files", f));
      path = "/datasets/upload";
    }
    say("upload", "Uploading…");
    const r = await api(jy(path), { method: "POST", body: form });
    ing.meta = r;
    setDir(r.input_dir);
    paintMeta();
    say("upload", `Uploaded. ${cnt((r.saved_files || []).length)} supported files kept, ${cnt(r.total_rows)} rows in total.`);
  }

  async function inspect() {
    const dir = ing.inputDir.trim();
    if (!dir) throw new Error("Enter the input folder on the server, or upload files first.");
    ing.meta = await api(jy(`/datasets/inspect?input_dir=${enc(dir)}`));
    paintMeta();
    say("upload", `Inspected: ${cnt(ing.meta.total_rows)} rows.`);
  }

  async function startJob() {
    const dir = ing.inputDir.trim();
    if (!dir) throw new Error("Enter the input folder on the server, or upload files first.");
    ing.meta = await api(jy(`/datasets/inspect?input_dir=${enc(dir)}`));
    paintMeta();
    const missing = ing.meta.missing_required_files || [];
    if (missing.length) throw new Error(`Cannot start: missing required files ${missing.join(", ")}.`);
    const r = await api(jy(`/jobs/start?input_dir=${enc(dir)}&async_processing=true`), { method: "POST" });
    setJobId(r.job_id);
    ing.lastStages = {};
    ing.feed.push(`${new Date().toLocaleTimeString()}  Job started`);
    say("job", r.legacy_jobs_cleared ? `Started. ${cnt(r.legacy_jobs_cleared)} older job(s) for this folder were cleared.` : "Started.");
    await loadJob();
    schedulePoll();
    loadHistory().catch(() => {});
  }

  async function persist() {
    const id = ing.jobId;
    const r = await api(jy(`/jobs/${enc(id)}/persist`), { method: "POST" });
    toast(`${cnt(r.resources_persisted)} records persisted. Our steps will start on their own.`);
    say("job", `Persisted ${cnt(r.resources_persisted)} records.`);
    await loadJob();
    loadHistory().catch(() => {});
    watchBatch();
  }

  async function loadValidation() {
    const r = await api(jy(`/jobs/${enc(ing.jobId)}/validation`));
    ing.validation = r.issues || [];
    paintValidation();
  }

  async function loadPatients() {
    const r = await api(jy(`/jobs/${enc(ing.jobId)}/patients?persisted_only=false&bundleable_only=true`));
    ing.patients = (r.patient_details && r.patient_details.length)
      ? r.patient_details : (r.patients || []).map(id => ({ patient_id: id }));
    if (!ing.patients.some(p => p.patient_id === ing.patientId)) {
      ing.patientId = "";
      ing.resources = null;
    }
    paintPatients();
  }

  async function pickPatient(patientId) {
    ing.patientId = patientId;
    ing.resources = null;
    paintPatients();
    if (!patientId) return;
    await guard("pick", "patients", async () => {
      const id = ing.jobId;
      const r = await api(jy(`/jobs/${enc(id)}/patients/${enc(patientId)}/resources`));
      if (id !== ing.jobId || patientId !== ing.patientId) return;
      ing.resources = r.resources || [];
      paintPatientDetail();
    });
  }

  async function downloadOne() {
    const patientId = ing.patientId;
    if (!patientId) throw new Error("Choose a patient first.");
    if (!ing.resources) {
      const r = await api(jy(`/jobs/${enc(ing.jobId)}/patients/${enc(patientId)}/resources`));
      ing.resources = r.resources || [];
      paintPatientDetail();
    }
    saveFile(`fhir-${patientId}.bundle.json`, bundleOf(ing.jobId, patientId, ing.resources));
    say("patients", "Saved the bundle for the selected patient.");
  }

  async function downloadAll() {
    const id = ing.jobId;
    let ids = ing.patients ? ing.patients.map(p => p.patient_id) : null;
    if (!ids) {
      const r = await api(jy(`/jobs/${enc(id)}/patients?persisted_only=false&bundleable_only=true`));
      ids = r.patients || [];
    }
    if (!ids.length) throw new Error("There are no patients to export for this job yet.");
    const bundles = new Array(ids.length);
    let done = 0;
    await pool(ids, 4, async (patientId, i) => {
      const r = await api(jy(`/jobs/${enc(id)}/patients/${enc(patientId)}/resources`));
      bundles[i] = bundleOf(id, patientId, r.resources || []);
      say("patients", `Fetched ${cnt(++done)} of ${cnt(ids.length)} patients…`);
    });
    saveFile(`fhir-job-${id}-patients.json`, { job_id: id, patient_count: bundles.length,
      exported_at: new Date().toISOString(), bundles });
    say("patients", `Saved one file with ${cnt(bundles.length)} patient bundles.`);
  }

  // ------------------------------------------------------------ tab

  async function init() {
    try {
      await loadHistory();
    } catch (err) {
      say("history", `Could not load the job list: ${err.message}`, true);
    }
    const tryJob = async id => {
      setJobId(id);
      try {
        await loadJob();
        return true;
      } catch (err) {
        say("job", `Could not load job: ${err.message}`, true);
        setJobId("");
        return false;
      }
    };
    const saved = store("hedis.job") || "";
    let ok = saved ? await tryJob(saved) : false;
    if (!ok && ing.history.length) ok = await tryJob(ing.history[0].id);
    if (ok) say("job", "");
  }

  TABS.ingestion = {
    render: async () => {
      try {
        ensureBuilt();
        if (!ing.inited) {
          ing.inited = true;
          await init();
        }
        await refreshBatch();
        paintOurSteps();
        ing.pollFails = 0;
        schedulePoll();
      } catch (err) {
        if (ing.el) say("job", `Could not update this screen: ${err.message}`, true);
      }
    },
  };
})();
