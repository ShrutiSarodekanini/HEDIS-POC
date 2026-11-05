import { useMemo, useState } from "react"

import type { QualityAnomaly, QualityCheck } from "../../models/quality"
import { formatInteger } from "../../utils/format"
import { failedCheckSentence } from "../dashboard/qualityFormat"
import { QualityAnomalyGroups } from "./QualityAnomalyGroups"
import { QualityChecksTable } from "./QualityChecksTable"
import { groupAnomalies, severityRows } from "./qualityDisplay"

interface QualityRunResultsProps {
  checks: QualityCheck[]
  anomalies: QualityAnomaly[]
  loading: boolean
  compact?: boolean
}

const countStatus = (checks: QualityCheck[], status: string): number =>
  checks.filter((item) => String(item.status).toLowerCase() === status).length

export function QualityRunResults({ checks, anomalies, loading, compact = false }: QualityRunResultsProps) {
  const [checksOpen, setChecksOpen] = useState<boolean>(false)

  const groupedAnomalies = useMemo(() => groupAnomalies(anomalies), [anomalies])
  const severityBars = useMemo(() => severityRows(anomalies), [anomalies])
  const checksPassed = useMemo(() => countStatus(checks, "passed"), [checks])
  const checksNotAvailable = useMemo(() => countStatus(checks, "not_available"), [checks])
  const failedChecks = useMemo(
    () => checks.filter((item) => String(item.status).toLowerCase() === "failed"),
    [checks],
  )

  return (
    <>
      <div className={`kpi-grid ${checksNotAvailable > 0 ? "five" : "four"}`}>
        <article className="kpi-card">
          <span>Checks run</span>
          <strong>{formatInteger(checks.length)}</strong>
        </article>
        <article className="kpi-card">
          <span>Passed</span>
          <strong>{formatInteger(checksPassed)}</strong>
        </article>
        <article className="kpi-card">
          <span>Failed</span>
          <strong>{formatInteger(failedChecks.length)}</strong>
        </article>
        <article className="kpi-card">
          <span>Anomalies raised</span>
          <strong>{formatInteger(anomalies.length)}</strong>
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
        {loading ? (
          <p className="card-message">Loading run details...</p>
        ) : severityBars.length > 0 ? (
          <div className="dq-bars">
            {severityBars.map((row) => (
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
        {loading ? (
          <p className="card-message">Loading anomalies...</p>
        ) : groupedAnomalies.length === 0 ? (
          <p className="empty">No anomalies for these filters.</p>
        ) : (
          <QualityAnomalyGroups groups={groupedAnomalies} compact={compact} />
        )}
      </div>

      <div className="stack">
        <p className="label-strong">Checks</p>
        {loading ? (
          <p className="card-message">Loading checks...</p>
        ) : checks.length === 0 ? (
          <p className="empty">No checks for these filters.</p>
        ) : !compact ? (
          <QualityChecksTable checks={checks} />
        ) : (
          <>
            {failedChecks.length > 0 ? (
              <ul className="failed-checks">
                {failedChecks.map((check, index) => (
                  <li key={`${check.check_name}-${check.measure_id ?? check.resource_type ?? ""}-${index}`}>
                    {failedCheckSentence(check)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="note">All checks passed.</p>
            )}
            <div className="actions">
              <button
                type="button"
                className="btn outline calc-small"
                aria-expanded={checksOpen}
                onClick={() => {
                  setChecksOpen((current) => !current)
                }}
              >
                {checksOpen ? "Hide checks" : "Show all checks"}
              </button>
            </div>
            {checksOpen ? <QualityChecksTable checks={checks} /> : null}
          </>
        )}
      </div>
    </>
  )
}
