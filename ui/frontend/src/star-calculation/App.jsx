import React, { useEffect, useMemo, useState } from "react";
import HedisSidebarLayout from "./components/HedisSidebarLayout";
import "./styles.css";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function StarResult({ title, result }) {
  if (!result) return null;
  return (
    <div className="result-box">
      <h3>{title}</h3>
      <p><strong>Status:</strong> {result.rating_status}</p>
      <p><strong>Measure:</strong> {result.measure_code}</p>
      <p><strong>Measurement Year:</strong> {result.measurement_year}</p>
      <p><strong>CMS Rating Year:</strong> {result.cms_rating_year}</p>
      <p><strong>Input Score:</strong> {String(result.input_scoring_value ?? "N/A")}</p>
      <p><strong>Applied Score:</strong> {String(result.applied_scoring_value ?? "N/A")}</p>
      <p><strong>Assigned Star:</strong> {String(result.assigned_star_rating ?? "N/A")}</p>
      <p><strong>Explanation:</strong> {result.explanation}</p>
    </div>
  );
}

function formatRanges(cutPoints) {
  const sorted = [...cutPoints].sort((a, b) => a.star - b.star);
  const byStar = new Map(sorted.map((c) => [c.star, c.minimum_rate]));
  const out = [];
  for (let star = 1; star <= 5; star += 1) {
    const min = star === 1 ? 0.1 : byStar.get(star);
    if (min == null) {
      out.push({ star, label: "N/A" });
      continue;
    }
    const nextMin = byStar.get(star + 1);
    const max = nextMin == null ? 100 : Math.max(min, Number((nextMin - 0.1).toFixed(1)));
    out.push({ star, label: `${min}% to ${max}%` });
  }
  return out;
}

export default function App() {
  const [activePage, setActivePage] = useState("star-calculation");

  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [catalog, setCatalog] = useState({ cms_rating_year: 2027, measures: [] });

  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const [dashboard, setDashboard] = useState(null);

  const [selectedMeasureCode, setSelectedMeasureCode] = useState("");
  const [selectedGapIds, setSelectedGapIds] = useState([]);

  const [calcLoading, setCalcLoading] = useState(false);
  const [calcError, setCalcError] = useState("");
  const [calcResult, setCalcResult] = useState(null);

  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState("");
  const [compareResult, setCompareResult] = useState(null);

  const selectedMeasureCard = useMemo(() => {
    if (!dashboard || !selectedMeasureCode) return null;
    return dashboard.measure_cards.find((m) => m.measure_id === selectedMeasureCode) || null;
  }, [dashboard, selectedMeasureCode]);

  const selectedMeasureDetail = useMemo(
    () => catalog.measures.find((m) => m.measure_code === selectedMeasureCode) || null,
    [catalog.measures, selectedMeasureCode]
  );

  const navItems = useMemo(
    () => [
      { key: "dashboard", label: "Dashboard", icon: "⌂", to: "/" },
      { key: "star-calculation", label: "Star Calculation", icon: "★", to: "/star-calculation" },
    ],
    []
  );

  useEffect(() => {
    async function loadInitial() {
      setCatalogLoading(true);
      setDashboardLoading(true);
      try {
        const [catalogRes, dashRes] = await Promise.all([
          fetch(apiUrl("/api/star-rating/catalog")),
          fetch(apiUrl("/api/star-dashboard/calculate"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          }),
        ]);

        const catalogPayload = await catalogRes.json();
        if (!catalogRes.ok) {
          setCatalogError(catalogPayload.detail || "Failed to load measure catalog.");
        } else {
          setCatalog(catalogPayload);
        }

        const dashPayload = await dashRes.json();
        if (!dashRes.ok) {
          setDashboardError(dashPayload.detail || "Failed to load live measure values.");
        } else {
          setDashboard(dashPayload);
          const firstMeasure = dashPayload.measure_cards?.[0]?.measure_id;
          if (firstMeasure) {
            setSelectedMeasureCode(firstMeasure);
          }
        }
      } catch (error) {
        setCatalogError(String(error));
        setDashboardError(String(error));
      } finally {
        setCatalogLoading(false);
        setDashboardLoading(false);
      }
    }
    loadInitial();
  }, []);

  async function refreshDashboard(measureCode, gapIds) {
    setDashboardLoading(true);
    setDashboardError("");
    try {
      const response = await fetch(apiUrl("/api/star-dashboard/calculate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ measure_id: measureCode || null, selected_gap_ids: gapIds || [] }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setDashboardError(payload.detail || "Failed to refresh dashboard values.");
        return;
      }
      setDashboard(payload);
    } catch (error) {
      setDashboardError(String(error));
    } finally {
      setDashboardLoading(false);
    }
  }

  async function changeMeasure(nextMeasureCode) {
    setSelectedMeasureCode(nextMeasureCode);
    setSelectedGapIds([]);
    await refreshDashboard(nextMeasureCode, []);
    setCalcResult(null);
    setCompareResult(null);
    setCalcError("");
    setCompareError("");
  }

  function toggleGap(gapId, checked) {
    setSelectedGapIds((current) => {
      if (checked) {
        return [...current, gapId];
      }
      return current.filter((id) => id !== gapId);
    });
  }

  async function applySelectedGaps() {
    await refreshDashboard(selectedMeasureCode, selectedGapIds);
    setCompareResult(null);
    setCompareError("");
  }

  async function runDirectCalculation() {
    if (!selectedMeasureCard) {
      setCalcError("Select a measure first.");
      return;
    }
    setCalcLoading(true);
    setCalcError("");
    setCalcResult(null);
    try {
      const response = await fetch(apiUrl("/api/star-rating/calculate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cms_rating_year: catalog.cms_rating_year,
          result: {
            measure_code: selectedMeasureCard.measure_id,
            measurement_year: selectedMeasureCard.measurement_year,
            scoring_value: selectedMeasureCard.baseline_rate,
            denominator: selectedMeasureCard.total_gaps,
            result_status: "completed",
            scoring_value_kind: "cms_scoring_value",
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setCalcError(payload.detail || "Star calculation failed.");
        return;
      }
      setCalcResult(payload);
    } catch (error) {
      setCalcError(String(error));
    } finally {
      setCalcLoading(false);
    }
  }

  async function runComparison() {
    if (!selectedMeasureCard) {
      setCompareError("Select a measure first.");
      return;
    }
    setCompareLoading(true);
    setCompareError("");
    setCompareResult(null);
    try {
      const response = await fetch(apiUrl("/api/star-rating/compare"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cms_rating_year: catalog.cms_rating_year,
          before: {
            measure_code: selectedMeasureCard.measure_id,
            measurement_year: selectedMeasureCard.measurement_year,
            scoring_value: selectedMeasureCard.baseline_rate,
            denominator: selectedMeasureCard.total_gaps,
            result_status: "completed",
            scoring_value_kind: "cms_scoring_value",
          },
          after: {
            measure_code: selectedMeasureCard.measure_id,
            measurement_year: selectedMeasureCard.measurement_year,
            scoring_value: selectedMeasureCard.projected_rate,
            denominator: selectedMeasureCard.total_gaps,
            result_status: "completed",
            scoring_value_kind: "cms_scoring_value",
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setCompareError(payload.detail || "Comparison failed.");
        return;
      }
      setCompareResult(payload);
    } catch (error) {
      setCompareError(String(error));
    } finally {
      setCompareLoading(false);
    }
  }

  return (
    <HedisSidebarLayout
      title="Star Calculation Dashboard"
      subtitle="All values come from live measure data, star cut points, and weights from the backend."
      items={navItems}
      activeKey={activePage}
      onSelect={setActivePage}
    >
      <main className="star-app">
      <header className="hero">
        <h1>Star Calculation Dashboard</h1>
        <p>All values come from live measure data, star cut points, and weights from the backend.</p>
      </header>

      {dashboard ? (
        <section className="panel summary-panel">
          <div className="summary-grid">
            <div className="summary-card">
              <span>Rated Measures</span>
              <strong>{dashboard.overall?.rated_measure_count ?? 0}</strong>
            </div>
            <div className="summary-card">
              <span>Baseline Overall Star</span>
              <strong>{dashboard.overall?.baseline_star ?? "N/A"}</strong>
            </div>
            <div className="summary-card">
              <span>Projected Overall Star</span>
              <strong>{dashboard.overall?.projected_star ?? "N/A"}</strong>
            </div>
            <div className="summary-card">
              <span>Overall Delta</span>
              <strong>{dashboard.overall?.star_delta ?? "N/A"}</strong>
            </div>
          </div>
        </section>
      ) : null}

      <div className="main-grid">
        <section className="panel">
          <h2>All Measures, Weights, and Star Ranges</h2>
          {catalogLoading ? <p>Loading measure catalog...</p> : null}
          {catalogError ? <p className="error">{catalogError}</p> : null}
          {!catalogLoading && !catalogError ? (
            <>
              <p className="note">
                CMS Year {catalog.cms_rating_year}. <a href={catalog.cms_technical_notes_url} target="_blank" rel="noreferrer">Technical Notes</a>
                {" | "}
                <a href={catalog.cms_measure_set_url} target="_blank" rel="noreferrer">Measure Set</a>
              </p>
              <div className="table-wrap">
                <table className="cutpoint-table">
                  <thead>
                    <tr>
                      <th>Measure</th>
                      <th>MY</th>
                      <th>Weight</th>
                      <th>1 Star</th>
                      <th>2 Star</th>
                      <th>3 Star</th>
                      <th>4 Star</th>
                      <th>5 Star</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalog.measures.map((measure) => {
                      const ranges = formatRanges(measure.cut_points || []);
                      return (
                        <tr key={`${measure.measure_code}-${measure.measurement_year}`}>
                          <td>{measure.measure_code} ({measure.measure_name || measure.measure_code})</td>
                          <td>{measure.measurement_year}</td>
                          <td>{measure.weight ?? "N/A"}</td>
                          <td>{ranges[0].label}</td>
                          <td>{ranges[1].label}</td>
                          <td>{ranges[2].label}</td>
                          <td>{ranges[3].label}</td>
                          <td>{ranges[4].label}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </section>

        <div className="right-col">
          <section className="panel">
            <h2>Direct Star Calculation</h2>
            <label>
              Measure
              <select value={selectedMeasureCode} onChange={(e) => changeMeasure(e.target.value)}>
                {(dashboard?.measure_cards || []).map((measure) => {
                  const m = catalog.measures.find((c) => c.measure_code === measure.measure_id && c.measurement_year === measure.measurement_year);
                  return (
                    <option value={measure.measure_id} key={measure.measure_id}>
                      {measure.measure_id} ({m?.measure_name || measure.measure_name || measure.measure_id})
                    </option>
                  );
                })}
              </select>
            </label>

            {dashboardLoading ? <p>Loading live values...</p> : null}
            {dashboardError ? <p className="error">{dashboardError}</p> : null}

            {selectedMeasureCard ? (
              <div className="result-box">
                <p><strong>Measure:</strong> {selectedMeasureCard.measure_id} ({selectedMeasureDetail?.measure_name || selectedMeasureCard.measure_name || selectedMeasureCard.measure_id})</p>
                <p><strong>Measurement Year:</strong> {selectedMeasureCard.measurement_year}</p>
                <p><strong>Weight:</strong> {selectedMeasureDetail?.weight ?? "N/A"}</p>
                <p><strong>Total Gaps (denominator):</strong> {selectedMeasureCard.total_gaps}</p>
                <p><strong>Closed Gaps (numerator):</strong> {selectedMeasureCard.baseline_closed}</p>
                <p><strong>Current Rate:</strong> {selectedMeasureCard.baseline_rate ?? "N/A"}%</p>
                <p><strong>Current Projected Star:</strong> {selectedMeasureCard.baseline_star ?? "N/A"}</p>
              </div>
            ) : null}

            <button onClick={runDirectCalculation} disabled={calcLoading || !selectedMeasureCard}>
              {calcLoading ? "Calculating..." : "Calculate Current Star (Actual Values)"}
            </button>
            <div className="calc-result-slot">
              {calcError ? <p className="error">{calcError}</p> : null}
              <StarResult title="Calculation Result" result={calcResult} />
            </div>
          </section>
        </div>
      </div>

      <section className="panel full-width-panel">
        <h2>Before vs After Comparison</h2>
        <p className="note">Select open gaps to simulate closure, then compare actual before/after star values.</p>

        <div className="gap-grid">
          {(dashboard?.selected_measure_gaps || []).length ? (
            dashboard.selected_measure_gaps.map((gap) => (
              <label key={gap.care_gap_id} className="gap-item">
                <input
                  type="checkbox"
                  checked={selectedGapIds.includes(gap.care_gap_id)}
                  disabled={gap.is_closed}
                  onChange={(e) => toggleGap(gap.care_gap_id, e.target.checked)}
                />
                <span>
                  {gap.care_gap_id} {gap.is_closed ? "(already closed)" : "(open)"}
                </span>
              </label>
            ))
          ) : (
            <p>No gaps available for this measure.</p>
          )}
        </div>

        <div className="action-row">
          <button onClick={applySelectedGaps} disabled={dashboardLoading || !selectedMeasureCode}>Apply Selected Closures</button>
          <button onClick={runComparison} disabled={compareLoading || !selectedMeasureCard}>
            {compareLoading ? "Comparing..." : "Compare Before vs After (Actual Values)"}
          </button>
        </div>

        {selectedMeasureCard ? (
          <div className="result-box">
            <p><strong>Before Rate:</strong> {selectedMeasureCard.baseline_rate ?? "N/A"}%</p>
            <p><strong>After Rate:</strong> {selectedMeasureCard.projected_rate ?? "N/A"}%</p>
            <p><strong>Before Star:</strong> {selectedMeasureCard.baseline_star ?? "N/A"}</p>
            <p><strong>After Star:</strong> {selectedMeasureCard.projected_star ?? "N/A"}</p>
            <p><strong>Selected Closures:</strong> {selectedGapIds.length}</p>
          </div>
        ) : null}

        {compareError ? <p className="error">{compareError}</p> : null}
        {compareResult ? (
          <div className="compare-results-row">
            <StarResult title="Before" result={compareResult.before} />
            <StarResult title="After" result={compareResult.after} />
          </div>
        ) : null}
        {compareResult ? <p><strong>Star Changed:</strong> {String(compareResult.star_rating_changed)}</p> : null}
      </section>
      </main>
    </HedisSidebarLayout>
  );
}
