import { useEffect, useMemo, useState } from "react"

import { QualityRunResults } from "../components/quality/QualityRunResults"
import { humanize, shortPatient, toRunStatusClass } from "../components/quality/qualityDisplay"
import { useQualityWorkflow } from "../hooks/useQualityWorkflow"
import { formatDateTime, formatInteger, formatRate } from "../utils/format"

const ALL_MEASURES = "All"

const formatDateOnly = (value: string | null | undefined): string => (value ? String(value).slice(0, 10) : "-")

const toCoverageNote = (gapLimitDays: number | null | undefined): string => {
  const limit = typeof gapLimitDays === "number" && Number.isFinite(gapLimitDays) ? gapLimitDays : 45
  return `Enrollment gaps per member (gaps over ${limit} days break continuous enrollment).`
}

export function DataQualityPage() {
  const {
    capabilities,
    overview,
    runs,
    runDetail,
    coverage,
    coverageFilters,
    loadingSetup,
    loadingRunDetail,
    loadingCoverage,
    runningChecks,
    setupError,
    errorMessage,
    statusMessage,
    selectedBatchId,
    selectedRunId,
    selectedMeasureId,
    selectedSeverity,
    setSelectedBatchId,
    setSelectedRunId,
    setSelectedMeasureId,
    setSelectedSeverity,
    setCoverageFilter,
    retrySetup,
    runChecksNow,
    refreshCoverage,
  } = useQualityWorkflow()

  const [coverageOpen, setCoverageOpen] = useState<boolean>(false)

  const selectedRun = useMemo(
    () => runs.find((run) => String(run.run_id) === selectedRunId) ?? null,
    [runs, selectedRunId],
  )

  const selectedMeasureMeta = useMemo(
    () => (overview?.measures ?? []).find((measure) => measure.measure_id === selectedMeasureId) ?? null,
    [overview?.measures, selectedMeasureId],
  )

  const runScopeMeasure = selectedRun?.measure_id ?? null
  const runScopeWarning = useMemo(() => {
    if (!runScopeMeasure) {
      return ""
    }
    if (selectedMeasureId === ALL_MEASURES) {
      return `This run was executed for measure ${runScopeMeasure} only. To see every measure, run checks with All measures selected.`
    }
    if (selectedMeasureId !== runScopeMeasure) {
      return `This run covers measure ${runScopeMeasure}. With ${selectedMeasureId} selected, results can be empty or partial.`
    }
    return ""
  }, [runScopeMeasure, selectedMeasureId])

  const runFeedReport = useMemo(() => {
    const summary = selectedRun?.summary
    if (!summary || typeof summary !== "object") {
      return null
    }
    const record = summary as Record<string, unknown>
    return typeof record.feed_report === "string" ? record.feed_report : null
  }, [selectedRun?.summary])

  const runIsRunning = selectedRun ? selectedRun.status.trim().toLowerCase() === "running" : false
  const runIsFailed = selectedRun ? selectedRun.status.trim().toLowerCase() === "failed" : false

  const filteredChecks = useMemo(() => {
    const checks = runDetail?.checks ?? []
    if (selectedMeasureId === ALL_MEASURES) {
      return checks
    }
    return checks.filter((item) => item.measure_id === selectedMeasureId)
  }, [runDetail?.checks, selectedMeasureId])

  const filteredAnomalies = useMemo(() => {
    const anomalies = runDetail?.anomalies ?? []
    if (selectedMeasureId === ALL_MEASURES) {
      return anomalies
    }
    return anomalies.filter((item) => item.measure_id === selectedMeasureId)
  }, [runDetail?.anomalies, selectedMeasureId])

  const runContext = useMemo(() => {
    if (!selectedRun) {
      return ""
    }
    return `Batch ${String(selectedRun.batch_id ?? selectedBatchId).slice(0, 8)} | run #${selectedRun.run_id} | ${humanize(selectedRun.status)} | scope ${runScopeMeasure ?? "all measures"}${runFeedReport ? ` | Feed report: ${runFeedReport}` : ""}`
  }, [runFeedReport, runScopeMeasure, selectedBatchId, selectedRun])

  const runStatusNote = useMemo(() => {
    if (!selectedRun) {
      return ""
    }
    return `Started ${formatDateTime(selectedRun.started_at)} | Completed ${formatDateTime(selectedRun.completed_at)}`
  }, [selectedRun])

  const hasBatches = (overview?.batches ?? []).length > 0
  const automationMeasures = overview?.measures ?? []
  const automationSummaryReason = overview?.not_available?.[0]?.reason ?? ""
  const automationOpenGaps = useMemo(
    () => automationMeasures.reduce((sum, item) => sum + (item.open_gaps ?? 0), 0),
    [automationMeasures],
  )
  const automationProjectedDelta = useMemo(
    () => automationMeasures.reduce((sum, item) => {
      const baseline = item.baseline_closed ?? 0
      const projected = item.projected_closed ?? baseline
      return sum + (projected - baseline)
    }, 0),
    [automationMeasures],
  )

  useEffect(() => {
    if (!coverageOpen || capabilities?.mode !== "integration") {
      return
    }
    void refreshCoverage()
  }, [
    capabilities?.mode,
    coverageFilters.compliance,
    coverageFilters.endDate,
    coverageFilters.payer,
    coverageFilters.startDate,
    coverageOpen,
    refreshCoverage,
  ])

  if (loadingSetup) {
    return (
      <section id="view-quality" className="view">
        <article className="card state-card">
          <h2>Data Quality</h2>
          <p className="card-message">Loading quality capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-quality" className="view">
      <div className="page-head">
        <div>
          <h1>Data Quality</h1>
          <p>Review checks, anomalies, and enrollment coverage after each ingestion run.</p>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Data quality API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check quality endpoints
            </button>
          </div>
        </article>
      ) : null}

      {overview?.not_available?.length && capabilities?.mode !== "automation" ? (
        <article className="card">
          <div className="card-head">
            <h2>Not available on the one schema</h2>
          </div>
          <div className="integration-capabilities">
            {overview.not_available.map((item, index) => (
              <p key={`${item.name ?? "na"}-${index}`} className="note">
                <strong>{item.name ?? "Not available"}:</strong> {item.reason ?? "No details"}
              </p>
            ))}
          </div>
        </article>
      ) : null}

      {capabilities?.mode === "integration" ? (
        <>
          <article className="card">
            <div className="card-head row-split">
              <h2>Data quality and anomaly detection</h2>
              {selectedRun ? <span className={toRunStatusClass(selectedRun.status)}>{humanize(selectedRun.status)}</span> : null}
            </div>

            <div className="calc-toolbar quality-filters">
              <label className="calc-check">
                <span>Batch</span>
                <select
                  className="text-input"
                  value={selectedBatchId}
                  onChange={(event) => {
                    setSelectedBatchId(event.target.value)
                  }}
                >
                  {(overview?.batches ?? []).map((batch) => {
                    const runCount = typeof batch.runs === "number" ? batch.runs : 0
                    return (
                      <option key={batch.batch_id} value={batch.batch_id}>
                        {batch.batch_id.slice(0, 8)} | {humanize(batch.status ?? "unknown")} | {formatDateTime(batch.started_at)} ({runCount} run{runCount === 1 ? "" : "s"})
                      </option>
                    )
                  })}
                </select>
              </label>

              <label className="calc-check">
                <span>Measure</span>
                <select
                  className="text-input"
                  value={selectedMeasureId}
                  onChange={(event) => {
                    setSelectedMeasureId(event.target.value)
                  }}
                >
                  <option value={ALL_MEASURES}>All measures</option>
                  {(overview?.measures ?? []).map((measure) => (
                    <option key={measure.measure_id} value={measure.measure_id}>
                      {measure.measure_id}
                    </option>
                  ))}
                </select>
              </label>

              <label className="calc-check">
                <span>Run</span>
                <select
                  className="text-input"
                  value={selectedRunId}
                  onChange={(event) => {
                    setSelectedRunId(event.target.value)
                  }}
                >
                  {runs.map((run) => (
                    <option key={run.run_id} value={String(run.run_id)}>
                      #{run.run_id} | {humanize(run.status)} | {run.measure_id ?? "All measures"}
                    </option>
                  ))}
                </select>
              </label>

              <label className="calc-check">
                <span>Severity</span>
                <select
                  className="text-input"
                  value={selectedSeverity}
                  onChange={(event) => {
                    setSelectedSeverity(event.target.value)
                  }}
                >
                  <option value="All">All</option>
                  <option value="Critical">Critical</option>
                  <option value="High">High</option>
                  <option value="Medium">Medium</option>
                  <option value="Low">Low</option>
                </select>
              </label>
            </div>

            {selectedMeasureMeta?.description ? (
              <p className="note small">
                {selectedMeasureMeta.measure_id}: {selectedMeasureMeta.description}
              </p>
            ) : null}

            {runScopeWarning ? <p className="note small">{runScopeWarning}</p> : null}

            <details className="dq-advanced">
              <summary>Advanced rerun</summary>
              <div className="dq-adv-body">
                <p className="note small">
                  Checks run automatically after batch loading. Run checks now is mainly for rule updates or manual reruns.
                </p>
                <div className="actions">
                  <button
                    type="button"
                    className="btn primary"
                    disabled={runningChecks || !selectedBatchId}
                    onClick={() => {
                      void runChecksNow()
                    }}
                  >
                    {runningChecks ? "Running..." : "Run checks now"}
                  </button>
                </div>
              </div>
            </details>

            {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
            {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}

            {!hasBatches ? (
              <p className="empty">
                No batch has been loaded yet. Upload files on <a href="#ingestion">Data Ingestion</a>; checks run automatically after loading.
              </p>
            ) : !selectedRun ? (
              <p className="empty">
                No checks have run on this batch yet. They run automatically after loading; use Advanced rerun only when needed.
              </p>
            ) : runIsFailed ? (
              <p className="card-message error">{selectedRun.error ?? "The selected checks run failed."}</p>
            ) : (
              <>
                {runIsRunning ? <p className="note small">Checks are running. Use Run checks now to update results after completion.</p> : null}
                <p className="note small">{runContext}</p>
                <p className="note small">{runStatusNote}</p>

                <QualityRunResults
                  compact={false}
                  checks={filteredChecks}
                  anomalies={filteredAnomalies}
                  loading={loadingRunDetail}
                />
              </>
            )}
          </article>

          <details
            className="card dq-coverage"
            onToggle={(event) => {
              setCoverageOpen(event.currentTarget.open)
            }}
          >
            <summary>
              Coverage report <span className="muted small">{toCoverageNote(coverage?.summary?.gap_limit_days)}</span>
            </summary>
            <div className="dq-cov-body">
              <div className="calc-toolbar quality-filters">
                <label className="calc-check">
                  <span>Payer</span>
                  <select
                    className="text-input"
                    value={coverageFilters.payer}
                    onChange={(event) => {
                      setCoverageFilter("payer", event.target.value)
                    }}
                  >
                    <option value="All">All</option>
                    {(coverage?.payers ?? []).map((payer) => (
                      <option key={payer} value={payer}>{payer}</option>
                    ))}
                  </select>
                </label>

                <label className="calc-check">
                  <span>Compliance</span>
                  <select
                    className="text-input"
                    value={coverageFilters.compliance}
                    onChange={(event) => {
                      setCoverageFilter("compliance", event.target.value)
                    }}
                  >
                    <option value="All">All</option>
                    <option value="Compliant">Compliant</option>
                    <option value="Violation">Violation</option>
                  </select>
                </label>

                <label className="calc-check">
                  <span>Start date</span>
                  <input
                    className="text-input"
                    type="date"
                    value={coverageFilters.startDate}
                    onChange={(event) => {
                      setCoverageFilter("startDate", event.target.value)
                    }}
                  />
                </label>

                <label className="calc-check">
                  <span>End date</span>
                  <input
                    className="text-input"
                    type="date"
                    value={coverageFilters.endDate}
                    onChange={(event) => {
                      setCoverageFilter("endDate", event.target.value)
                    }}
                  />
                </label>
              </div>

              {loadingCoverage ? <p className="card-message">Loading coverage report...</p> : null}

              {coverage ? (
                <>
                  <div className="kpi-grid four">
                    <article className="kpi-card">
                      <span>Total covered</span>
                      <strong>{formatInteger(coverage.summary.total_covered)}</strong>
                    </article>
                    <article className="kpi-card">
                      <span>Continuous enrollment</span>
                      <strong>{formatInteger(coverage.summary.continuous_enrollment)}</strong>
                    </article>
                    <article className="kpi-card">
                      <span>HEDIS violations</span>
                      <strong>{formatInteger(coverage.summary.hedis_violations)}</strong>
                    </article>
                    <article className="kpi-card">
                      <span>Compliance rate</span>
                      <strong>{formatRate(coverage.summary.compliance_rate)}</strong>
                    </article>
                  </div>

                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Patient</th>
                          <th>Payer</th>
                          <th>Period</th>
                          <th className="r">Gap days</th>
                          <th>Compliant</th>
                        </tr>
                      </thead>
                      <tbody>
                        {coverage.coverage_records.length > 0 ? (
                          coverage.coverage_records.slice(0, 100).map((row) => (
                            <tr key={`${row.patient_id}-${row.coverage_id}-${row.period_start ?? "na"}`}>
                              <td><span className="pt">{shortPatient(row.patient_id)}</span></td>
                              <td>{row.payer ?? "-"}</td>
                              <td>{formatDateOnly(row.period_start)} to {formatDateOnly(row.period_end)}</td>
                              <td className="r">{formatInteger(row.gap_days)}</td>
                              <td>
                                <span className={row.continuous_enrollment_compliant ? "status status-ok" : "status status-bad"}>
                                  {row.continuous_enrollment_compliant ? "Compliant" : "Violation"}
                                </span>
                              </td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td colSpan={5} className="empty">No coverage records found. Load a batch on Data Ingestion first.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>

                  {coverage.coverage_records.length > 100 ? (
                    <p className="note small">
                      Showing 100 of {formatInteger(coverage.coverage_records.length)} records.
                    </p>
                  ) : null}
                </>
              ) : coverageOpen && !loadingCoverage ? (
                <p className="empty">No coverage records found. Load a batch on Data Ingestion first.</p>
              ) : null}
            </div>
          </details>
        </>
      ) : null}

      {capabilities?.mode === "automation" ? (
        <article className="card">
          <div className="card-head row-split">
            <h2>Data quality and anomaly detection</h2>
            <span className="chip warn">Compatibility</span>
          </div>
          <p className="note">
            This backend profile does not expose detailed /api/quality checks. The section below uses available
            automation and dashboard endpoints so quality snapshot data is still visible.
          </p>
          {automationSummaryReason ? <p className="note small">{automationSummaryReason}</p> : null}
          <div className="actions">
            <button
              type="button"
              className="btn primary"
              disabled={runningChecks}
              onClick={() => {
                void runChecksNow()
              }}
            >
              {runningChecks ? "Running..." : "Run checks now"}
            </button>
          </div>

          {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
          {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}

          <div className="kpi-grid four">
            <article className="kpi-card">
              <span>Measures in snapshot</span>
              <strong>{formatInteger(automationMeasures.length)}</strong>
            </article>
            <article className="kpi-card">
              <span>Open gaps</span>
              <strong>{formatInteger(automationOpenGaps)}</strong>
            </article>
            <article className="kpi-card">
              <span>Projected closures</span>
              <strong>{formatInteger(automationProjectedDelta)}</strong>
            </article>
            <article className="kpi-card">
              <span>Batches tracked</span>
              <strong>{formatInteger((overview?.batches ?? []).length)}</strong>
            </article>
          </div>

          <div className="card-head row-split">
            <h2>Quality snapshot by measure</h2>
            <span className="note">Derived from star dashboard endpoint</span>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Measure</th>
                  <th className="r">Open gaps</th>
                  <th className="r">Baseline closed</th>
                  <th className="r">Projected closed</th>
                  <th className="r">Baseline rate</th>
                  <th className="r">Projected rate</th>
                  <th className="r">Stars</th>
                </tr>
              </thead>
              <tbody>
                {automationMeasures.length > 0 ? (
                  automationMeasures.map((measure) => (
                    <tr key={measure.measure_id}>
                      <td>
                        <div>{measure.measure_id}</div>
                        <div className="note">{measure.description ?? "-"}</div>
                      </td>
                      <td className="r">{formatInteger(measure.open_gaps)}</td>
                      <td className="r">{formatInteger(measure.baseline_closed)}</td>
                      <td className="r">{formatInteger(measure.projected_closed)}</td>
                      <td className="r">{formatRate(measure.baseline_rate)}</td>
                      <td className="r">{formatRate(measure.projected_rate)}</td>
                      <td className="r">{measure.baseline_star ?? "-"}{" to "}{measure.projected_star ?? "-"}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="empty">No snapshot data available yet. Run checks now to refresh automation quality summary.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="card-head row-split">
            <h2>Automation batch status</h2>
            <span className="note">From flow and ingestion jobs</span>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Batch</th>
                  <th>Status</th>
                  <th>Step</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {(overview?.batches ?? []).length > 0 ? (
                  (overview?.batches ?? []).map((batch) => (
                    <tr key={batch.batch_id}>
                      <td>{batch.batch_id}</td>
                      <td>{batch.status ?? "unknown"}</td>
                      <td>{batch.step ?? "-"}</td>
                      <td>{formatDateTime(batch.started_at)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="empty">No batch status is available yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </article>
      ) : null}

      {capabilities?.mode === "unavailable" ? (
        <article className="card danger-box">
          <p>
            Data quality endpoints are not available in the current backend profile. Switch to the integration API
            profile to use full quality checks.
          </p>
        </article>
      ) : null}
    </section>
  )
}
