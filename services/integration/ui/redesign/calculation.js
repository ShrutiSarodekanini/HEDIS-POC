// HEDIS Calculation tab: Jyothi's measure engine run through our bridge. Every result is stored first; this tab reads it back.
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
  };
  const ui = { boxes: new Map() };

  const n0 = v => Number(v || 0).toLocaleString();
  const pct = (a, b) => (b > 0 ? `${fmt((100 * a) / b, 1)}%` : "-");
  const sum = (list, f) => list.reduce((s, m) => s + Number(f(m) || 0), 0);
  const yesNo = v => (v ? "Yes" : "No");
  const words = s => (s ? String(s).replaceAll("_", " ") : "-");
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
    ui.facts = $("dl", { class: "facts" });
    ui.runStatus = $("span", {});
    ui.checks = $("div", { class: "calc-checks" });
    ui.runBtn = $("button", { type: "button", class: "btn btn-primary", onclick: runCalc }, "Run calculation");
    ui.progress = $("ol", { class: "steps", hidden: true });
    ui.runMsg = $("p", { class: "note small", role: "status" });
    ui.lastRun = $("p", { class: "note small" });
    ui.boxes.clear();
    calc.known.clear();
    calc.picked.clear();

    const pickAll = on => {
      calc.picked = new Set(on ? calc.known : []);
      for (const box of ui.boxes.values()) box.checked = on;
    };
    const engineCard = $("section", { class: "card" },
      $("div", { class: "card-head" }, $("h2", {}, "Engine run"), ui.runStatus),
      $("p", { class: "note" }, "The engine runs through our bridge and every result is stored first in measure_result, " +
        "care_gap and care_gap_detail. This screen only reads those tables back."),
      $("div", { class: "grid-2" },
        $("div", { class: "stack" }, $("span", { class: "field-label" }, "Latest stored run"), ui.facts, ui.lastRun),
        $("div", { class: "stack" },
          $("span", { class: "field-label" }, "Measures to run"), ui.checks,
          $("div", { class: "btn-row" },
            $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => pickAll(true) }, "Select all"),
            $("button", { type: "button", class: "btn btn-secondary calc-small", onclick: () => pickAll(false) }, "Clear")),
          $("p", { class: "note small" }, "Runs the measure engine again for the latest batch's patients, stores the " +
            "results and gaps, then scores the gaps again."),
          $("div", { class: "btn-row" }, ui.runBtn),
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
    const rows = run
      ? [["Run id", String(run.run_id), "mono"], ["Engine", `${run.engine} ${run.engine_version}`],
        ["Measurement year", String(run.measurement_year)], ["Run at", when(run.run_at)]]
      : [["Engine", d.engine ? `${d.engine} ${d.engine_version || ""}`.trim() : "-"],
        ["Measurement year", d.measurement_year == null ? "-" : String(d.measurement_year)],
        ["Run at", "No run stored yet"]];
    rows.push(["Batch", b ? b.batch_id.slice(0, 8) : "None yet", "mono"], ["Batch status", b ? statusText(b) : "-"]);
    ui.facts.replaceChildren(...rows.flatMap(([k, v, cls]) => [$("dt", {}, k), $("dd", { class: cls }, v)]));

    ui.runStatus.replaceChildren(b ? $("span", {
      class: `status ${running ? "status-warn" : b.status === "failed" ? "status-bad" : "status-ok"}`,
    }, running ? "Running" : b.status === "failed" ? "Failed" : "Finished") : "");

    ui.runBtn.disabled = running || calc.starting;
    ui.runBtn.textContent = running ? statusText(b) : "Run calculation";

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

    const rc = counts().recalculation;
    const rm = rc && rc.measures && rc.measures.engine_run_id ? rc.measures : null;
    const scored = counts().prioritization;
    ui.lastRun.classList.remove("error");
    if (!running && b && b.status === "failed") {
      ui.lastRun.textContent = `Batch failed at ${b.step}${b.error ? `: ${b.error}` : ""}.`;
      ui.lastRun.classList.add("error");
    } else if (rm) {
      const which = Array.isArray(rc.requested) ? rc.requested.join(", ") : "all measures";
      ui.lastRun.textContent = `Last recalculation (${which}, engine run ${rm.engine_run_id}): ` +
        `${n0(rm.patients)} patients, ${n0(rm.gaps_open)} open gaps, ${n0(rm.gaps_closed_by_engine)} closed by ` +
        `the engine, ${n0(scored && scored.gaps_scored)} gaps scored again.`;
    } else {
      ui.lastRun.textContent = "";
    }
  }

  async function runCalc() {
    let body = {};
    if (calc.known.size && !calc.picked.size) {
      setRunMsg("Choose at least one measure.", true);
      return;
    }
    if (calc.known.size && calc.picked.size < calc.known.size) body = { measures: [...calc.picked] };
    calc.starting = true;
    renderRun();
    setRunMsg("Starting...");
    try {
      await api("/api/calculation/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      calc.waiting = true;
      state.batch = await loadBatch();
      poll();
      setRunMsg(`Calculation started for ${body.measures ? body.measures.length : "all"} measures.`);
      toast("Calculation started.");
    } catch (err) {
      setRunMsg(err.message, true);
    } finally {
      calc.starting = false;
      renderRun();
    }
  }

  // ---------------------------------------------------------------- summary, outcome table, gaps

  function renderKpis() {
    const ms = measures();
    const eligible = sum(ms, m => m.denominator - m.excluded);
    const met = sum(ms, m => m.met);
    const kpi = (label, value, note) => $("div", { class: "kpi" }, $("span", {}, label), $("b", {}, value),
      note ? $("small", {}, note) : null);
    ui.kpiNote.textContent = calc.data.measurement_year ? `Measurement year ${calc.data.measurement_year}` : "";
    ui.kpis.replaceChildren(
      kpi("PATIENTS EVALUATED", n0(Math.max(0, ...ms.map(m => m.evaluated || 0))), "most in any one measure"),
      kpi("MEASURE CHECKS", n0(sum(ms, m => m.evaluated)), "patient and measure pairs"),
      kpi("ELIGIBLE", n0(eligible), "in the measure, not excluded"),
      kpi("NUMERATOR MET", n0(met)),
      kpi("EXCLUSIONS", n0(sum(ms, m => m.excluded))),
      kpi("OPEN GAPS", n0(sum(ms, m => m.gaps_open))),
      kpi("CURRENT RATE", pct(met, eligible), "met divided by eligible"));
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
      const hasResults = !!calc.data.latest_run || measures().some(m => m.evaluated > 0 || m.denominator > 0);
      ui.empty.hidden = hasResults;
      ui.results.hidden = !hasResults;
      syncChecks();
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
