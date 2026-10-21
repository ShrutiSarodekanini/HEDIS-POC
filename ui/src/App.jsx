import { useEffect, useMemo, useRef, useState } from "react";

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || "Request failed");
  }
  return data;
}

function fmtStar(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return Number(value).toFixed(2);
}

function fmtRate(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(2)}%`;
}

function fmtThreshold(value) {
  if (value == null || Number.isNaN(Number(value))) return "N/A";
  return `${Number(value).toFixed(2)}%`;
}

function deltaClass(delta) {
  if (delta == null) return "neutral";
  if (delta > 0) return "up";
  if (delta < 0) return "down";
  return "neutral";
}

function deltaText(delta) {
  if (delta == null || Number.isNaN(Number(delta))) return "N/A";
  const num = Number(delta);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}`;
}

function MetricCard({ title, baseline, projected, delta }) {
  return (
    <article className="metric-card">
      <p>{title}</p>
      <h3>
        {fmtStar(baseline)}
        <span className="arrow">&rarr;</span>
        {fmtStar(projected)}
      </h3>
      <strong className={`delta ${deltaClass(delta)}`}>Delta {deltaText(delta)}</strong>
    </article>
  );
}

export default function App() {
  const bootstrapped = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dashboard, setDashboard] = useState(null);
  const [selectedMeasureId, setSelectedMeasureId] = useState("");
  const [selectedGapIds, setSelectedGapIds] = useState([]);
  const [simulateLoading, setSimulateLoading] = useState(false);
  const [seedSummary, setSeedSummary] = useState(null);

  async function seedSampleData() {
    const summary = await fetchJson("/api/star-dashboard/seed-sample-data", {
      method: "POST",
    });
    setSeedSummary(summary);
  }

  async function loadDashboard(measureId, gapIds) {
    const payload = {
      measure_id: measureId || null,
      selected_gap_ids: gapIds || [],
    };
    return fetchJson("/api/star-dashboard/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  useEffect(() => {
    async function bootstrap() {
      if (bootstrapped.current) return;
      bootstrapped.current = true;
      setLoading(true);
      setError("");
      try {
        await seedSampleData();
        const data = await loadDashboard(null, []);
        setDashboard(data);
        if (data.measure_cards?.length) {
          const first = data.measure_cards[0].measure_id;
          setSelectedMeasureId(first);
          const focused = await loadDashboard(first, []);
          setDashboard(focused);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    bootstrap();
  }, []);

  const selectedMeasure = useMemo(() => {
    if (!dashboard || !selectedMeasureId) return null;
    return dashboard.measure_cards.find((m) => m.measure_id === selectedMeasureId) || null;
  }, [dashboard, selectedMeasureId]);

  const selectedMeasureGaps = useMemo(() => {
    if (!dashboard?.selected_measure_gaps) return [];
    const sorted = [...dashboard.selected_measure_gaps].sort((a, b) => {
      if (a.is_closed !== b.is_closed) return a.is_closed ? 1 : -1;
      return String(a.care_gap_id).localeCompare(String(b.care_gap_id));
    });
    return sorted.slice(0, 160);
  }, [dashboard]);

  const patientRows = useMemo(() => dashboard?.patient_rows || [], [dashboard]);

  async function selectMeasure(measureId) {
    setSelectedMeasureId(measureId);
    setSelectedGapIds([]);
    setSimulateLoading(true);
    setError("");
    try {
      const data = await loadDashboard(measureId, []);
      setDashboard(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setSimulateLoading(false);
    }
  }

  function toggleGap(gapId) {
    setSelectedGapIds((current) =>
      current.includes(gapId)
        ? current.filter((id) => id !== gapId)
        : [...current, gapId]
    );
  }

  async function runProjection() {
    if (!selectedMeasureId) return;
    setSimulateLoading(true);
    setError("");
    try {
      const data = await loadDashboard(selectedMeasureId, selectedGapIds);
      setDashboard(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setSimulateLoading(false);
    }
  }

  async function resetProjection() {
    if (!selectedMeasureId) return;
    setSelectedGapIds([]);
    setSimulateLoading(true);
    setError("");
    try {
      const data = await loadDashboard(selectedMeasureId, []);
      setDashboard(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setSimulateLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="app-shell">
        <div className="loading-screen">Preparing realistic dashboard data...</div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <h1>Population Star Analytics</h1>
          <p>
            Real-time simulation of star movement from care-gap closure using current patient data.
          </p>
        </div>
        <div className="hero-chip">Database-driven projection</div>
      </header>

      {seedSummary && (
        <div className="seed-banner">
          Sample data ready: +{seedSummary.inserted_members} members, +{seedSummary.inserted_measures} measures,
          +{seedSummary.inserted_care_gaps} care gaps.
        </div>
      )}

      {error && <p className="error-box">{error}</p>}

      {dashboard && (
        <>
          <section className="overall-grid">
            <MetricCard
              title="Overall Star (Measure Average)"
              baseline={dashboard.overall?.baseline_star}
              projected={dashboard.overall?.projected_star}
              delta={dashboard.overall?.star_delta}
            />
            <article className="metric-card small">
              <p>Rated Measures</p>
              <h3>{dashboard.overall?.rated_measure_count ?? 0}</h3>
              <strong className="delta neutral">Based on available measure data</strong>
            </article>
            <article className="metric-card small">
              <p>Selected Gaps for Projection</p>
              <h3>{selectedGapIds.length}</h3>
              <strong className="delta neutral">Applies to focused measure only</strong>
            </article>
          </section>

          <section className="board">
            <div className="panel measures">
              <div className="panel-head">
                <h2>Measure Performance and Star Shift</h2>
                <button onClick={() => selectMeasure(selectedMeasureId)} disabled={simulateLoading}>
                  Refresh
                </button>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Measure</th>
                      <th>Star Eligibility</th>
                      <th>Gaps (Closed/Total)</th>
                      <th>Rate</th>
                      <th>Star</th>
                      <th>Star Delta</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dashboard.measure_cards.map((row) => (
                      <tr
                        key={row.measure_id}
                        className={row.measure_id === selectedMeasureId ? "active-row" : ""}
                        onClick={() => selectMeasure(row.measure_id)}
                      >
                        <td>
                          <strong>{row.measure_id}</strong>
                          <small>{row.measure_name}</small>
                        </td>
                        <td>
                          <strong>{row.star_eligible ? "Mapped" : "Not Mapped"}</strong>
                          <small>{row.rating_note}</small>
                        </td>
                        <td>
                          {row.baseline_closed}/{row.total_gaps}
                          <span className="projected"> &rarr; {row.projected_closed}/{row.total_gaps}</span>
                        </td>
                        <td>
                          {fmtRate(row.baseline_rate)}
                          <span className="projected"> &rarr; {fmtRate(row.projected_rate)}</span>
                        </td>
                        <td>
                          {fmtStar(row.baseline_star)}
                          <span className="projected"> &rarr; {fmtStar(row.projected_star)}</span>
                        </td>
                        <td className={`delta ${deltaClass(row.star_delta)}`}>{deltaText(row.star_delta)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="panel simulation">
              <div className="panel-head">
                <h2>Gap Closure Simulator</h2>
                <span className="selected-tag">{selectedMeasure ? selectedMeasure.measure_id : "No measure"}</span>
              </div>
              {selectedMeasure ? (
                <>
                  <p className="subtext">
                    Select open gaps to project star movement. Small changes like 2.10 &rarr; 2.11 are shown with two decimals.
                  </p>
                  <div className="sim-summary">
                    <div>
                      <span>Current Star</span>
                      <strong>{fmtStar(selectedMeasure.baseline_star)}</strong>
                    </div>
                    <div>
                      <span>Projected Star</span>
                      <strong>{fmtStar(selectedMeasure.projected_star)}</strong>
                    </div>
                    <div>
                      <span>Delta</span>
                      <strong className={`delta ${deltaClass(selectedMeasure.star_delta)}`}>
                        {deltaText(selectedMeasure.star_delta)}
                      </strong>
                    </div>
                  </div>
                  <div className="gap-list">
                    {selectedMeasureGaps.map((gap) => (
                      <label key={gap.care_gap_id} className={gap.is_closed ? "gap-item closed" : "gap-item"}>
                        <input
                          type="checkbox"
                          disabled={gap.is_closed}
                          checked={selectedGapIds.includes(gap.care_gap_id)}
                          onChange={() => toggleGap(gap.care_gap_id)}
                        />
                        <div>
                          <strong>{gap.care_gap_id}</strong>
                          <small>
                            {gap.member_name} ({gap.member_id}) | {gap.status}
                          </small>
                        </div>
                      </label>
                    ))}
                  </div>
                  <div className="action-row">
                    <button onClick={runProjection} disabled={simulateLoading}>
                      {simulateLoading ? "Calculating..." : "Run Projection"}
                    </button>
                    <button className="ghost" onClick={resetProjection} disabled={simulateLoading}>
                      Reset
                    </button>
                  </div>
                </>
              ) : (
                <p className="subtext">Select a measure from the table to start simulation.</p>
              )}
            </div>
          </section>

          <section className="panel patients">
            <div className="panel-head">
              <h2>Patient-Level Star Impact</h2>
              <span>{patientRows.length} rows</span>
            </div>
            {dashboard.cms_reference && (
              <div className="cms-reference">
                <h3>
                  CMS {dashboard.cms_reference.cms_rating_year} Reference Used for Measure Calculations
                </h3>
                <p>{dashboard.cms_reference.note}</p>
                <p>
                  <strong>Mapped measures in current data:</strong>{" "}
                  {dashboard.cms_reference.mapped_measure_ids?.length
                    ? dashboard.cms_reference.mapped_measure_ids.join(", ")
                    : "None"}
                </p>
                <p>
                  <a href={dashboard.cms_reference.technical_notes_url} target="_blank" rel="noreferrer">
                    CMS 2027 Technical Notes
                  </a>
                  {" | "}
                  <a href={dashboard.cms_reference.measure_set_url} target="_blank" rel="noreferrer">
                    CMS 2027 Measures and Weights
                  </a>
                </p>
                {Array.isArray(dashboard.cms_reference.star_cut_points) &&
                  dashboard.cms_reference.star_cut_points.length > 0 && (
                    <>
                      <p>
                        <strong>What star for how many percentage?</strong> Values below the 2-star threshold are 1 star.
                      </p>
                      <div className="table-wrap cms-cutpoints">
                        <table>
                          <thead>
                            <tr>
                              <th>Measure</th>
                              <th>2 Star Min</th>
                              <th>3 Star Min</th>
                              <th>4 Star Min</th>
                              <th>5 Star Min</th>
                            </tr>
                          </thead>
                          <tbody>
                            {dashboard.cms_reference.star_cut_points.map((row) => (
                              <tr key={`cp-${row.measure_id}`}>
                                <td>
                                  <strong>{row.measure_id}</strong>
                                  <small>{row.measure_name}</small>
                                </td>
                                <td>{fmtThreshold(row.two_star_min)}</td>
                                <td>{fmtThreshold(row.three_star_min)}</td>
                                <td>{fmtThreshold(row.four_star_min)}</td>
                                <td>{fmtThreshold(row.five_star_min)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
              </div>
            )}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Patient</th>
                    <th>Measure</th>
                    <th>Eligibility</th>
                    <th>Rate</th>
                    <th>Star</th>
                    <th>Delta</th>
                  </tr>
                </thead>
                <tbody>
                  {patientRows.map((row) => (
                    <tr key={`${row.member_id}-${row.measure_id}`}>
                      <td>
                        <strong>{row.member_name}</strong>
                        <small>{row.member_id}</small>
                      </td>
                      <td>{row.measure_id}</td>
                      <td>{row.star_eligible ? "Mapped" : "Not Mapped"}</td>
                      <td>
                        {fmtRate(row.baseline_rate)}
                        <span className="projected"> &rarr; {fmtRate(row.projected_rate)}</span>
                      </td>
                      <td>
                        {fmtStar(row.baseline_star)}
                        <span className="projected"> &rarr; {fmtStar(row.projected_star)}</span>
                      </td>
                      <td className={`delta ${deltaClass(row.star_delta)}`}>{deltaText(row.star_delta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
