import { useMemo, useState } from "react"

import { useCalculationWorkflow } from "../hooks/useCalculationWorkflow"
import { formatDateTime, formatInteger, formatRate } from "../utils/format"

type OutcomeFilter = "" | "gap" | "met" | "excluded" | "not in population"

const OUTCOME_FILTERS: Array<{ value: OutcomeFilter; label: string }> = [
  { value: "", label: "All" },
  { value: "gap", label: "Gap" },
  { value: "met", label: "Met" },
  { value: "excluded", label: "Excluded" },
  { value: "not in population", label: "Not in population" },
]

const normalizeOutcome = (value: string): string => value.trim().toLowerCase()

const toOutcomeLabel = (value: string): string => {
  const normalized = normalizeOutcome(value)
  if (normalized.length === 0) {
    return "-"
  }
  return normalized.replace(/_/g, " ").replace(/\b\w/g, (part) => part.toUpperCase())
}

const toOutcomeClass = (value: string): string => {
  const normalized = normalizeOutcome(value)
  if (normalized === "gap") {
    return "status status-warn"
  }
  if (normalized === "met") {
    return "status status-ok"
  }
  if (normalized === "excluded") {
    return "status"
  }
  return "muted"
}

export function RunCalculationPage() {
  const {
    capabilities,
    loadingSetup,
    setupError,
    loadingOverview,
    loadingPatients,
    runningCalculation,
    overview,
    selectedMeasure,
    selectedMeasureId,
    selectedMeasureIdsForRun,
    patients,
    patientError,
    statusMessage,
    statusError,
    retrySetup,
    selectMeasure,
    toggleMeasureForRun,
    selectAllMeasuresForRun,
    clearMeasuresForRun,
    refresh,
    run,
  } = useCalculationWorkflow()

  const [filter, setFilter] = useState<OutcomeFilter>("")
  const [search, setSearch] = useState<string>("")

  const filteredPatients = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase()
    return patients.filter((row) => {
      const rowOutcome = normalizeOutcome(row.outcome)
      const matchesOutcome = !filter || rowOutcome === filter
      const matchesSearch = normalizedSearch.length === 0 || row.patientId.toLowerCase().startsWith(normalizedSearch)
      return matchesOutcome && matchesSearch
    })
  }, [filter, patients, search])

  const gapWorklistRows = useMemo(
    () => filteredPatients.filter((row) => normalizeOutcome(row.outcome) === "gap").slice(0, 50),
    [filteredPatients],
  )

  if (loadingSetup) {
    return (
      <section id="view-calculation" className="view">
        <article className="card state-card">
          <h2>Run Gap Calculation</h2>
          <p className="card-message">Loading calculation capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-calculation" className="view">
      <div className="page-head">
        <div>
          <h1>Run Gap Calculation</h1>
          <p>Run calculation, review outcomes by measure, and inspect patient-level gap results.</p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={loadingOverview || loadingPatients}
            onClick={() => {
              void refresh()
            }}
          >
            {loadingOverview || loadingPatients ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Calculation API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check calculation endpoints
            </button>
          </div>
        </article>
      ) : null}

      <article className="card">
        <div className="card-head row-split">
          <h2>Engine run</h2>
          <span className="chip info">{capabilities?.mode === "integration" ? "Integration mode" : "Star mode"}</span>
        </div>

        <p className="note">
          Latest stored run: {overview?.latestRunLabel ?? "-"}
          {overview?.measurementYear ? ` | Measurement year ${overview.measurementYear}` : ""}
        </p>

        {capabilities?.supportsSelectedMeasures && overview ? (
          <>
            <div className="calc-checks">
              {overview.measures.map((measure) => (
                <label key={measure.measureId} className="calc-check" title={measure.measureName}>
                  <input
                    type="checkbox"
                    checked={selectedMeasureIdsForRun.includes(measure.measureId)}
                    onChange={() => {
                      toggleMeasureForRun(measure.measureId)
                    }}
                  />
                  <span>{measure.measureId}</span>
                </label>
              ))}
            </div>
            <div className="actions">
              <button
                type="button"
                className="btn outline calc-small"
                onClick={selectAllMeasuresForRun}
              >
                Select all
              </button>
              <button
                type="button"
                className="btn outline calc-small"
                onClick={clearMeasuresForRun}
              >
                Clear
              </button>
            </div>
          </>
        ) : (
          <p className="note">This backend profile runs calculation across all available measures.</p>
        )}

        <div className="actions">
          <button
            type="button"
            className="btn primary"
            disabled={runningCalculation || !capabilities}
            onClick={() => {
              void run()
            }}
          >
            {runningCalculation ? "Running..." : "Run calculation"}
          </button>
        </div>

        {statusMessage ? <p className={`card-message ${statusError ? "error" : ""}`}>{statusMessage}</p> : null}
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Calculation summary</h2>
          <span className="note">{overview?.measurementYear ? `Measurement year ${overview.measurementYear}` : ""}</span>
        </div>

        <div className="kpi-grid four">
          <article className="kpi-card">
            <span>Measures</span>
            <strong>{formatInteger(overview?.measures.length ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Eligible checks</span>
            <strong>{formatInteger(overview?.measures.reduce((sum, item) => sum + Math.max(0, item.inMeasure - item.excluded), 0) ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Numerator met</span>
            <strong>{formatInteger(overview?.measures.reduce((sum, item) => sum + item.met, 0) ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Open gaps</span>
            <strong>{formatInteger(overview?.measures.reduce((sum, item) => sum + item.gapsOpen, 0) ?? 0)}</strong>
          </article>
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Measure outcome table</h2>
          <span className="note">Select a row to see patient outcomes below</span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Measure</th>
                <th>Type</th>
                <th>In measure</th>
                <th>Met</th>
                <th>Excluded</th>
                <th>Not met</th>
                <th>Rate</th>
                <th>Gaps open</th>
                <th>Closed by engine</th>
                <th>Confirmed by reviewer</th>
              </tr>
            </thead>
            <tbody>
              {(overview?.measures ?? []).length > 0 ? (
                overview?.measures.map((measure) => (
                  <tr
                    key={measure.measureId}
                    className={measure.measureId === selectedMeasureId ? "history-row-active" : ""}
                    onClick={() => {
                      void selectMeasure(measure.measureId)
                    }}
                  >
                    <td>
                      <div>{measure.measureName}</div>
                      <div className="note">{measure.measureId}</div>
                    </td>
                    <td>{measure.measureType}</td>
                    <td>{formatInteger(measure.inMeasure)}</td>
                    <td>{formatInteger(measure.met)}</td>
                    <td>{formatInteger(measure.excluded)}</td>
                    <td>{formatInteger(measure.notMet)}</td>
                    <td>{formatRate(measure.rate)}</td>
                    <td>{formatInteger(measure.gapsOpen)}</td>
                    <td>{formatInteger(measure.closedByEngine)}</td>
                    <td>{formatInteger(measure.confirmedByReviewer)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={10} className="empty">No measure results available yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>
            Patient-level outcomes
            {selectedMeasure ? `: ${selectedMeasure.measureName} (${selectedMeasure.measureId})` : ""}
          </h2>
        </div>

        {patientError ? <p className="card-message error">Could not load patient results: {patientError}</p> : null}

        <div className="calc-toolbar">
          <div className="pills">
            {OUTCOME_FILTERS.map((item) => {
              const count = item.value
                ? patients.filter((row) => normalizeOutcome(row.outcome) === item.value).length
                : patients.length
              return (
                <button
                  key={item.value || "all"}
                  type="button"
                  className={`pill ${filter === item.value ? "active" : ""}`}
                  onClick={() => {
                    setFilter(item.value)
                  }}
                >
                  {item.label} ({formatInteger(count)})
                </button>
              )
            })}
          </div>
          <input
            type="search"
            className="text-input calc-search"
            value={search}
            placeholder="Start of patient id"
            onChange={(event) => {
              setSearch(event.target.value)
            }}
          />
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>In denominator</th>
                <th>Met</th>
                <th>Excluded</th>
                <th>Outcome</th>
                <th>Gap status</th>
                <th>Review status</th>
                <th>Computed at</th>
              </tr>
            </thead>
            <tbody>
              {loadingPatients ? (
                <tr>
                  <td colSpan={8} className="empty">Loading patient results...</td>
                </tr>
              ) : filteredPatients.length > 0 ? (
                filteredPatients.map((row) => (
                  <tr key={`${row.patientId}-${row.gapId ?? "none"}`}>
                    <td>{row.patientId}</td>
                    <td>{row.inDenominator ? "Yes" : "No"}</td>
                    <td>{row.met ? "Yes" : "No"}</td>
                    <td>{row.excluded ? "Yes" : "No"}</td>
                    <td><span className={toOutcomeClass(row.outcome)}>{toOutcomeLabel(row.outcome)}</span></td>
                    <td>{toOutcomeLabel(row.gapStatus)}</td>
                    <td>{toOutcomeLabel(row.reviewStatus)}</td>
                    <td>{formatDateTime(row.computedAt)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8} className="empty">No patients match the selected filters.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Gap and blocker worklist</h2>
          <span className="note">
            {selectedMeasure
              ? `${selectedMeasure.measureId}: showing ${formatInteger(gapWorklistRows.length)} of ${formatInteger(filteredPatients.filter((row) => normalizeOutcome(row.outcome) === "gap").length)} gaps`
              : ""}
          </span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>What is missing</th>
                <th>Gap status</th>
                <th>Review status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {gapWorklistRows.length > 0 ? (
                gapWorklistRows.map((row) => (
                  <tr key={`work-${row.patientId}-${row.gapId ?? "none"}`}>
                    <td>{row.patientId}</td>
                    <td>{row.missingElement ?? "-"}</td>
                    <td>{toOutcomeLabel(row.gapStatus)}</td>
                    <td>{toOutcomeLabel(row.reviewStatus)}</td>
                    <td>
                      {row.gapId ? (
                        <div className="calc-actions">
                          <a className="text-link" href="#view-evidence">Open in Evidence Review</a>
                          <a className="text-link" href="#view-prioritization">Prioritization</a>
                        </div>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="empty">No gaps for the selected filters.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  )
}
