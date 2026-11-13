import type { EvidenceGapDetail, EvidenceLatestRun } from "../../models/evidence"
import { formatDateTime, formatInteger } from "../../utils/format"
import { titleCase } from "./evidenceFormat"

type PageSent = { documentId: string; pageNo: number; method: string | null; handwritten: boolean }

const pageLabel = (page: PageSent): string =>
  `${page.documentId} p${page.pageNo}${page.method ? ` (${page.method}${page.handwritten ? ", handwritten" : ""})` : ""}`

const aiStatusClass = (status: string): string => {
  const normalized = status.trim().toLowerCase().replaceAll("_", "-")
  return `ai-run ai-${normalized || "unknown"}`
}

function PagesSent({ pages }: { pages: PageSent[] }) {
  return <p className="note small">Pages sent: {pages.length ? pages.map(pageLabel).join(" | ") : "none"}</p>
}

interface GapAiRunsProps {
  detail: EvidenceGapDetail
  latestRun: EvidenceLatestRun | null
}

/** Section 6 of the gap panel: the run just made and every earlier AI run on this gap. */
export function GapAiRuns({ detail, latestRun }: GapAiRunsProps) {
  return (
    <section className="psec">
      <h3>6 · AI details</h3>

      {latestRun ? (
        <div className="ai-run ai-succeeded">
          <p className="label-strong">The run just made (run {latestRun.runId ?? "?"}) · {titleCase(latestRun.status ?? "unknown")}</p>
          <p className="note small">
            Saved {formatInteger(latestRun.saved)} · Skipped {formatInteger(latestRun.skippedAlreadySaved)} ·
            Failed attempts {formatInteger(latestRun.failedAttempts)}
          </p>
          {latestRun.attempts.map((attempt, index) => (
            <p key={`latest-attempt-${index}`} className="note small">
              Attempt {attempt.attemptId || "?"} · {titleCase(attempt.status)} · {attempt.model ?? "-"} ·
              {" "}{attempt.durationMs != null ? `${attempt.durationMs}ms` : "-"} ·
              {" "}tokens {attempt.promptTokens ?? "?"}/{attempt.outputTokens ?? "?"}
            </p>
          ))}
        </div>
      ) : null}

      {detail.aiRuns.length ? (
        detail.aiRuns.map((run) => (
          <div key={run.runId} className={aiStatusClass(run.status)}>
            <div>
              Run {run.runId} · {formatDateTime(run.startedAt)} · {run.modelName ?? "-"} · {titleCase(run.status)} ·
              {" "}{formatInteger(run.kept)} kept, {formatInteger(run.droppedCount)} dropped · prompt {run.promptVersion ?? "?"}
            </div>
            {run.correlationId ? <p className="note small">Correlation ID: {run.correlationId}</p> : null}
            <PagesSent pages={run.pagesSent} />
            {run.error ? <p className="note small error">{run.error}</p> : null}
          </div>
        ))
      ) : (
        <p className="empty">No AI runs on this gap yet.</p>
      )}
    </section>
  )
}
