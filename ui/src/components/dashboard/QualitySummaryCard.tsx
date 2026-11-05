import { useMemo, useState } from "react"

import { useQualitySummary } from "../../hooks/useQualitySummary"
import type { LatestBatch } from "../../models/calculation"
import { formatDateTime } from "../../utils/format"
import { QualityRunResults } from "../quality/QualityRunResults"
import { SEVERITY_ORDER, humanize, toSeverityKey } from "../quality/qualityDisplay"
import { qualityTitleCase as asLabel } from "./qualityFormat"

const ALL = "All"

const EMPTY_TEXT = "Results appear here after an ingestion run finishes."

const staleNote = (latestBatch: LatestBatch | null, runBatchId: string): string | null => {
  if (!latestBatch || latestBatch.batchId === runBatchId) {
    return null
  }
  const status = latestBatch.status.toLowerCase()
  if (status === "running") {
    return "A new batch is running. These are the previous batch's results."
  }
  if (status === "failed") {
    return "The latest batch failed. These are the previous batch's results."
  }
  return null
}

interface QualitySummaryCardProps {
  dataVersion: number
  latestBatch: LatestBatch | null
}

export function QualitySummaryCard({ dataVersion, latestBatch }: QualitySummaryCardProps) {
  const { summary, loading, error, forbidden, unavailable } = useQualitySummary(dataVersion)
  const [measureFilter, setMeasureFilter] = useState<string>(ALL)
  const [severityFilter, setSeverityFilter] = useState<string>(ALL)
  const note = summary ? staleNote(latestBatch, summary.run.batch_id) : null

  const measureOptions = useMemo(() => {
    const ids = new Set<string>()
    for (const item of [...(summary?.checks ?? []), ...(summary?.anomalies ?? [])]) {
      if (item.measure_id) {
        ids.add(item.measure_id)
      }
    }
    return Array.from(ids).sort()
  }, [summary])

  const activeMeasure = measureOptions.includes(measureFilter) ? measureFilter : ALL

  const filteredChecks = useMemo(() => {
    const checks = summary?.checks ?? []
    return activeMeasure === ALL ? checks : checks.filter((item) => item.measure_id === activeMeasure)
  }, [activeMeasure, summary])

  const filteredAnomalies = useMemo(
    () =>
      (summary?.anomalies ?? []).filter(
        (item) =>
          (activeMeasure === ALL || item.measure_id === activeMeasure) &&
          (severityFilter === ALL || toSeverityKey(item.severity) === severityFilter),
      ),
    [activeMeasure, severityFilter, summary],
  )

  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>Data quality summary</h2>
        {summary ? (
          <div className="calc-actions">
            <label className="calc-check">
              <span>Measure</span>
              <select
                className="text-input"
                value={activeMeasure}
                onChange={(event) => {
                  setMeasureFilter(event.target.value)
                }}
              >
                <option value={ALL}>All</option>
                {measureOptions.map((measureId) => (
                  <option key={measureId} value={measureId}>
                    {measureId}
                  </option>
                ))}
              </select>
            </label>
            <label className="calc-check">
              <span>Severity</span>
              <select
                className="text-input"
                value={severityFilter}
                onChange={(event) => {
                  setSeverityFilter(event.target.value)
                }}
              >
                <option value={ALL}>All</option>
                {SEVERITY_ORDER.map((key) => (
                  <option key={key} value={key}>
                    {humanize(key)}
                  </option>
                ))}
              </select>
            </label>
            <span className="chip info">{asLabel(summary.run.status)}</span>
          </div>
        ) : null}
      </div>

      {loading ? (
        <p className="card-message">Loading data quality results...</p>
      ) : forbidden ? (
        <p className="card-message">Data quality details are visible to administrators only.</p>
      ) : error && !summary ? (
        <p className="card-message error">{error}</p>
      ) : unavailable ? (
        <p className="card-message error">Data quality results are unavailable from this backend.</p>
      ) : !summary ? (
        <p className="card-message">{EMPTY_TEXT}</p>
      ) : (
        <>
          <p className="freshness">
            Batch {summary.run.batch_id.slice(0, 8)} | Run {summary.run.run_id} | Completed{" "}
            {formatDateTime(summary.run.completed_at)}
          </p>

          {note ? <p className="card-message">{note}</p> : null}
          {error ? <p className="card-message error">{error}</p> : null}

          <QualityRunResults compact checks={filteredChecks} anomalies={filteredAnomalies} loading={loading} />
        </>
      )}
    </article>
  )
}
