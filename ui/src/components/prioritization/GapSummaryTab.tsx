import { useState } from "react"

import type { EvidenceGapDetail } from "../../models/evidence"
import type { PrioritizationWorklistItem } from "../../models/prioritization"
import { formatDateTime } from "../../utils/format"
import { titleCase } from "../evidence/evidenceFormat"
import { CHANNEL_LABELS, formatNumber } from "./prioritizationFormat"

const CLOSABLE = new Set(["closed_pending_engine", "excluded_pending_engine"])

interface GapSummaryTabProps {
  item: PrioritizationWorklistItem
  detail: EvidenceGapDetail | null
  closing: boolean
  closedStatus: string | null
  closeError: string | null
  onClose: () => void
}

export function GapSummaryTab({ item, detail, closing, closedStatus, closeError, onClose }: GapSummaryTabProps) {
  const [confirming, setConfirming] = useState<boolean>(false)
  const reviewStatus = closedStatus ?? detail?.reviewStatus ?? item.reviewStatus
  const canClose = !closedStatus && CLOSABLE.has(reviewStatus)

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

      {closedStatus ? (
        <p className="card-message">Gap closed</p>
      ) : canClose ? (
        confirming ? (
          <div className="confirm-box">
            <p>Close this gap? This marks the care as done.</p>
            <div className="actions">
              <button
                type="button"
                className="btn success"
                disabled={closing}
                onClick={() => {
                  onClose()
                }}
              >
                {closing ? "Closing..." : "Confirm"}
              </button>
              <button
                type="button"
                className="btn outline"
                disabled={closing}
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
              Close gap
            </button>
          </div>
        )
      ) : (
        <div className="actions">
          <button type="button" className="btn primary" disabled>
            Close gap
          </button>
          <span className="note">Confirm the AI evidence first.</span>
        </div>
      )}

      {closeError ? <p className="card-message error">{closeError}</p> : null}
    </>
  )
}
