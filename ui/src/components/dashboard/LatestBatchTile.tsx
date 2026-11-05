import type { UseLatestBatchResult } from "../../hooks/useLatestBatch"
import { formatDateTime } from "../../utils/format"

interface LatestBatchTileProps {
  latest: UseLatestBatchResult
  syncedAt: string | null
}

const titleCase = (value: string): string =>
  value.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (part) => part.toUpperCase())

export function LatestBatchTile({ latest, syncedAt }: LatestBatchTileProps) {
  const { batch, awaitingBatch, loading, error } = latest
  const status = batch?.status.toLowerCase() ?? ""
  const running = status === "running"

  const value = loading && !batch
    ? "Loading..."
    : error && !batch
      ? "Unavailable"
      : batch
        ? titleCase(batch.status)
        : "No batch yet"
  const accent = running ? "info" : status === "failed" || (error && !batch) ? "danger" : batch ? "good" : "neutral"
  const errorText = batch?.error ?? (batch ? null : error)

  return (
    <article className={`kpi-card ${accent}`}>
      <span>Latest batch</span>
      <strong>{value}</strong>
      {batch ? (
        <>
          <small className="kpi-time">
            {running ? `Step: ${batch.step ? titleCase(batch.step) : "-"}` : `Finished ${formatDateTime(batch.updatedAt)}`}
          </small>
          {running && batch.progressMessage ? <small className="kpi-time">{batch.progressMessage}</small> : null}
          <small className="kpi-time">Started {formatDateTime(batch.startedAt)}</small>
        </>
      ) : null}
      {errorText ? (
        <small className="kpi-time kpi-error" title={errorText}>{errorText}</small>
      ) : null}
      {awaitingBatch && !running ? <small className="kpi-time">Ingestion loading, pipeline starts next</small> : null}
      {running ? <small className="kpi-time">Updates every 5 seconds</small> : null}
      {syncedAt ? <small className="kpi-time">Synced {formatDateTime(syncedAt)}</small> : null}
    </article>
  )
}
