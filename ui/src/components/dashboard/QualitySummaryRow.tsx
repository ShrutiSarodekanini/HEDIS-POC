import { useMemo } from "react"

import type { QualityAnomaly, QualityCheck, QualityRunSummary } from "../../models/quality"
import { formatDateTime, formatInteger } from "../../utils/format"
import { severityRows } from "../quality/qualityDisplay"
import { qualityTitleCase as asLabel } from "./qualityFormat"

const countStatus = (checks: QualityCheck[], status: string): number =>
  checks.filter((item) => String(item.status).toLowerCase() === status).length

interface QualitySummaryRowProps {
  run: QualityRunSummary
  checks: QualityCheck[]
  anomalies: QualityAnomaly[]
  open: boolean
  detailsId: string
  onToggle: () => void
}

export function QualitySummaryRow({ run, checks, anomalies, open, detailsId, onToggle }: QualitySummaryRowProps) {
  const passed = useMemo(() => countStatus(checks, "passed"), [checks])
  const failed = useMemo(() => countStatus(checks, "failed"), [checks])
  const bars = useMemo(() => severityRows(anomalies), [anomalies])
  const barTitle = bars.map((row) => `${row.label} ${row.count}`).join(", ")

  return (
    <div className="dq-summary-row">
      <span className="chip info">{asLabel(run.status)}</span>
      <span className="freshness">
        Batch {run.batch_id.slice(0, 8)} | Run {run.run_id} | Completed {formatDateTime(run.completed_at)}
      </span>
      <span className="chip">{formatInteger(checks.length)} checks</span>
      <span className="chip good">{formatInteger(passed)} passed</span>
      <span className={failed > 0 ? "chip danger" : "chip"}>{formatInteger(failed)} failed</span>
      <span className="chip">{formatInteger(anomalies.length)} anomalies</span>
      {bars.length > 0 ? (
        <div className="dq-stack" title={barTitle} role="img" aria-label={`Anomalies by severity: ${barTitle}`}>
          {bars.map((row) => (
            <span key={row.key} className={`dq-stack-${row.key}`} style={{ width: `${row.ratio * 100}%` }}></span>
          ))}
        </div>
      ) : null}
      <button
        type="button"
        className="btn outline calc-small dq-toggle"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={onToggle}
      >
        <span aria-hidden="true" className={open ? "chevron open" : "chevron"}>&#8250;</span>
        {open ? "Hide details" : "Show details"}
      </button>
    </div>
  )
}
