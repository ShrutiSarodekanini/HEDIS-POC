import type { DocumentPageRef, EvidenceQueueItem } from "../../models/evidence"
import { GapEvidenceList } from "./GapEvidenceList"

interface GapEvidenceTabProps {
  rows: EvidenceQueueItem[]
  canDecide: boolean
  canRun: boolean
  noDocuments: boolean
  runningExtraction: boolean
  selectedProposalId: string
  reviewerEmail: string
  decisionReason: string
  runningDecision: boolean
  onRun: () => void
  onReasonChange: (value: string) => void
  onDecide: (decision: "confirm" | "reject", proposalId: string) => void
  onViewDocument: (pageRef: DocumentPageRef) => void
}

export function GapEvidenceTab({
  rows,
  canDecide,
  canRun,
  noDocuments,
  runningExtraction,
  selectedProposalId,
  reviewerEmail,
  decisionReason,
  runningDecision,
  onRun,
  onReasonChange,
  onDecide,
  onViewDocument,
}: GapEvidenceTabProps) {
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
              <span>Reviewing as</span>
              <input className="text-input" readOnly value={reviewerEmail} />
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
    </>
  )
}
