import { useMemo } from "react"

import { useEvidenceWorkflow } from "../hooks/useEvidenceWorkflow"
import type { EvidenceGapDetail, EvidenceQueueItem } from "../models/evidence"
import { formatDateTime, formatInteger, toMeterWidth } from "../utils/format"

const MAX_QUEUE_ROWS = 40

const CHANNEL_LABELS: Record<string, string> = {
  abstractor_review: "Abstractor review",
  member_outreach_call: "Member outreach call",
  mailed_fit_kit: "Mailed FIT kit",
  point_of_care_alert: "Point-of-care alert",
  provider_gap_list: "Provider gap list",
  chart_review: "Chart review",
}

const formatConfidence = (value: number | null): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "- confidence"
  }
  const normalized = value <= 1 ? value * 100 : value
  return `${normalized.toFixed(1)}% confidence`
}

const formatDuration = (value: number | null): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`
  }
  return `${(value / 1000).toFixed(2)} s`
}

const formatDelta = (value: number | null | undefined, fractionDigits = 3): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  const prefix = value > 0 ? "+" : ""
  return `${prefix}${value.toFixed(fractionDigits)}`
}

const formatPercent = (value: number | null | undefined, fractionDigits = 2): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  return `${value.toFixed(fractionDigits)}%`
}

const titleCase = (value: string | null | undefined): string => {
  const text = (value ?? "").trim()
  if (!text) {
    return "-"
  }
  return text
    .replaceAll("_", " ")
    .split(" ")
    .filter((part) => part.length > 0)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ")
}

const typeName = (value: string | null | undefined): string => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (!normalized) {
    return "Evidence"
  }
  if (normalized === "bp_control") {
    return "Blood pressure control"
  }
  if (normalized === "hba1c") {
    return "HbA1c"
  }
  return titleCase(normalized)
}

const findingSentence = (item: EvidenceQueueItem): string => {
  const parts = [
    item.evidenceKind ? titleCase(item.evidenceKind) : "Evidence",
    item.evidenceType ? typeName(item.evidenceType) : "",
    item.serviceDateRaw ? `on ${item.serviceDateRaw}` : "",
    item.value ? `value ${item.value}` : "",
  ].filter((part) => part.length > 0)
  return parts.join(" ")
}

const checkLine = (item: EvidenceQueueItem): string => {
  if (!item.ruleCheck) {
    return "No rule-check diagnostics were returned for this finding."
  }

  const inside = item.ruleCheck.insideWindow
  const windowText = item.ruleCheck.windowText
    || [item.ruleCheck.windowFrom, item.ruleCheck.windowTo].filter(Boolean).join(" to ")
    || "n/a"

  return `Window ${windowText}. Service dates ${item.ruleCheck.serviceDateFrom ?? "-"} to ${item.ruleCheck.serviceDateTo ?? "-"}. In window: ${inside == null ? "unknown" : inside ? "yes" : "no"}.`
}

const impactLine = (item: EvidenceQueueItem): string => {
  if (!item.impact) {
    return "Impact preview unavailable for this gap."
  }
  if (!item.impact.available) {
    return item.impact.note ?? "Impact preview unavailable for this gap."
  }

  const closures = item.impact.closuresForNextStar == null ? "-" : String(item.impact.closuresForNextStar)
  return `Rate ${formatPercent(item.impact.rateNow)} to ${formatPercent(item.impact.rateAfter)} (${formatDelta(item.impact.changePoints, 2)} pts). Stars ${item.impact.starsNow ?? "-"} to ${item.impact.starsAfter ?? "-"}. Closures needed for next star after this gap: ${closures}.`
}

const quoteBlock = (item: EvidenceQueueItem): string => {
  if (item.context?.text) {
    const start = Math.max(0, item.context.quoteStart)
    const end = Math.max(start, item.context.quoteEnd)
    const text = item.context.text
    const before = text.slice(0, start)
    const quote = text.slice(start, end)
    const after = text.slice(end)
    return `${before}[QUOTE]${quote}[/QUOTE]${after}`
  }
  return item.excerpt
}

const pageList = (pages: Array<{ documentId: string; pageNo: number; method: string | null; handwritten: boolean }>): string => {
  if (!pages.length) {
    return "none"
  }
  return pages.map((page) => `${page.documentId} p${page.pageNo}${page.method ? ` (${page.method}${page.handwritten ? ", handwritten" : ""})` : ""}`).join(" | ")
}

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

function GapPanel({
  loadingGapDetail,
  scopeGapDetail,
  latestRun,
}: {
  loadingGapDetail: boolean
  scopeGapDetail: EvidenceGapDetail | null
  latestRun: ReturnType<typeof useEvidenceWorkflow>["latestRun"]
}) {
  if (loadingGapDetail) {
    return <p className="note">Loading gap panel...</p>
  }

  if (!scopeGapDetail) {
    return <p className="empty">Choose a scope gap to load full gap facts, score breakdown, and AI run history.</p>
  }

  const breakdown = scopeGapDetail.breakdown
  const likelihood = breakdown?.likelihood
  const value = breakdown?.value
  const engagement = breakdown?.engagement

  return (
    <div className="gap-panel">
      <section className="psec">
        <h3>1 · Gap</h3>
        <dl className="facts">
          <div><dt>Measure</dt><dd>{scopeGapDetail.measureName} ({scopeGapDetail.measureId})</dd></div>
          <div><dt>Patient</dt><dd>{scopeGapDetail.patientId}</dd></div>
          <div><dt>Missing</dt><dd>{scopeGapDetail.missingElement || "-"}</dd></div>
          <div><dt>Tier</dt><dd>{titleCase(scopeGapDetail.tier)}</dd></div>
          <div><dt>Deadline</dt><dd>{scopeGapDetail.deadlineDate ?? "-"}</dd></div>
          <div><dt>Review status</dt><dd>{titleCase(scopeGapDetail.reviewStatus)}</dd></div>
          <div>
            <dt>Last event</dt>
            <dd>
              {scopeGapDetail.lastRelevantEventDate
                ? `${scopeGapDetail.lastRelevantEventDate} ${scopeGapDetail.lastRelevantEventDescription ?? ""}`
                : "none recorded"}
            </dd>
          </div>
        </dl>
      </section>

      {likelihood ? (
        <section className="psec">
          <h3>2 · How likely it closes</h3>
          <div className="calc">
            {likelihood.steps.map((step, index) => (
              <div key={`${step.label}-${index}`} className="calc-row">
                <span>{step.label}</span>
                <span className="v">{formatDelta(step.change, 4)}</span>
              </div>
            ))}
            <div className="calc-row calc-total">
              <span>Closure likelihood</span>
              <span className="v">{formatDelta(likelihood.result, 4)}</span>
            </div>
          </div>
          {likelihood.raw != null && likelihood.result != null && likelihood.raw !== likelihood.result ? (
            <p className="note small">Raw {formatDelta(likelihood.raw, 4)}, kept between {likelihood.min ?? "-"} and {likelihood.max ?? "-"}.</p>
          ) : null}
        </section>
      ) : null}

      {value ? (
        <section className="psec">
          <h3>3 · How much it helps the stars</h3>
          <dl className="facts">
            <div><dt>Done / eligible</dt><dd>{value.numerator ?? "-"} / {value.denominator ?? "-"}</dd></div>
            <div><dt>Rate</dt><dd>{formatPercent(value.rate)} to {value.rounded ?? "-"}%</dd></div>
            <div><dt>Stars now</dt><dd>{value.stars ?? "-"}</dd></div>
            <div><dt>Next star</dt><dd>{value.nextCut != null ? `${value.nextCut}%` : "already at top"}</dd></div>
            <div><dt>Measure weight</dt><dd>{value.weight ?? "-"}</dd></div>
          </dl>
          <p className="formula">
            {value.reason ? `${value.reason} -> result ${formatDelta(value.result, 4)}` : `Result ${formatDelta(value.result, 4)}`}
          </p>
        </section>
      ) : null}

      {engagement ? (
        <section className="psec">
          <h3>4 · How reachable</h3>
          <div className="calc">
            {engagement.steps.map((step, index) => (
              <div key={`${step.label}-${index}`} className="calc-row">
                <span>{step.label}</span>
                <span className="v">{formatDelta(step.change, 3)}</span>
              </div>
            ))}
            <div className="calc-row calc-total">
              <span>Engagement (max {engagement.cap ?? "-"})</span>
              <span className="v">{formatDelta(engagement.result, 3)}</span>
            </div>
          </div>
        </section>
      ) : null}

      <section className="psec">
        <h3>5 · Priority</h3>
        <p className="formula">Composite {formatDelta(breakdown?.composite, 4)}</p>
        <p className="note">Recommended channel: {CHANNEL_LABELS[breakdown?.channel ?? ""] ?? titleCase(breakdown?.channel) ?? "-"}</p>
        <p className="note">{impactLine(scopeGapDetail.evidence[0] ?? {
          proposalId: "",
          gapId: scopeGapDetail.gapId,
          gapKey: scopeGapDetail.key,
          patientId: scopeGapDetail.patientId,
          measureId: scopeGapDetail.measureId,
          measureName: scopeGapDetail.measureName,
          tier: scopeGapDetail.tier,
          missingElement: scopeGapDetail.missingElement,
          deadlineDate: scopeGapDetail.deadlineDate,
          documentId: "",
          pageNo: null,
          evidenceKind: "",
          evidenceType: "",
          serviceDateRaw: null,
          serviceDateFrom: null,
          serviceDateTo: null,
          value: null,
          excerpt: "",
          proposedFinding: "",
          confidence: null,
          model: null,
          reason: null,
          status: "proposed",
          reviewStatus: scopeGapDetail.reviewStatus,
          decisionReason: null,
          context: null,
          ruleCheck: null,
          attempt: null,
          impact: scopeGapDetail.impact,
        })}</p>
      </section>

      <section className="psec">
        <h3>6 · AI details</h3>

        {latestRun ? (
          <div className="ai-run ai-succeeded">
            <p className="label-strong">The run just made (run {latestRun.runId ?? "?"})</p>
            <p className="note small">Pages sent: {pageList(latestRun.pages)}</p>
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

        {scopeGapDetail.aiRuns.length ? (
          scopeGapDetail.aiRuns.map((run) => (
            <div key={run.runId} className={aiStatusClass(run.status)}>
              <div>
                Run {run.runId} · {formatDateTime(run.startedAt)} · {run.modelName ?? "-"} · {titleCase(run.status)} ·
                {" "}{formatInteger(run.kept)} kept, {formatInteger(run.droppedCount)} dropped · prompt {run.promptVersion ?? "?"}
              </div>
              <p className="note small">Pages sent: {pageList(run.pagesSent)}</p>
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
    </div>
  )
}

export function EvidenceReviewPage() {
  const {
    capabilities,
    overview,
    scopeGapDetail,
    latestRun,
    selectedScopeGap,
    selectedScopeGapId,
    selectedProposalId,
    reviewerId,
    decisionReason,
    loadingSetup,
    loadingOverview,
    loadingGapDetail,
    runningExtraction,
    runningDecision,
    runningExport,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup,
    refreshAll,
    setSelectedScopeGapId,
    setSelectedProposalId,
    setReviewerId,
    setDecisionReason,
    runExtractionNow,
    submitDecision,
    runExport,
  } = useEvidenceWorkflow()

  const queueRows = useMemo(
    () => (overview?.queue ?? []).slice(0, MAX_QUEUE_ROWS),
    [overview?.queue],
  )

  if (loadingSetup) {
    return (
      <section id="view-evidence" className="view">
        <article className="card state-card">
          <h2>Evidence Review</h2>
          <p className="card-message">Loading evidence review capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-evidence" className="view">
      <div className="page-head">
        <div>
          <h1>Evidence Review</h1>
          <p>AI-proposed findings from chart text, confirmed or rejected by a named reviewer.</p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={loadingOverview}
            onClick={() => {
              void refreshAll()
            }}
          >
            {loadingOverview ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Evidence review API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check evidence endpoints
            </button>
          </div>
        </article>
      ) : null}

      <div className="grid two">
        <article className="card">
          <h2>Run AI evidence extraction</h2>

          <label className="calc-check">
            <span>Extraction model version</span>
            <input
              className="text-input"
              readOnly
              value={overview?.modelName ?? "Unavailable in this backend profile"}
            />
          </label>

          <label className="calc-check">
            <span>Scope</span>
            <select
              className="text-input"
              value={selectedScopeGapId}
              onChange={(event) => {
                setSelectedScopeGapId(event.target.value)
              }}
            >
              {overview?.scopeGaps.length ? overview.scopeGaps.map((row) => (
                <option key={row.gapId} value={row.gapId}>
                  {row.measureId} - {row.measureName} ({row.patientId}) {row.hasDocuments ? "" : "[no docs]"}
                </option>
              )) : (
                <option value="">No available scope</option>
              )}
            </select>
          </label>

          <button
            type="button"
            className="btn primary"
            disabled={
              !selectedScopeGap
              || runningExtraction
              || (capabilities?.mode === "integration" && !selectedScopeGap.hasDocuments)
            }
            onClick={() => {
              void runExtractionNow()
            }}
          >
            {runningExtraction
              ? (capabilities?.mode === "integration" ? "Running extraction..." : "Running process...")
              : (capabilities?.mode === "integration" ? "Run extraction" : "Run process now")}
          </button>

          {capabilities?.mode === "integration" && selectedScopeGap && !selectedScopeGap.hasDocuments ? (
            <p className="note">Selected gap has no converted documents yet. Upload and convert documents first.</p>
          ) : null}

          {capabilities?.mode === "dashboard" ? (
            <p className="note">
              This backend profile uses star dashboard plus automation process endpoints. Run process now refreshes
              queue data from currently loaded records.
            </p>
          ) : null}

          <h3 className="title evidence-subhead">Acceptance rate by model</h3>
          {overview?.acceptanceByModel.length ? overview.acceptanceByModel.map((row) => (
            <div key={row.model} className="evidence-model-row">
              <div className="metric-row">
                <span>{row.model}</span>
                <b>{row.acceptanceRate == null ? "-" : `${row.acceptanceRate.toFixed(1)}%`}</b>
              </div>
              <div className="meter"><span style={{ width: `${toMeterWidth(row.acceptanceRate)}%` }}></span></div>
              <p className="note">
                Proposed {formatInteger(row.proposed)} | Confirmed {formatInteger(row.confirmed)} | Rejected {formatInteger(row.rejected)}
              </p>
            </div>
          )) : <p className="empty">No model acceptance history yet.</p>}
        </article>

        <article className="card">
          <h2>Audit-first by design</h2>
          <p className="note">Enforced by table constraints, not UI promise:</p>
          <pre className="codebox">{`CHECK (
  (status = 'proposed'
    AND reviewer_id IS NULL
    AND decided_at IS NULL)
  OR
  (status IN ('confirmed','rejected')
    AND reviewer_id IS NOT NULL
    AND decided_at IS NOT NULL)
)`}</pre>

          <p className="note">
            Current model connectivity: {overview?.modelConnected == null ? "unknown" : overview.modelConnected ? "connected" : "not ready"}
          </p>

          <div className="kpi-grid three">
            <article className="kpi-card">
              <span>Proposed</span>
              <strong>{overview?.counts.proposed == null ? "-" : formatInteger(overview.counts.proposed)}</strong>
            </article>
            <article className="kpi-card">
              <span>Confirmed</span>
              <strong>{overview?.counts.confirmed == null ? "-" : formatInteger(overview.counts.confirmed)}</strong>
            </article>
            <article className="kpi-card">
              <span>Rejected</span>
              <strong>{overview?.counts.rejected == null ? "-" : formatInteger(overview.counts.rejected)}</strong>
            </article>
          </div>

          <label className="calc-check">
            <span>Reviewer ID</span>
            <input
              className="text-input"
              value={reviewerId}
              onChange={(event) => {
                setReviewerId(event.target.value)
              }}
              placeholder="abstractor.user"
            />
          </label>

          <div className="actions">
            <button
              type="button"
              className="btn outline"
              disabled={runningExport}
              onClick={() => {
                void runExport()
              }}
            >
              {runningExport
                ? (capabilities?.mode === "integration" ? "Exporting..." : "Loading sample data...")
                : (capabilities?.mode === "integration" ? "Export approved evidence to engine" : "Load sample data")}
            </button>
            <span className="note">
              Total {overview?.exportSummary.total == null ? "-" : formatInteger(overview.exportSummary.total)} |
              Ready {overview?.exportSummary.readyToExport == null ? "-" : formatInteger(overview.exportSummary.readyToExport)} |
              Waiting {overview?.exportSummary.waitingForParts == null ? "-" : formatInteger(overview.exportSummary.waitingForParts)}
            </span>
          </div>

          {capabilities?.mode === "dashboard" ? (
            <p className="note small">
              Load sample data triggers the dashboard seed endpoint, then refreshes scope and queue.
            </p>
          ) : null}

          <h3 className="title evidence-subhead">Recent exports</h3>
          {overview?.exports.length ? (
            <div className="export-history">
              {overview.exports.map((run) => (
                <div key={run.id} className="export-row">
                  <p className="title">Run {run.id} · {formatDateTime(run.runAt)}</p>
                  <p className="note">Rows {formatInteger(run.rowCount)} · SHA {run.fileSha256 || "-"}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty">No export runs yet.</p>
          )}
        </article>
      </div>

      <article className="card">
        <h2>Gap panel</h2>
        <GapPanel loadingGapDetail={loadingGapDetail} scopeGapDetail={scopeGapDetail} latestRun={latestRun} />
      </article>

      <article className="card">
        <h2>Review queue</h2>
        <p className="note">
          {overview?.queueSourceLabel ?? "-"} | {overview?.queueLine ?? "-"} | Showing {queueRows.length} row(s)
        </p>

        {queueRows.length === 0 ? (
          <p className="empty">No proposed evidence is currently waiting for review.</p>
        ) : queueRows.map((row) => {
          const active = row.proposalId === selectedProposalId
          return (
            <div
              key={row.proposalId}
              className={active ? "review-row active-review qcard" : "review-row qcard"}
              onClick={() => {
                setSelectedProposalId(row.proposalId)
              }}
            >
              <div>
                <p className="title">{row.patientId} - {row.measureName} ({row.measureId})</p>
                <p className="note">Gap {row.gapKey} | Tier {titleCase(row.tier)} | Review status {titleCase(row.reviewStatus)}</p>
                <p>{row.proposedFinding}</p>
              </div>
              <span className="chip">{formatConfidence(row.confidence)}</span>

              {active ? (
                <>
                  <div className="fsec"><b>GAP</b><p>{row.missingElement || "Missing element not provided."} Deadline: {row.deadlineDate ?? "-"}.</p></div>
                  <div className="fsec"><b>FINDING</b><p>{findingSentence(row)}</p></div>
                  <div className="fsec"><b>WHY</b><p>{row.reason ?? "No rule reason provided."}</p></div>
                  <div className="fsec"><b>CHECK</b><p>{checkLine(row)}</p></div>
                  <div className="fsec"><b>QUOTE</b><pre className="codebox wrap">{quoteBlock(row)}</pre></div>
                  <div className="fsec"><b>IMPACT</b><p>{impactLine(row)}</p></div>
                  <div className="fsec"><b>AI RUN</b>
                    <p>
                      {row.attempt
                        ? `${row.attempt.model ?? "-"} at ${formatDateTime(row.attempt.startedAt)} · ${formatDuration(row.attempt.durationMs)} · kept ${formatInteger(row.attempt.kept)} dropped ${formatInteger(row.attempt.dropped)}`
                        : "No attempt metadata returned for this finding."}
                    </p>
                  </div>

                  {capabilities?.mode === "integration" ? (
                    <>
                      <p className="note">Reviewer decision requires reviewer_id and decided_at.</p>

                      <div className="grid two">
                        <label className="calc-check">
                          <span>Reviewer ID</span>
                          <input
                            className="text-input"
                            value={reviewerId}
                            onChange={(event) => {
                              setReviewerId(event.target.value)
                            }}
                          />
                        </label>
                        <label className="calc-check">
                          <span>Decision notes (required on reject)</span>
                          <input
                            className="text-input"
                            value={decisionReason}
                            onChange={(event) => {
                              setDecisionReason(event.target.value)
                            }}
                            placeholder="Add review reason"
                          />
                        </label>
                      </div>

                      <div className="actions">
                        <button
                          type="button"
                          className="btn success"
                          disabled={runningDecision}
                          onClick={() => {
                            void submitDecision("confirm")
                          }}
                        >
                          {runningDecision ? "Submitting..." : "Confirm finding"}
                        </button>
                        <button
                          type="button"
                          className="btn outline"
                          disabled={runningDecision}
                          onClick={() => {
                            void submitDecision("reject")
                          }}
                        >
                          {runningDecision ? "Submitting..." : "Reject finding"}
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="note">Decision actions are available in integration backend profile.</p>
                  )}
                </>
              ) : null}
            </div>
          )
        })}
      </article>

      {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
      {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}
    </section>
  )
}
