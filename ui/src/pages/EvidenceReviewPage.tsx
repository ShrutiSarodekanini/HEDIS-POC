import { useMemo } from "react"

import { GapPanel } from "../components/evidence/GapPanel"
import {
  findingSentence,
  formatConfidence,
  impactLine,
  quoteBlock,
  titleCase,
} from "../components/evidence/evidenceFormat"
import { useEvidenceWorkflow } from "../hooks/useEvidenceWorkflow"
import type { EvidenceQueueItem } from "../models/evidence"
import { formatDateTime, formatInteger, toMeterWidth } from "../utils/format"

const MAX_QUEUE_ROWS = 40

const formatDuration = (value: number | null): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`
  }
  return `${(value / 1000).toFixed(2)} s`
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
    loadingGapDetail,
    runningExtraction,
    runningDecision,
    runningExport,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup,
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
