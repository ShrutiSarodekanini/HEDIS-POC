import { useEffect, useMemo, useState } from "react"

import { useQualityWorkflow } from "../hooks/useQualityWorkflow"
import { formatDateTime, formatInteger, formatRate } from "../utils/format"

const ALL_MEASURES = "All"
const SEVERITY_ORDER = ["critical", "high", "medium", "low"]
const MAX_ANOMALIES_PER_GROUP = 20
const MAX_CHECK_ROWS = 200

const ANOMALY_TYPE_INFO: Record<string, { title: string; description: string }> = {
  measure_evidence_missing: {
    title: "Measure evidence missing",
    description: "No records with the required codes were found for this measure.",
  },
  measure_low_patient_coverage: {
    title: "Low patient coverage for a measure",
    description: "Too few patients have evidence for this measure to trust its results.",
  },
  measure_observation_missing: {
    title: "Measure observations missing",
    description: "Observation codes required by this measure are absent from the batch.",
  },
  mapping_error: {
    title: "Mapping error",
    description: "An observation unit or value did not map as expected.",
  },
  mapping_error_rate: {
    title: "Mapping error rate above threshold",
    description: "Too many records failed validation during ingestion.",
  },
  coverage_gap_violation: {
    title: "Coverage gap over threshold",
    description: "Some members have enrollment gaps above the allowed limit.",
  },
  required_feed_missing: {
    title: "Required feed missing",
    description: "No records arrived for a resource type required by measures.",
  },
  feed_dropout: {
    title: "Feed dropped out",
    description: "A feed that normally has data sent none in this batch.",
  },
  volume_drop: {
    title: "Feed volume dropped",
    description: "This feed sent significantly fewer records than baseline.",
  },
  volume_spike: {
    title: "Feed volume spiked",
    description: "This feed sent significantly more records than baseline.",
  },
}

const FACT_LABELS: Record<string, string> = {
  measure_id: "Measure",
  code: "Code",
  reason: "Reason",
  resource_type: "Resource type",
  clinical_evidence_mode: "Evidence needed",
  max_patient_count: "Patients with evidence",
  min_distinct_patients_with_evidence: "Minimum patients needed",
  error_count: "Errors",
  warning_count: "Warnings",
  total_resources: "Total records",
  error_rate: "Error rate",
  violation_count: "Members over limit",
  threshold_gap_days: "Gap limit (days)",
  record_count: "Records",
  current_count: "Records now",
  baseline_avg_count: "Usual records",
  drop_pct: "Drop (%)",
  spike_pct: "Spike (%)",
  drop_threshold_pct: "Drop limit (%)",
  spike_threshold_pct: "Spike limit (%)",
  min_expected: "Lowest expected",
  max_expected: "Highest expected",
  actual_value: "Actual value",
  expected_unit: "Expected unit",
  actual_unit: "Actual unit",
  observation_codes: "Expected codes",
}

const REASON_LABELS: Record<string, string> = {
  missing_or_invalid_unit: "Unit missing or not expected",
  value_out_of_expected_range: "Value outside expected range",
}

const CODE_KEYS = new Set([
  "measure_id",
  "reason",
  "resource_type",
  "code",
  "expected_unit",
  "actual_unit",
  "clinical_evidence_mode",
])

const HIDDEN_KEYS = new Set(["patient_id", "observation_id", "check"])
const CODE_VALUE = /^[A-Za-z0-9_.:%/\[\]{}-]{1,40}$/

const humanize = (value: string): string => {
  const text = String(value ?? "")
  if (!text) {
    return "-"
  }
  const replaced = text.replaceAll("_", " ")
  return replaced.charAt(0).toUpperCase() + replaced.slice(1)
}

const shortPatient = (value: string | null | undefined): string => {
  if (!value) {
    return "pt-?"
  }
  return `pt-${String(value).slice(0, 4)}...`
}

const formatMetricValue = (value: number | null | undefined): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return value.toFixed(2)
}

const formatDateOnly = (value: string | null | undefined): string => (value ? String(value).slice(0, 10) : "-")

const toRunStatusClass = (value: string): string => {
  const normalized = value.trim().toLowerCase()
  if (normalized === "completed" || normalized === "passed") {
    return "status status-ok"
  }
  if (normalized === "failed") {
    return "status status-bad"
  }
  if (normalized === "running" || normalized === "not_available") {
    return "status status-warn"
  }
  return "status"
}

const toSeverityKey = (value: string | null | undefined): string => {
  const normalized = String(value ?? "").trim().toLowerCase()
  if (SEVERITY_ORDER.includes(normalized)) {
    return normalized
  }
  return "other"
}

const toSeverityRank = (value: string | null | undefined): number => {
  const normalized = toSeverityKey(value)
  const index = SEVERITY_ORDER.indexOf(normalized)
  return index >= 0 ? index : SEVERITY_ORDER.length
}

const toCoverageNote = (gapLimitDays: number | null | undefined): string => {
  const limit = typeof gapLimitDays === "number" && Number.isFinite(gapLimitDays) ? gapLimitDays : 45
  return `Enrollment gaps per member (gaps over ${limit} days break continuous enrollment).`
}

const toAnomalyFacts = (details: Record<string, unknown> | null | undefined): Array<{ label: string; value: string }> => {
  if (!details || typeof details !== "object") {
    return []
  }

  const facts: Array<{ label: string; value: string }> = []
  for (const [key, raw] of Object.entries(details)) {
    if (HIDDEN_KEYS.has(key) || raw == null) {
      continue
    }

    let shown: string | null = null
    if (typeof raw === "number") {
      shown = Number.isFinite(raw) ? String(Math.round(raw * 100) / 100) : null
    } else if (typeof raw === "boolean") {
      shown = raw ? "Yes" : "No"
    } else if (typeof raw === "string" && CODE_KEYS.has(key) && CODE_VALUE.test(raw)) {
      shown = key === "reason" ? (REASON_LABELS[raw] ?? humanize(raw)) : raw
    } else if (key === "observation_codes" && Array.isArray(raw) && raw.length > 0) {
      const safeCodes = raw.filter((item): item is string => typeof item === "string" && CODE_VALUE.test(item))
      if (safeCodes.length > 0) {
        const visible = safeCodes.slice(0, 6)
        shown = visible.join(", ") + (safeCodes.length > visible.length ? ` and ${safeCodes.length - visible.length} more` : "")
      }
    }

    if (shown != null) {
      facts.push({ label: FACT_LABELS[key] ?? humanize(key), value: shown })
    }
  }

  const evidenceChecks = details.evidence_checks
  if (Array.isArray(evidenceChecks)) {
    const missingSources = evidenceChecks
      .filter((entry) => {
        if (!entry || typeof entry !== "object") {
          return false
        }
        const typed = entry as Record<string, unknown>
        return typed.row_count === 0 && typeof typed.resource_type === "string" && CODE_VALUE.test(typed.resource_type)
      })
      .map((entry) => (entry as Record<string, unknown>).resource_type as string)

    facts.push({ label: "Missing sources", value: missingSources.length > 0 ? missingSources.join(", ") : "None" })
  }

  return facts
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
    loadingOverview,
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
    refreshOverview,
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

  const groupedAnomalies = useMemo(() => {
    const grouped = new Map<string, typeof filteredAnomalies>()
    for (const anomaly of filteredAnomalies) {
      const type = anomaly.check_type ?? anomaly.anomaly_type ?? "unknown"
      const current = grouped.get(type)
      if (current) {
        current.push(anomaly)
      } else {
        grouped.set(type, [anomaly])
      }
    }

    return Array.from(grouped.entries())
      .map(([type, items]) => ({
        type,
        items: [...items].sort((left, right) => toSeverityRank(left.severity) - toSeverityRank(right.severity)),
      }))
      .sort((left, right) => {
        const leftRank = toSeverityRank(left.items[0]?.severity)
        const rightRank = toSeverityRank(right.items[0]?.severity)
        if (leftRank !== rightRank) {
          return leftRank - rightRank
        }
        return right.items.length - left.items.length
      })
  }, [filteredAnomalies])

  const severityRows = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const anomaly of filteredAnomalies) {
      const key = toSeverityKey(anomaly.severity)
      counts[key] = (counts[key] ?? 0) + 1
    }

    const keys = [
      ...SEVERITY_ORDER.filter((key) => counts[key]),
      ...Object.keys(counts).filter((key) => !SEVERITY_ORDER.includes(key)).sort(),
    ]

    const total = filteredAnomalies.length
    return keys.map((key) => ({
      key,
      label: humanize(key),
      count: counts[key],
      ratio: total > 0 ? counts[key] / total : 0,
    }))
  }, [filteredAnomalies])

  const checksPassed = useMemo(
    () => filteredChecks.filter((item) => String(item.status).toLowerCase() === "passed").length,
    [filteredChecks],
  )

  const checksFailed = useMemo(
    () => filteredChecks.filter((item) => String(item.status).toLowerCase() === "failed").length,
    [filteredChecks],
  )

  const checksNotAvailable = useMemo(
    () => filteredChecks.filter((item) => String(item.status).toLowerCase() === "not_available").length,
    [filteredChecks],
  )

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
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={loadingOverview}
            onClick={() => {
              void refreshOverview()
            }}
          >
            {loadingOverview ? "Refreshing..." : "Refresh"}
          </button>
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
                {runIsRunning ? <p className="note small">Checks are running. This page auto-refreshes until the run completes.</p> : null}
                <p className="note small">{runContext}</p>
                <p className="note small">{runStatusNote}</p>

                <div className={`kpi-grid ${checksNotAvailable > 0 ? "five" : "four"}`}>
                  <article className="kpi-card">
                    <span>Checks run</span>
                    <strong>{formatInteger(filteredChecks.length)}</strong>
                  </article>
                  <article className="kpi-card">
                    <span>Passed</span>
                    <strong>{formatInteger(checksPassed)}</strong>
                  </article>
                  <article className="kpi-card">
                    <span>Failed</span>
                    <strong>{formatInteger(checksFailed)}</strong>
                  </article>
                  <article className="kpi-card">
                    <span>Anomalies raised</span>
                    <strong>{formatInteger(filteredAnomalies.length)}</strong>
                  </article>
                  {checksNotAvailable > 0 ? (
                    <article className="kpi-card">
                      <span>Not available</span>
                      <strong>{formatInteger(checksNotAvailable)}</strong>
                    </article>
                  ) : null}
                </div>

                <div className="stack">
                  <p className="label-strong">Severity breakdown</p>
                  {loadingRunDetail ? (
                    <p className="card-message">Loading run details...</p>
                  ) : severityRows.length > 0 ? (
                    <div className="dq-bars">
                      {severityRows.map((row) => (
                        <div key={row.key} className={`dq-bar dq-bar-${row.key}`}>
                          <span>{row.label}</span>
                          <div className="dq-track">
                            <span style={{ width: `${Math.max(0, Math.min(100, row.ratio * 100))}%` }} />
                          </div>
                          <b>{formatInteger(row.count)}</b>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="empty">No anomalies for these filters.</p>
                  )}
                </div>

                <div className="stack">
                  <p className="label-strong">Anomalies</p>
                  {loadingRunDetail ? (
                    <p className="card-message">Loading anomalies...</p>
                  ) : groupedAnomalies.length === 0 ? (
                    <p className="empty">No anomalies for these filters.</p>
                  ) : (
                    groupedAnomalies.map((group) => {
                      const typeInfo = ANOMALY_TYPE_INFO[group.type] ?? {
                        title: humanize(group.type),
                        description: "",
                      }
                      const visibleItems = group.items.slice(0, MAX_ANOMALIES_PER_GROUP)

                      return (
                        <section key={group.type} className="dq-group">
                          <div className="dq-group-head">
                            <h3>{typeInfo.title}</h3>
                            <span className="status">{formatInteger(group.items.length)}</span>
                          </div>
                          {typeInfo.description ? <p className="note small">{typeInfo.description}</p> : null}
                          <div className="dq-cards">
                            {visibleItems.map((anomaly) => {
                              const facts = toAnomalyFacts(anomaly.details)
                              const bits = [
                                anomaly.resource_type ? `Resource: ${anomaly.resource_type}` : null,
                                anomaly.patient_id ? shortPatient(anomaly.patient_id) : null,
                                anomaly.detected_at ? formatDateTime(anomaly.detected_at) : null,
                              ].filter((item): item is string => Boolean(item))

                              return (
                                <article
                                  key={anomaly.anomaly_id}
                                  className={`dq-card dq-card-${toSeverityKey(anomaly.severity)}`}
                                >
                                  <div className="dq-card-head">
                                    <span className={toRunStatusClass(String(anomaly.severity ?? "unknown"))}>
                                      {humanize(String(anomaly.severity ?? "unknown"))}
                                    </span>
                                    {bits.length > 0 ? <span className="muted small">{bits.join(" | ")}</span> : null}
                                  </div>
                                  {facts.length > 0 ? (
                                    <dl className="facts">
                                      {facts.map((fact) => (
                                        <div key={`${anomaly.anomaly_id}-${fact.label}`}>
                                          <dt>{fact.label}</dt>
                                          <dd className="mono">{fact.value}</dd>
                                        </div>
                                      ))}
                                    </dl>
                                  ) : null}
                                </article>
                              )
                            })}
                          </div>
                          {group.items.length > visibleItems.length ? (
                            <p className="note small">and {formatInteger(group.items.length - visibleItems.length)} more</p>
                          ) : null}
                        </section>
                      )
                    })
                  )}
                </div>

                <div className="stack">
                  <p className="label-strong">Checks</p>
                  {loadingRunDetail ? (
                    <p className="card-message">Loading checks...</p>
                  ) : filteredChecks.length === 0 ? (
                    <p className="empty">No checks for these filters.</p>
                  ) : (
                    <>
                      <div className="dq-scroll">
                        <table className="compact">
                          <thead>
                            <tr>
                              <th>Check</th>
                              <th>Status</th>
                              <th>Metric</th>
                              <th className="r">Value</th>
                              <th className="r">Threshold</th>
                              <th>Resource type</th>
                            </tr>
                          </thead>
                          <tbody>
                            {filteredChecks.slice(0, MAX_CHECK_ROWS).map((item) => (
                              <tr key={`${item.check_name}-${item.metric_name ?? "metric"}-${item.measure_id ?? "all"}`}>
                                <td>{humanize(item.check_name)}</td>
                                <td><span className={toRunStatusClass(item.status)}>{humanize(item.status)}</span></td>
                                <td className="mono small">{item.metric_name ?? "-"}</td>
                                <td className="r">{formatMetricValue(item.metric_value)}</td>
                                <td className="r">{formatMetricValue(item.threshold_value)}</td>
                                <td>{item.resource_type ?? "-"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {filteredChecks.length > MAX_CHECK_ROWS ? (
                        <p className="note small">
                          and {formatInteger(filteredChecks.length - MAX_CHECK_ROWS)} more
                        </p>
                      ) : null}
                    </>
                  )}
                </div>
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
