// Star Rating tab: Pradeep's star functions on the stored measure results, read only. Nothing here is saved.
// The DOM is built once; later renders repaint only the parts whose data changed, so the chosen measure and
// ticked gaps survive the 3 second refresh.
"use strict";
(() => {
  const st = { catalog: null, catalogError: "", measuresError: "", gapError: false, measure: "", gaps: [],
    ticks: new Set(), epoch: 0, loadedFor: "", sig: "", notesUrl: "", gapBoxes: [] };
  const ui = {};
  const sigs = {};

  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  const rows = () => (state.measures && state.measures.measures) || [];
  const cardOf = id => rows().find(m => m.measure === id) || null;
  const entryOf = id => (st.catalog && st.catalog.measures.find(c => c.measure_id === id)) || null;
  const isStar = id => {
    const e = entryOf(id);
    const c = cardOf(id);
    return !!((e && e.cut_points.length) || (c && c.cut_points && c.cut_points.length));
  };
  const plain = v => String(Number(v));
  const signed = (v, d = 2) => (v == null ? "-" : `${Number(v) > 0 ? "+" : ""}${fmt(Number(v), d)}`);
  const val = v => (v == null ? "-" : String(v));
  const mean = list => (list.length ? list.reduce((a, b) => a + b, 0) / list.length : null);
  const yesNo = v => (v ? "yes" : "no");
  const kv = list => list.flatMap(([k, v]) => [$("dt", {}, k), $("dd", {}, v)]);
  const starText = s => $("span", { class: "st-star" }, starRow(s), s == null ? "No star" : String(s));
  const noteErr = text => $("p", { class: "note error" }, text);
  const calcFor = (id, rate, denominator) => post("/api/stars/calculate", { measure_id: id,
    rate: rate == null ? null : Number(rate), denominator: Number(denominator) });

  function repaint(key, data, box, make) {
    const sig = JSON.stringify(data);
    if (sigs[key] === sig) return;
    sigs[key] = sig;
    box.replaceChildren(...make());
  }

  // ---------------------------------------------------------------- build (once)

  function build() {
    for (const k of Object.keys(sigs)) delete sigs[k];
    st.loadedFor = "";
    ui.tiles = $("div", { class: "tile-row" });
    ui.notesLink = $("a", { class: "link", target: "_blank", rel: "noopener", hidden: true });
    ui.catalogNote = $("p", { class: "note" });
    ui.catalogBox = $("div", { class: "table-wrap" });
    ui.cardsNote = $("span", { class: "muted small" });
    ui.cardsErr = $("p", { class: "note error", hidden: true });
    ui.cardsBox = $("div", { class: "measure-grid" });

    ui.select = $("select", { class: "input", id: "st-measure", onchange: e => changeMeasure(e.target.value) });
    ui.facts = $("dl", { class: "facts" });
    ui.calcBtn = $("button", { type: "button", class: "btn btn-primary", onclick: runCalc },
      "Calculate current star (actual values)");
    ui.calcOut = $("div", { class: "st-out" });
    ui.calcCard = $("section", { class: "card", hidden: true },
      $("div", { class: "card-head" }, $("h2", {}, "Direct star calculation")),
      $("div", { class: "grid-2" },
        $("div", { class: "st-col" },
          $("div", { class: "field" }, $("label", { class: "field-label", for: "st-measure" }, "Measure"), ui.select),
          ui.facts, $("div", { class: "btn-row" }, ui.calcBtn)),
        ui.calcOut));

    ui.cmpMeasure = $("span", { class: "muted small" });
    ui.gapCount = $("span", { class: "label-strong" });
    ui.selected = $("span", { class: "muted small" });
    ui.selectAll = $("button", { type: "button", class: "btn btn-secondary", onclick: selectAllOpen }, "Select all open");
    ui.clear = $("button", { type: "button", class: "btn btn-secondary", onclick: clearTicks }, "Clear");
    ui.gapMsg = $("p", { class: "note" });
    ui.gapList = $("div", { class: "st-gaplist" });
    ui.applyBtn = $("button", { type: "button", class: "btn btn-primary", onclick: applyClosures },
      "Apply selected closures");
    ui.compareBtn = $("button", { type: "button", class: "btn btn-secondary", onclick: compareBeforeAfter },
      "Compare before vs after (actual values)");
    ui.applyOut = $("div", { class: "st-out" });
    ui.compareOut = $("div", { class: "st-out" });
    ui.cmpCard = $("section", { class: "card", hidden: true },
      $("div", { class: "card-head" }, $("h2", {}, "Before and after comparison"), ui.cmpMeasure),
      $("p", { class: "note" }, "Tick open gaps to see what closing them would do to the rate and the star. " +
        "Nothing is saved."),
      $("div", { class: "card-head" }, ui.gapCount,
        $("div", { class: "btn-row" }, ui.selected, ui.selectAll, ui.clear)),
      ui.gapMsg, ui.gapList,
      $("div", { class: "btn-row" }, ui.applyBtn, ui.compareBtn),
      ui.applyOut, ui.compareOut);

    ui.page = $("div", { class: "view-root st-page" },
      $("div", { class: "separate-data" }, $("span", {}, $("b", {}, "Pradeep's star functions"),
        " on the stored measure results; nothing on this screen is saved. His rate keeps 2 decimals; the gap " +
        "prioritization rounds to a whole percent first, so a star can differ near a cut point.")),
      ui.tiles,
      $("section", { class: "card" },
        $("div", { class: "card-head" }, $("h2", {}, "All measures, weights and star ranges"), ui.notesLink),
        ui.catalogNote, ui.catalogBox),
      $("section", { class: "card" },
        $("div", { class: "card-head" }, $("h2", {}, "Measure cards"), ui.cardsNote),
        ui.cardsErr, ui.cardsBox),
      ui.calcCard, ui.cmpCard);
    byId("stars-root").replaceChildren(ui.page);
  }

  // ---------------------------------------------------------------- repaint from data

  function update() {
    const ms = rows();
    paintTiles(ms);
    paintCatalog();
    paintCards(ms);

    if (!ms.some(m => m.measure === st.measure)) {
      st.measure = (ms.find(m => m.stars != null) || ms[0] || {}).measure || "";
      resetResults();
      st.ticks.clear();
      st.gaps = [];
      st.epoch += 1;
      st.loadedFor = "";
    }
    ui.calcCard.hidden = ui.cmpCard.hidden = !ms.length;
    if (!ms.length) return;

    repaint("select", ms.map(m => [m.measure, m.name]), ui.select, () => ms.map(m =>
      $("option", { value: m.measure }, `${m.measure} (${m.name || m.measure})`)));
    ui.select.value = st.measure;
    paintFacts();
    ui.cmpMeasure.textContent = st.measure;

    const m = cardOf(st.measure);
    const sig = `${st.measure}|${m.numerator}|${m.denominator}|${m.open_gaps}`;
    const changed = st.loadedFor === st.measure && sig !== st.sig;
    st.sig = sig;
    if (st.loadedFor !== st.measure || changed) {
      if (changed) resetResults();
      loadGaps();
    }
  }

  function paintTiles(ms) {
    const rated = ms.filter(m => m.stars != null);
    const base = mean(rated.map(m => m.stars));
    const proj = mean(rated.map(m => m.projected_stars ?? m.stars));
    const delta = base == null || proj == null ? null : proj - base;
    const tone = delta == null || delta === 0 ? "tile-low" : delta > 0 ? "tile-high" : "tile-bad";
    repaint("tiles", [rated.length, base, proj], ui.tiles, () => [
      $("div", { class: "tile tile-low" }, $("span", {}, "RATED MEASURES"), $("b", {}, String(rated.length))),
      $("div", { class: "tile tile-high" }, $("span", {}, "BASELINE OVERALL STAR"),
        $("b", {}, base == null ? "-" : fmt(base, 1))),
      $("div", { class: "tile tile-high" }, $("span", {}, "PROJECTED OVERALL STAR"),
        $("b", {}, proj == null ? "-" : fmt(proj, 1))),
      $("div", { class: `tile ${tone}` }, $("span", {}, "OVERALL CHANGE"),
        $("b", {}, delta == null ? "-" : signed(delta, 1)))]);
  }

  function paintCatalog() {
    const c = st.catalog;
    ui.catalogNote.textContent = c ? `Measurement year ${c.measurement_year}, CMS ${c.star_year} Star Ratings. ` +
      "Star ranges are the CMS cut points." : "";
    if (c) ui.notesLink.textContent = `CMS ${c.star_year} technical notes`;
    repaint("catalog", [c, st.catalogError], ui.catalogBox, () => {
      if (!c) return [st.catalogError ? noteErr(`Could not load the measure catalog: ${st.catalogError}`)
        : $("p", { class: "empty" }, "Loading the measure catalog.")];
      if (!c.measures.length) return [$("p", { class: "empty" }, "No measures in the catalog.")];
      const heads = ["Measure", "Measurement year", "Weight", "1 star", "2 stars", "3 stars", "4 stars", "5 stars"];
      return [$("table", { class: "table compact" },
        $("thead", {}, $("tr", {}, ...heads.map((h, i) => $("th", { class: i === 1 || i === 2 ? "r" : null }, h)))),
        $("tbody", {}, ...c.measures.map(e => {
          const byStar = new Map(e.cut_points.map(p => [p.stars, p]));
          return $("tr", {},
            $("td", {}, $("div", {}, e.name || e.measure_id), $("div", { class: "pt" }, e.measure_id),
              e.cut_points.length ? null : $("div", { class: "note small" }, "Not a Star measure")),
            $("td", { class: "r" }, String(e.measurement_year)),
            $("td", { class: "r" }, e.weight == null ? "-" : plain(e.weight)),
            ...[1, 2, 3, 4, 5].map(n => {
              const p = byStar.get(n);
              return $("td", { class: "st-range" }, p ? `${plain(p.low)} to ${plain(p.high ?? 100)}%` : "-");
            }));
        })))];
    });
  }

  function paintCards(ms) {
    const year = state.measures && state.measures.cms_rating_year;
    ui.cardsNote.textContent = year ? `CMS ${year} cut points` : "";
    ui.cardsErr.hidden = !st.measuresError;
    ui.cardsErr.textContent = st.measuresError ? `Could not refresh the measure results: ${st.measuresError}` : "";
    repaint("cards", ms, ui.cardsBox, () => ms.length ? ms.map(cardNode)
      : [$("p", { class: "empty" }, "No measure results yet. Ingest and persist a batch on Data Ingestion.")]);
  }

  function cardNode(m) {
    const star = (m.cut_points || []).length > 0;
    const extra = (m.confirmed_pending_engine || 0) + (m.excluded_pending_engine || 0) > 0;
    return $("div", { class: "mcard" },
      $("div", { class: "mcard-top" }, $("span", { class: "mcard-name" }, m.name || m.measure),
        $("span", { class: "mcard-code" }, m.measure)),
      $("div", { class: "mcard-top" }, $("span", { class: "mcard-rate" }, m.rate == null ? "-" : `${fmt(m.rate, 2)}%`),
        star ? starRow(m.stars) : $("span", { class: "mcard-line" }, "Not a Star measure")),
      $("span", { class: "mcard-line" }, `${m.numerator} of ${m.denominator} met, ${m.open_gaps} open gaps`),
      star ? cutChips(m) : null,
      extra ? $("span", { class: "mcard-line st-proj" }, `Projected ${fmt(m.projected_rate, 2)}%`,
        starRow(m.projected_stars),
        `after reviewer-confirmed closures and exclusions reach the engine (${m.confirmed_pending_engine || 0} ` +
        `confirmed, ${m.excluded_pending_engine || 0} excluded)`) : null);
  }

  function paintFacts() {
    const m = cardOf(st.measure);
    const e = entryOf(st.measure);
    repaint("facts", [m, e, st.catalog && st.catalog.measurement_year], ui.facts, () => m ? kv([
      ["Measurement year", e ? String(e.measurement_year) : val(st.catalog && st.catalog.measurement_year)],
      ["Weight", !st.catalog ? "-" : e && e.weight != null ? plain(e.weight) : "Not a Star measure"],
      ["Total in measure", String(m.denominator)],
      ["Met", String(m.numerator)],
      ["Current rate", m.rate == null ? "-" : `${fmt(m.rate, 2)}%`],
      ["Current star", isStar(m.measure) ? starText(m.stars) : "Not a Star measure"]]) : []);
  }

  // ---------------------------------------------------------------- measure selection and gaps

  function resetResults() {
    ui.calcOut.replaceChildren();
    ui.applyOut.replaceChildren();
    ui.compareOut.replaceChildren();
  }

  function changeMeasure(id) {
    st.measure = id;
    st.ticks.clear();
    st.gaps = [];
    st.epoch += 1;
    st.loadedFor = "";
    resetResults();
    update();
  }

  async function loadGaps() {
    const measure = st.measure;
    const epoch = st.epoch;
    st.loadedFor = measure;
    ui.gapMsg.className = "note";
    ui.gapMsg.textContent = "Loading the gap list.";
    try {
      const r = await post("/api/stars/what-if", { measure_id: measure, gap_ids: [] });
      if (epoch !== st.epoch) return;
      rememberNotes(r.measure);
      st.gaps = r.care_gaps || [];
      st.gapError = false;
      ui.gapMsg.textContent = "";
    } catch (err) {
      if (epoch !== st.epoch) return;
      st.gaps = [];
      st.gapError = true;
      st.loadedFor = "";
      ui.gapMsg.className = "note error";
      ui.gapMsg.textContent = `Could not load the gaps: ${err.message}`;
    }
    drawGaps();
  }

  function drawGaps() {
    const open = st.gaps.filter(g => g.evidence_status === "open");
    const openIds = new Set(open.map(g => g.gap_id));
    st.ticks = new Set([...st.ticks].filter(id => openIds.has(id)));
    ui.gapCount.textContent = st.gaps.length ? `${open.length} open, ${st.gaps.length - open.length} met` : "";
    st.gapBoxes = [];
    const items = st.gaps.map(g => {
      const closed = g.evidence_status !== "open";
      const box = $("input", { type: "checkbox", disabled: closed, onchange: e => {
        if (e.target.checked) st.ticks.add(g.gap_id); else st.ticks.delete(g.gap_id);
        updateSelected();
      } });
      box.checked = !closed && st.ticks.has(g.gap_id);
      st.gapBoxes.push([g.gap_id, box]);
      return $("label", { class: `st-gap${closed ? " met" : ""}` }, box,
        $("span", { class: "mono" }, `gap ${String(g.gap_id).slice(0, 8)} ${closed ? "(already met)" : "(open)"}`));
    });
    if (!items.length && !st.gapError) items.push($("p", { class: "empty" }, "No gaps for this measure."));
    ui.gapList.replaceChildren(...items);
    ui.selectAll.disabled = ui.clear.disabled = !open.length;
    updateSelected();
  }

  function updateSelected() {
    ui.selected.textContent = `${st.ticks.size} selected`;
    ui.applyBtn.disabled = !st.ticks.size;
  }

  function selectAllOpen() {
    for (const [id, box] of st.gapBoxes) if (!box.disabled) { box.checked = true; st.ticks.add(id); }
    updateSelected();
  }

  function clearTicks() {
    for (const [, box] of st.gapBoxes) box.checked = false;
    st.ticks.clear();
    updateSelected();
  }

  function rememberNotes(measure) {
    const url = measure && measure.cms_technical_notes_url;
    if (!url || url === st.notesUrl || !/^https?:\/\//i.test(url)) return;
    st.notesUrl = url;
    ui.notesLink.href = url;
    ui.notesLink.hidden = false;
  }

  // ---------------------------------------------------------------- actions

  function resultBox(title, r, star) {
    if (r.rating_status === "unavailable" && !star) {
      return $("div", { class: "st-result" }, title ? $("p", { class: "label-strong" }, title) : null,
        $("p", { class: "note" }, "Not a Star measure: no CMS cut points, so no star."));
    }
    const applied = r.applied_scoring_value == null ? null : Number(r.applied_scoring_value);
    const chips = (r.applied_cut_points || []).length
      ? cutChips({ rounded: applied, cut_points: r.applied_cut_points }) : "-";
    return $("div", { class: "st-result" }, title ? $("p", { class: "label-strong" }, title) : null,
      $("dl", { class: "facts" }, ...kv([
        ["Status", $("span", { class: `status ${r.rating_status === "assigned" ? "status-ok" : "status-warn"}` },
          val(r.rating_status))],
        ["Measure", val(r.measure_code)],
        ["Measurement year", val(r.measurement_year)],
        ["CMS rating year", val(r.cms_rating_year)],
        ["Input score", val(r.input_scoring_value)],
        ["Applied score", val(r.applied_scoring_value)],
        ["Assigned star", starText(r.assigned_star_rating)],
        ["Explanation", val(r.explanation)],
        ["Applied cut points", chips]])));
  }

  function whatIfBox(r) {
    const b = r.baseline || {};
    const a = r.recalculated || {};
    const c = r.changes || {};
    const rate = v => (v == null ? "-" : `${fmt(Number(v), 2)}%`);
    return $("div", { class: "st-result" },
      $("dl", { class: "facts" }, ...kv([
        ["Before rate", rate(b.performance_rate)],
        ["After rate", rate(a.performance_rate)],
        ["Before star", starText(b.sample_star_rating)],
        ["After star", starText(a.sample_star_rating)],
        ["Selected closures", String((r.selected_gap_ids || []).length)],
        ["Rate change (points)", signed(c.performance_rate_change_percentage_points)],
        ["Relative improvement %", c.relative_improvement_percent == null ? "-"
          : `${signed(c.relative_improvement_percent)}%`],
        ["Star changed", yesNo(c.star_rating_changed)]])),
      ...(r.warnings || []).map(w => $("p", { class: "note" }, String(w))),
      r.disclaimer ? $("p", { class: "note small" }, String(r.disclaimer)) : null);
  }

  async function runCalc() {
    const m = cardOf(st.measure);
    if (!m) return;
    const epoch = st.epoch;
    ui.calcBtn.disabled = true;
    ui.calcOut.replaceChildren($("p", { class: "note" }, "Calculating."));
    try {
      const r = await calcFor(m.measure, m.rate, m.denominator);
      if (epoch === st.epoch) ui.calcOut.replaceChildren(resultBox("Calculation result", r, isStar(m.measure)));
    } catch (err) {
      if (epoch === st.epoch) ui.calcOut.replaceChildren(noteErr(err.message));
    } finally {
      ui.calcBtn.disabled = false;
    }
  }

  async function whatIfNow() {
    const r = await post("/api/stars/what-if", { measure_id: st.measure, gap_ids: [...st.ticks] });
    rememberNotes(r.measure);
    return r;
  }

  async function applyClosures() {
    if (!st.ticks.size) return;
    const epoch = st.epoch;
    ui.applyBtn.disabled = ui.compareBtn.disabled = true;
    ui.applyOut.replaceChildren($("p", { class: "note" }, "Calculating."));
    try {
      const r = await whatIfNow();
      if (epoch === st.epoch) ui.applyOut.replaceChildren(whatIfBox(r));
    } catch (err) {
      if (epoch === st.epoch) ui.applyOut.replaceChildren(noteErr(err.message));
    } finally {
      ui.compareBtn.disabled = false;
      updateSelected();
    }
  }

  async function compareBeforeAfter() {
    const id = st.measure;
    if (!id) return;
    const epoch = st.epoch;
    ui.applyBtn.disabled = ui.compareBtn.disabled = true;
    ui.compareOut.replaceChildren($("p", { class: "note" }, "Calculating."));
    try {
      const r = await whatIfNow();
      const denominator = r.baseline.denominator;
      const [before, after] = await Promise.all([calcFor(id, r.baseline.performance_rate, denominator),
        calcFor(id, r.recalculated.performance_rate, denominator)]);
      if (epoch !== st.epoch) return;
      ui.applyOut.replaceChildren(whatIfBox(r));
      const star = isStar(id);
      const changed = before.assigned_star_rating !== after.assigned_star_rating;
      ui.compareOut.replaceChildren(
        $("div", { class: "grid-2" }, resultBox("Before", before, star), resultBox("After", after, star)),
        $("p", { class: "label-strong" }, `Star changed: ${yesNo(changed)}`));
    } catch (err) {
      if (epoch === st.epoch) ui.compareOut.replaceChildren(noteErr(err.message));
    } finally {
      ui.compareBtn.disabled = false;
      updateSelected();
    }
  }

  // ---------------------------------------------------------------- tab

  TABS.stars = {
    render: async () => {
      const root = byId("stars-root");
      if (!ui.page || !root.contains(ui.page)) build();
      const loads = [api("/api/measures").then(r => { state.measures = r; st.measuresError = ""; })
        .catch(err => { st.measuresError = err.message; })];
      if (!st.catalog) {
        loads.push(api("/api/stars/catalog").then(r => { st.catalog = r; st.catalogError = ""; })
          .catch(err => { st.catalogError = err.message; }));
      }
      await Promise.all(loads);
      update();
    },
  };
})();
