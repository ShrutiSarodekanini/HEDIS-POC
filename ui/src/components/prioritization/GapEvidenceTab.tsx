import { useState } from "react"

import type { DocumentPageRef, EvidenceGapDetail, EvidenceLatestRun, EvidenceQueueItem } from "../../models/evidence"
import { GapAiRuns } from "../evidence/GapAiRuns"
import { GapEvidenceList } from "./GapEvidenceList"

interface GapEvidenceTabProps {
  rows: EvidenceQueueItem[]
  detail: EvidenceGapDetail | null
  latestRun: EvidenceLatestRun | null
  canDecide: boolean
  canRun: boolean
  noDocuments: boolean
  runningExtraction: boolean
  selectedProposalId: string
  reviewerId: string
  decisionReason: string
  runningDecision: boolean
  onRun: () => void
  onReviewerChange: (value: string) => void
  onReasonChange: (value: string) => void
  onDecide: (decision: "confirm" | "reject", proposalId: string) => void
  onViewDocument: (pageRef: DocumentPageRef) => void
}

export function GapEvidenceTab({
  rows,
  detail,
  latestRun,
  canDecide,
  canRun,
  noDocuments,
  runningExtraction,
  selectedProposalId,
  reviewerId,
  decisionReason,
  runningDecision,
  onRun,
  onReviewerChange,
  onReasonChange,
  onDecide,
  onViewDocument,
}: GapEvidenceTabProps) {
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false)

  return (
    <>
      <div className="evidence-toolbar">
        <button
          type="button"
          className="btn primary"
          disabled={!canRun || runningExtraction}
          title={noDocuments ? "This patient has no converted documents yet" : undefined}
          onClick={onRun}
        >
          {runningExtraction
            ? "Running AI... (can take a minute)"
            : rows.length > 0 ? "Run AI evidence again" : "Run AI evidence"}
        </button>
        {noDocuments ? <span className="note">No converted documents for this patient yet.</span> : null}

        {canDecide && rows.length > 0 ? (
          <>
            <label className="calc-check">
              <span>Reviewer ID</span>
              <input
                className="text-input"
                value={reviewerId}
                onChange={(event) => {
                  onReviewerChange(event.target.value)
                }}
              />
            </label>
            <label className="calc-check">
              <span>Decision notes (required to reject)</span>
              <input
                className="text-input"
                value={decisionReason}
                onChange={(event) => {
                  onReasonChange(event.target.value)
                }}
                placeholder="Add review reason"
              />
            </label>
          </>
        ) : null}
      </div>

      {!canDecide && rows.length > 0 ? (
        <p className="note">Decision actions are available in integration backend profile.</p>
      ) : null}

      <GapEvidenceList
        rows={rows}
        canDecide={canDecide}
        selectedProposalId={selectedProposalId}
        decisionReason={decisionReason}
        runningDecision={runningDecision}
        onDecide={onDecide}
        onViewDocument={onViewDocument}
      />

      {detail ? (
        <>
          <button
            type="button"
            className="btn link"
            aria-expanded={detailsOpen}
            onClick={() => {
              setDetailsOpen((current) => !current)
            }}
          >
            {detailsOpen ? "Hide AI run details" : "Show AI run details"}
          </button>
          {detailsOpen ? (
            <GapAiRuns
              detail={detail}
              latestRun={latestRun}
              onViewPage={(documentId, pageNo) => {
                onViewDocument({ documentId, pageNo, quote: null })
              }}
            />
          ) : null}
        </>
      ) : null}
    </>
  )
}
