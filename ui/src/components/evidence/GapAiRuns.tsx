import type { EvidenceGapDetail, EvidenceLatestRun } from "../../models/evidence"
import { formatDateTime, formatInteger } from "../../utils/format"
import { titleCase, typeName } from "./evidenceFormat"

type PageSent = { documentId: string; pageNo: number; method: string | null; handwritten: boolean }

const pageLabel = (page: PageSent): string =>
  `${page.documentId} p${page.pageNo}${page.method ? ` (${page.method}${page.handwritten ? ", handwritten" : ""})` : ""}`

const aiStatusClass = (status: string): string => {
  const normalized = status.trim().toLowerCase().replaceAll("_", "-")
  return `ai-run ai-${normalized || "unknown"}`
}

const prettyJson = (value: string | null | undefined): string => {
  if (!value) {
    return "-"
  }
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
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
          <p className="label-strong">The run just made (run {latestRun.runId ?? "?"})</p>
          <PagesSent pages={latestRun.pages} />
          {latestRun.calls.map((call, index) => (
            <details key={`latest-call-${index}`}>
              <summary>Call {index + 1} ({call.status ?? "?"}, {call.seconds ?? "?"}s)</summary>
              <pre className="codebox">{call.requestMessages.map((message) => `[${message.role}]\n${message.content}`).join("\n\n") || "No request payload captured."}</pre>
              {call.error ? <p className="note small error">{call.error}</p> : null}
              {call.answer ? <pre className="codebox">{prettyJson(call.answer)}</pre> : null}
            </details>
          ))}
          {latestRun.kept.map((item, index) => (
            <p key={`kept-${index}`} className="found kept">Kept: {typeName(item.evidenceType)} {item.documentId} p{item.pageNo ?? "?"}: "{item.excerpt}"</p>
          ))}
          {latestRun.dropped.map((item, index) => (
            <p key={`dropped-${index}`} className="found dropped">Dropped: {typeName(item.evidenceType)} {item.documentId ?? "?"}: {item.reason}{item.evidenceId ? ` (evidence ${item.evidenceId.slice(0, 8)})` : ""}</p>
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
            <PagesSent pages={run.pagesSent} />
            {run.error ? <p className="note small error">{run.error}</p> : null}
            <details>
              <summary>Raw answer</summary>
              <pre className="codebox">{prettyJson(run.rawResponse)}</pre>
            </details>
            {run.dropped.map((dropped, index) => (
              <p key={`${run.runId}-drop-${index}`} className="note small">
                Dropped {typeName(dropped.evidenceType)}: {dropped.reason}
                {dropped.evidenceId ? ` (evidence ${dropped.evidenceId.slice(0, 8)})` : ""}
              </p>
            ))}
          </div>
        ))
      ) : (
        <p className="empty">No AI runs on this gap yet.</p>
      )}
    </section>
  )
}
