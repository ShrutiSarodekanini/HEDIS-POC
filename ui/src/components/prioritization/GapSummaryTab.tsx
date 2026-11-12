import { useState } from "react"

import type { EvidenceGapDetail } from "../../models/evidence"
import type { PrioritizationWorklistItem } from "../../models/prioritization"
import { formatDateTime } from "../../utils/format"
import { titleCase } from "../evidence/evidenceFormat"
import { CHANNEL_LABELS, formatNumber } from "./prioritizationFormat"

// Every required part of the evidence is confirmed, so the engine can be asked to rerun with it.
const READY_FOR_ENGINE = new Set(["closed_pending_engine", "excluded_pending_engine"])
const CLOSED_BY_ENGINE = new Set(["closed", "excluded"])

interface GapSummaryTabProps {
  item: PrioritizationWorklistItem
  detail: EvidenceGapDetail | null
  running: boolean
  finalStatus: string | null
  outcome: string | null
  rerunError: string | null
  onRerun: () => void
}

export function GapSummaryTab({ item, detail, running, finalStatus, outcome, rerunError, onRerun }: GapSummaryTabProps) {
  const [confirming, setConfirming] = useState<boolean>(false)
  const reviewStatus = finalStatus ?? detail?.reviewStatus ?? item.reviewStatus
  const closed = CLOSED_BY_ENGINE.has(reviewStatus)
  const canRerun = !closed && READY_FOR_ENGINE.has(reviewStatus)

  return (
    <>
      <div className="kpi-grid four modal-kpis">
        <article className="kpi-card">
          <span>Tier</span>
          <strong>{titleCase(item.tier)}</strong>
        </article>
        <article className="kpi-card">
          <span>Composite score</span>
          <strong>{formatNumber(item.composite, 4)}</strong>
        </article>
        <article className="kpi-card">
          <span>Channel</span>
          <strong>{CHANNEL_LABELS[item.channel ?? ""] ?? item.channel ?? "-"}</strong>
        </article>
        <article className="kpi-card">
          <span>Next visit</span>
          <strong>{item.nextVisit ? formatDateTime(item.nextVisit) : "-"}</strong>
        </article>
      </div>

      <dl className="facts summary-facts">
        <div><dt>Review status</dt><dd>{titleCase(reviewStatus)}</dd></div>
        <div><dt>Missing</dt><dd>{detail?.missingElement || item.missingElement || "-"}</dd></div>
        <div><dt>Deadline</dt><dd>{detail?.deadlineDate ?? "-"}</dd></div>
      </dl>

      {outcome ? <p className="card-message">{outcome}</p> : null}

      {closed ? (
        outcome ? null : <p className="card-message">Closed by the measure engine</p>
      ) : canRerun ? (
        confirming ? (
          <div className="confirm-box">
            <p>
              Rerun the measure engine for this patient with the confirmed evidence? The engine closes the gap only if
              the measure is now met.
            </p>
            <div className="actions">
              <button
                type="button"
                className="btn success"
                disabled={running}
                onClick={() => {
                  onRerun()
                }}
              >
                {running ? "Engine running..." : "Confirm"}
              </button>
              <button
                type="button"
                className="btn outline"
                disabled={running}
                onClick={() => {
                  setConfirming(false)
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="actions">
            <button
              type="button"
              className="btn primary"
              onClick={() => {
                setConfirming(true)
              }}
            >
              Rerun engine
            </button>
          </div>
        )
      ) : (
        <div className="actions">
          <button type="button" className="btn primary" disabled>
            Rerun engine
          </button>
          <span className="note">Confirm the AI evidence first.</span>
        </div>
      )}

      {rerunError ? <p className="card-message error">{rerunError}</p> : null}
    </>
  )
}
