// HEDIS Calculation tab: the measure engine run through our bridge. Every result is stored first; this tab reads it back.
"use strict";
(() => {
  const PAGE = 20;
  const WORKLIST_MAX = 50;
  const FILTERS = [["", "All"], ["gap", "Gap"], ["met", "Met"], ["excluded", "Excluded"],
    ["not in population", "Not in population"]];
  const OUTCOME = {
    gap: ["status status-warn", "Gap"], met: ["status status-ok", "Met"], excluded: ["status", "Excluded"],
    "not in population": ["muted", "Not in population"],
  };
  const RUN_STEPS = [["measuring", "Run the measure engine"], ["prioritizing", "Score and rank open gaps"]];

  const calc = {
    data: null, error: "", selected: null, filter: "", search: "", shown: PAGE,
    picked: new Set(), known: new Set(),
    rows: null, rowsFor: "", rowsError: "", seq: 0, waiting: false, starting: false, outSig: "",
    candidates: null, candidatesError: "", candidateOffset: 0, selectedPatients: new Set(),
  };
  const ui = { boxes: new Map() };

  const n0 = v => Number(v || 0).toLocaleString();
  const pct = (a, b) => (b > 0 ? `${fmt((100 * a) / b, 1)}%` : "-");
  const sum = (list, f) => list.reduce((s, m) => s + Number(f(m) || 0), 0);
  const yesNo = v => (v ? "Yes" : "No");
  const words = s => (s ? String(s).replaceAll("_", " ") : "-");
  const durationText = seconds => {
    const total = Math.max(0, Math.round(Number(seconds || 0)));
    const minutes = Math.floor(total / 60);
    const remaining = total % 60;
    return minutes ? `${minutes}m ${remaining}s` : `${remaining}s`;
  };
  const measures = () => (calc.data && calc.data.measures) || [];
  const selectedMeasure = () => measures().find(m => m.measure === calc.selected) || null;
  const isRunning = () => !!(state.batch && state.batch.status === "running");
  const th = (text, cls) => $("th", { class: cls }, text);
  const emptyRow = (cols, text) => $("tr", {}, $("td", { colspan: String(cols), class: "empty" }, text));

  // ---------------------------------------------------------------- layout, built once

  function build() {
    if (ui.root && ui.root.isConnected) return;
    ui.root = byId("calculation-root");
    ui.loadMsg = $("p", { class: "note error", role: "status", hidden: true });
    ui.runStatus = $("span", {});
    ui.runMeta = $("p", { class: "note small", role: "status" });
    ui.checks = $("div", { class: "calc-checks" });
    ui.runBtn = $("button", { type: "button", class: "btn btn-primary", onclick: runCalc }, "Run selected patients");
    ui.progress = $("ol", { class: "steps", hidden: true });
    ui.runMsg = $("p", { class: "note small", role: "status" });
    ui.boxes.clear();
    calc.known.clear();
    calc.picked.clear();

    const pickAll = on => {
      calc.picked = new Set(on ? calc.known : []);
      for (const box of ui.boxes.values()) box.checked = on;
    };
    ui.candidateBody = $("tbody", {});
    ui.candidateCount = $("span", { class: "note" });
    ui.candidatePrev = $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => {
      calc.candidateOffset = Math.max(0, calc.candidateOffset - 10);
      loadCandidates();
    } }, "Previous 10");
    ui.candidateNext = $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => {
      calc.candidateOffset += 10;
      loadCandidates();
    } }, "Next 10");
    const patientPicker = $("div", { class: "stack" },
      $("div", { class: "calc-section-head" },
        $("span", { class: "field-label" }, "1. Select patients"), ui.candidateCount),
      $("div", { class: "table-wrap calc-candidate-table" }, $("table", { class: "table compact" },
        $("thead", {}, $("tr", {}, th("Run"), th("Patient ID"), th("Batch upload size"), th("Stored in FHIR"))),
        ui.candidateBody)),
      $("div", { class: "calc-pagination" }, ui.candidatePrev, ui.candidateNext));
    const engineCard = $("section", { class: "card" },
      $("div", { class: "card-head" },
        $("div", {}, $("h2", {}, "Run HEDIS calculation"),
          $("p", { class: "note" }, "Select patients and measures, then run.")),
        ui.runStatus),
      ui.runMeta,
      $("div", { class: "calc-run-layout" },
        patientPicker,
        $("div", { class: "stack calc-measures" },
          $("span", { class: "field-label" }, "2. Choose measures"), ui.checks,
          $("div", { class: "btn-row" },
            $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => pickAll(true) }, "Select all"),
            $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => pickAll(false) }, "Clear")),
          $("span", { class: "field-label" }, "3. Run"),
          $("div", { class: "btn-row calc-run-action" }, ui.runBtn),
          ui.progress, ui.runMsg)));

    ui.empty = $("p", { class: "empty", hidden: true }, "No engine results yet. Ingest and persist a batch on ",
      $("a", { class: "link", href: "#ingestion" }, "Data Ingestion"), ".");

    ui.kpiNote = $("span", { class: "note" });
    ui.kpis = $("div", { class: "kpis" });
    const summaryCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Calculation summary"), ui.kpiNote), ui.kpis);

    ui.outBody = $("tbody", {});
    const outcomeCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Measure outcome table"),
        $("span", { class: "note" }, "Select a row to see its patients below.")),
      $("div", { class: "table-wrap" }, $("table", { class: "table" },
        $("thead", {}, $("tr", {}, th("Measure"), th("Type"), th("In measure", "r"), th("Met", "r"), th("Excluded", "r"),
          th("Not met", "r"), th("Rate", "r"), th("Gaps open", "r"), th("Closed by engine", "r"),
          th("Confirmed by a reviewer", "r"))), ui.outBody)));

    ui.bars = $("div", { class: "bars" });
    const gapsCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Gaps by measure"), $("span", { class: "note" }, "Share of all open gaps")),
      ui.bars);

    ui.patTitle = $("h2", {}, "Patient-level outcomes");
    ui.patErr = $("p", { class: "note error", hidden: true });
    ui.pills = FILTERS.map(([value, text]) => {
      const btn = $("button", { type: "button", class: "pill", onclick: () => {
        calc.filter = value;
        calc.shown = PAGE;
        renderPatients();
      } }, text);
      return [value, btn, text];
    });
    ui.search = $("input", { class: "input calc-search", type: "search", placeholder: "Start of patient id",
      "aria-label": "Search patients by the start of the patient id", autocomplete: "off", oninput: e => {
        calc.search = e.target.value;
        calc.shown = PAGE;
        renderPatients();
      } });
    ui.patBody = $("tbody", {});
    ui.patCount = $("span", { class: "note" });
    ui.more = $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => {
      calc.shown += PAGE;
      renderPatients();
    } }, "Load next 20");
    const patientsCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, ui.patTitle),
      ui.patErr,
      $("div", { class: "calc-toolbar" }, $("div", { class: "pills" }, ...ui.pills.map(p => p[1])), ui.search),
      $("div", { class: "table-wrap" }, $("table", { class: "table compact" },
        $("thead", {}, $("tr", {}, th("Patient"), th("In denominator"), th("Met"), th("Excluded"), th("Outcome"),
          th("Gap status"), th("Review status"), th("Computed at"), th("Actions"))), ui.patBody)),
      $("div", { class: "calc-foot" }, ui.patCount, ui.more));

    ui.workTitle = $("span", { class: "note" });
    ui.workBody = $("tbody", {});
    const worklistCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Gap and blocker worklist"), ui.workTitle),
      $("div", { class: "table-wrap" }, $("table", { class: "table compact" },
        $("thead", {}, $("tr", {}, th("Patient"), th("What is missing"), th("Gap status"), th("Review status"),
          th("Actions"))), ui.workBody)));

    ui.results = $("div", { class: "view-root", hidden: true }, summaryCard, outcomeCard, gapsCard, patientsCard,
      worklistCard);
    ui.root.replaceChildren(ui.loadMsg, engineCard, ui.empty, ui.results);
    calc.outSig = "";
  }

  // ---------------------------------------------------------------- engine run card

  function syncChecks() {
    for (const m of measures()) {
      if (calc.known.has(m.measure)) continue;
      calc.known.add(m.measure);
      calc.picked.add(m.measure);
      const box = $("input", { type: "checkbox", id: `calc-m-${m.measure}`, onchange: e => {
        if (e.target.checked) calc.picked.add(m.measure);
        else calc.picked.delete(m.measure);
      } });
      box.checked = true;
      ui.boxes.set(m.measure, box);
      ui.checks.append($("label", { class: "calc-check", title: m.name || m.measure }, box,
        $("span", { class: "mono" }, m.measure)));
    }
  }

  function setRunMsg(text, bad) {
    ui.runMsg.textContent = text || "";
    ui.runMsg.classList.toggle("error", !!bad);
  }

  function renderRun() {
    const d = calc.data || {};
    const run = d.latest_run;
    const b = state.batch;
    const running = isRunning();

    ui.runStatus.replaceChildren(b ? $("span", {
      class: `status ${running ? "status-warn" : b.status === "failed" ? "status-bad" : "status-ok"}`,
    }, running ? "Running" : b.status === "failed" ? "Failed" : "Ready") : "");
    ui.runMeta.textContent = running ? statusText(b)
      : run ? `Last calculation: ${when(run.run_at)}` : "No calculation has run yet.";

    ui.runBtn.disabled = running || calc.starting || !calc.selectedPatients.size;
    ui.runBtn.textContent = running ? statusText(b) : "Run selected patients";

    if (running) {
      const idx = RUN_STEPS.findIndex(s => s[0] === b.step);
      ui.progress.replaceChildren(...RUN_STEPS.map(([key, text], i) => {
        const cls = idx < 0 ? "" : i < idx ? "done" : i === idx ? "running" : "";
        return $("li", { class: cls ? `step ${cls}` : "step" },
          $("span", { class: "dot" }, cls === "done" ? "✓" : cls === "running" ? "..." : "-"),
          $("span", { class: "step-name" }, text),
          $("span", { class: "step-detail" }, i === idx ? stepDetail(key, counts()) : ""));
      }));
    }
    ui.progress.hidden = !running;

    if (calc.waiting && !running) {
      calc.waiting = false;
      const failed = b && b.status === "failed";
      setRunMsg(failed ? `Calculation failed at ${b.step}${b.error ? `: ${b.error}` : ""}.`
        : "Calculation finished. The tables below show the stored results.", failed);
    }

  }

  async function runCalc() {
    let body = {};
    if (calc.known.size && !calc.picked.size) {
      setRunMsg("Choose at least one measure.", true);
      return;
    }
    if (!calc.selectedPatients.size) {
      setRunMsg("Choose at least one patient.", true);
      return;
    }
    body.patient_ids = [...calc.selectedPatients];
    if (calc.known.size && calc.picked.size < calc.known.size) body.measures = [...calc.picked];
    calc.starting = true;
    renderRun();
    setRunMsg("Starting...");
    try {
      await api("/api/calculation/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      calc.waiting = true;
      state.batch = await loadBatch();
      poll();
      setRunMsg(`Calculation started for ${body.patient_ids.length} selected patients.`);
      toast("Calculation started.");
    } catch (err) {
      setRunMsg(err.message, true);
    } finally {
      calc.starting = false;
      renderRun();
    }
  }

  // ---------------------------------------------------------------- summary, outcome table, gaps

  async function loadCandidates() {
    try {
      const r = await api(`/api/calculation/patients?offset=${calc.candidateOffset}&limit=10`);
      calc.candidates = r;
      calc.candidatesError = "";
      renderCandidates();
    } catch (err) {
      calc.candidates = null;
      calc.candidatesError = err.message;
      renderCandidates();
    }
  }

  function formatBytes(value) {
    if (value == null) return "Not available";
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${fmt(bytes / 1024, 1)} KB`;
    return `${fmt(bytes / (1024 * 1024), 1)} MB`;
  }

  function renderCandidates() {
    const page = calc.candidates;
    const rows = page ? page.patients || [] : [];
    ui.candidateBody.replaceChildren(...(rows.length ? rows.map(p => {
      const box = $("input", { type: "checkbox", checked: calc.selectedPatients.has(p.patient_id), onchange: e => {
        if (e.target.checked) calc.selectedPatients.add(p.patient_id);
        else calc.selectedPatients.delete(p.patient_id);
        renderRun();
      } });
      return $("tr", {}, $("td", {}, box), $("td", { class: "mono" }, p.patient_id),
        $("td", {}, formatBytes(p.input_bytes)), $("td", {}, when(p.stored_at)));
    }) : [emptyRow(4, calc.candidatesError ? `Could not load stored patients: ${calc.candidatesError}`
      : "No calculation-ready patients are available.")]));
    const total = page ? Number(page.total || 0) : 0;
    const shownStart = total ? Number(page.offset || 0) + 1 : 0;
    const shownEnd = Math.min(Number(page.offset || 0) + rows.length, total);
    ui.candidateCount.textContent = total ? `${calc.selectedPatients.size} selected · ${shownStart}-${shownEnd} of ${total}`
      : "";
    ui.candidatePrev.hidden = !page || !page.offset;
    ui.candidateNext.hidden = !page || Number(page.offset || 0) + rows.length >= total;
  }

  function renderKpis() {
    const ms = measures();
    const eligible = sum(ms, m => m.denominator - m.excluded);
    const met = sum(ms, m => m.met);
    const kpi = (label, value, note) => $("div", { class: "kpi" }, $("span", {}, label), $("b", {}, value),
      note ? $("small", {}, note) : null);
    ui.kpiNote.textContent = calc.data.measurement_year ? `Measurement year ${calc.data.measurement_year}` : "";
    const cards = [
      kpi("PATIENTS EVALUATED", n0(Math.max(0, ...ms.map(m => m.evaluated || 0))), "most in any one measure"),
      calc.data.latest_calculation_seconds == null ? null
        : kpi("CALCULATION TIME", durationText(calc.data.latest_calculation_seconds), "latest completed run"),
      kpi("MEASURE CHECKS", n0(sum(ms, m => m.evaluated)), "patient and measure pairs"),
      kpi("ELIGIBLE", n0(eligible), "in the measure, not excluded"),
      kpi("NUMERATOR MET", n0(met)),
      kpi("EXCLUSIONS", n0(sum(ms, m => m.excluded))),
      kpi("OPEN GAPS", n0(sum(ms, m => m.gaps_open))),
      kpi("CURRENT RATE", pct(met, eligible), "met divided by eligible"),
    ];
    ui.kpis.replaceChildren(...cards.filter(Boolean));
  }

  function renderOutcome() {
    const ms = measures();
    const sig = JSON.stringify(ms) + calc.selected;
    if (sig === calc.outSig) return;
    calc.outSig = sig;
    ui.outBody.replaceChildren(...(ms.length ? ms.map(m => {
      const pick = () => selectMeasure(m.measure);
      return $("tr", {
        tabindex: "0", title: "Show this measure's patients", class: m.measure === calc.selected ? "selected" : null,
        onclick: pick,
        onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } },
      },
      $("td", {}, $("div", {}, m.name || m.measure), $("div", { class: "pt" }, m.measure),
        m.inverse ? $("div", { class: "note small" },
          "Inverse logic: the engine's numerator is poor control, so met means not in the engine's numerator.") : null),
      $("td", {}, $("span", { class: m.star_measure ? "status status-ok" : "status" },
        m.star_measure ? "Star measure" : "Not a Star measure")),
      $("td", { class: "r" }, n0(m.denominator)),
      $("td", { class: "r" }, n0(m.met)),
      $("td", { class: "r" }, n0(m.excluded)),
      $("td", { class: "r" }, n0(m.not_met)),
      $("td", { class: "r" }, pct(m.met, m.denominator - m.excluded)),
      $("td", { class: "r" }, n0(m.gaps_open)),
      $("td", { class: "r" }, n0(m.gaps_closed)),
      $("td", { class: "r" }, n0(m.confirmed_by_reviewer)));
    }) : [emptyRow(10, "No measures stored yet.")]));
  }

  function renderBars() {
    const ms = measures();
    const total = sum(ms, m => m.gaps_open);
    ui.bars.replaceChildren(...(total ? ms.map(m => bar(`${m.name || m.measure} (${m.measure})`, m.gaps_open / total,
      `${n0(m.gaps_open)} open (${pct(m.gaps_open, total)})`)) : [$("p", { class: "empty" }, "No open gaps.")]));
  }

  // ---------------------------------------------------------------- patients and worklist

  function ensureSelected() {
    if (selectedMeasure()) return;
    const ms = measures();
    const first = ms.find(m => m.denominator > 0) || ms[0];
    calc.selected = first ? first.measure : null;
    calc.rows = null;
    calc.rowsFor = "";
    calc.shown = PAGE;
  }

  function selectMeasure(code) {
    if (code === calc.selected) return;
    calc.selected = code;
    calc.shown = PAGE;
    calc.rows = null;
    calc.rowsFor = "";
    calc.rowsError = "";
    renderOutcome();
    renderPatients();
    loadPatients();
  }

  async function loadPatients() {
    const m = selectedMeasure();
    if (!m) {
      calc.rows = null;
      renderPatients();
      return;
    }
    const run = calc.data.latest_run;
    const key = `${m.measure}|${run ? run.run_id : ""}|${m.computed_at || ""}`;
    if (calc.rows && calc.rowsFor === key) return;
    const mine = ++calc.seq;
    try {
      const r = await api(`/api/calculation/${encodeURIComponent(m.measure)}/patients`);
      if (mine !== calc.seq) return;
      calc.rows = r.patients || [];
      calc.rowsFor = key;
      calc.rowsError = "";
    } catch (err) {
      if (mine !== calc.seq) return;
      calc.rowsError = err.message;
    }
    renderPatients();
  }

  function gapLinks(p) {
    if (p.outcome !== "gap" || !p.gap_id) return null;
    return $("div", { class: "calc-actions" },
      $("button", { type: "button", class: "link calc-linkbtn", onclick: () => openOnEvidence(p.gap_id) },
        "Open in Evidence Review"),
      $("a", { class: "link", href: "#prioritization" }, "Prioritization"));
  }

  function renderPatients() {
    const m = selectedMeasure();
    const rows = calc.rows;
    ui.patTitle.textContent = m ? `Patient-level outcomes: ${m.name || m.measure} (${m.measure})`
      : "Patient-level outcomes";
    ui.patErr.hidden = !calc.rowsError;
    ui.patErr.textContent = calc.rowsError ? `Could not load patient results: ${calc.rowsError}` : "";

    for (const [value, btn, text] of ui.pills) {
      const n = rows ? (value ? rows.filter(p => p.outcome === value).length : rows.length) : null;
      btn.textContent = n == null ? text : `${text} (${n0(n)})`;
      btn.classList.toggle("active", calc.filter === value);
    }

    const q = calc.search.trim().toLowerCase();
    const list = rows ? rows.filter(p => (!calc.filter || p.outcome === calc.filter)
      && (!q || String(p.patient_id).toLowerCase().startsWith(q))) : [];
    const shown = list.slice(0, calc.shown);
    ui.patBody.replaceChildren(...(shown.length ? shown.map(p => {
      const o = OUTCOME[p.outcome] || ["muted", words(p.outcome)];
      return $("tr", {},
        $("td", { class: "pt" }, shortPt(p.patient_id)),
        $("td", {}, yesNo(p.in_denominator)),
        $("td", {}, yesNo(p.outcome === "met")),
        $("td", {}, yesNo(p.excluded)),
        $("td", {}, $("span", { class: o[0] }, o[1])),
        $("td", {}, words(p.gap_status)),
        $("td", {}, words(p.review_status)),
        $("td", {}, when(p.computed_at)),
        $("td", {}, gapLinks(p)));
    }) : [emptyRow(9, !m ? "Choose a measure above." : rows ? "No patients match." : calc.rowsError
      ? "No patient results to show." : "Loading patient results...")]));
    ui.patCount.textContent = rows ? `Showing ${shown.length} of ${list.length}` : "";
    ui.more.hidden = shown.length >= list.length;

    renderWorklist(m, rows);
  }

  function renderWorklist(m, rows) {
    const gaps = rows ? rows.filter(p => p.outcome === "gap") : [];
    const shown = gaps.slice(0, WORKLIST_MAX);
    ui.workTitle.textContent = m && rows
      ? `${m.measure}: showing ${shown.length} of ${n0(gaps.length)} gaps` : "";
    ui.workBody.replaceChildren(...(shown.length ? shown.map(p => $("tr", {},
      $("td", { class: "pt" }, shortPt(p.patient_id)),
      $("td", {}, missingText(p)),
      $("td", {}, words(p.gap_status)),
      $("td", {}, words(p.review_status)),
      $("td", {}, gapLinks(p))))
      : [emptyRow(5, !m ? "Choose a measure above." : rows ? "No gaps for this measure." : calc.rowsError
        ? "No gap list to show." : "Loading gaps...")]));
  }

  // ---------------------------------------------------------------- entry point

  async function render() {
    build();
    try {
      calc.data = await api("/api/calculation");
      calc.error = "";
    } catch (err) {
      calc.error = err.message;
    }
    ui.loadMsg.hidden = !calc.error;
    ui.loadMsg.textContent = calc.error ? `Could not load calculation results: ${calc.error}` : "";
    try {
      renderRun();
      if (!calc.data) {
        ui.results.hidden = true;
        ui.empty.hidden = true;
        return;
      }
      syncChecks();
      await loadCandidates();
      const hasResults = !!calc.data.latest_run || measures().some(m => m.evaluated > 0 || m.denominator > 0);
      ui.empty.hidden = hasResults;
      ui.results.hidden = !hasResults;
      if (!hasResults) return;
      ensureSelected();
      renderKpis();
      renderOutcome();
      renderBars();
      await loadPatients();
    } catch (err) {
      ui.loadMsg.hidden = false;
      ui.loadMsg.textContent = `Could not show calculation results: ${err.message}`;
    }
  }

  TABS.calculation = { render };
})();
