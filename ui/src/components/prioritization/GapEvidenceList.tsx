import { useState } from "react"

import type { DocumentPageRef, EvidenceQueueItem } from "../../models/evidence"
import { findingSentence, formatConfidence } from "../evidence/evidenceFormat"

const FIRST_FINDINGS = 3

const quoteOf = (row: EvidenceQueueItem): string => {
  if (row.context?.text) {
    const start = Math.max(0, row.context.quoteStart)
    return row.context.text.slice(start, Math.max(start, row.context.quoteEnd)) || row.excerpt
  }
  return row.excerpt
}

function FindingQuote({ row }: { row: EvidenceQueueItem }) {
  const [showContext, setShowContext] = useState<boolean>(false)
  const context = row.context
  const start = context ? Math.max(0, context.quoteStart) : 0
  const end = context ? Math.max(start, context.quoteEnd) : 0

  return (
    <div className="finding-quote">
      {showContext && context ? (
        <p className="quote-text">
          {context.text.slice(0, start)}
          <mark>{context.text.slice(start, end)}</mark>
          {context.text.slice(end)}
        </p>
      ) : (
        <p className="quote-text"><mark>{quoteOf(row)}</mark></p>
      )}
      {context ? (
        <button
          type="button"
          className="btn link"
          onClick={() => {
            setShowContext((current) => !current)
          }}
        >
          {showContext ? "Hide context" : "Show context"}
        </button>
      ) : null}
    </div>
  )
}

interface GapEvidenceListProps {
  rows: EvidenceQueueItem[]
  canDecide: boolean
  selectedProposalId: string
  decisionReason: string
  runningDecision: boolean
  onDecide: (decision: "confirm" | "reject", proposalId: string) => void
  onViewDocument: (pageRef: DocumentPageRef) => void
}

export function GapEvidenceList({
  rows,
  canDecide,
  selectedProposalId,
  decisionReason,
  runningDecision,
  onDecide,
  onViewDocument,
}: GapEvidenceListProps) {
  const [showAll, setShowAll] = useState<boolean>(false)

  if (rows.length === 0) {
    return <p className="empty">No AI evidence for this gap yet.</p>
  }

  const hasReason = decisionReason.trim().length > 0
  const visibleRows = showAll ? rows : rows.slice(0, FIRST_FINDINGS)

  return (
    <>
      <div className="finding-list">
        {visibleRows.map((row) => (
          <div key={row.proposalId} className={row.proposalId === selectedProposalId ? "finding-row selected" : "finding-row"}>
            <div className="finding-main">
              <div className="finding-head">
                <p className="title">{row.proposedFinding}</p>
                <span className="chip">{formatConfidence(row.confidence)}</span>
              </div>
              <p className="note">{findingSentence(row)}</p>
              <FindingQuote row={row} />
            </div>

            <div className="finding-side">
              <span className="note">Document {row.documentId || "-"} | Page {row.pageNo ?? "-"}</span>
              {row.documentId && row.pageNo != null ? (
                <button
                  type="button"
                  className="btn outline"
                  onClick={() => {
                    onViewDocument({ documentId: row.documentId, pageNo: row.pageNo ?? 1, quote: quoteOf(row) })
                  }}
                >
                  View document
                </button>
              ) : null}
              {canDecide ? (
                <>
                  <button
                    type="button"
                    className="btn success"
                    disabled={runningDecision}
                    onClick={() => {
                      onDecide("confirm", row.proposalId)
                    }}
                  >
                    {runningDecision ? "Submitting..." : "Confirm finding"}
                  </button>
                  <button
                    type="button"
                    className="btn outline"
                    disabled={runningDecision || !hasReason}
                    title={hasReason ? "Reject this finding" : "Enter decision notes above to reject"}
                    onClick={() => {
                      onDecide("reject", row.proposalId)
                    }}
                  >
                    {runningDecision ? "Submitting..." : "Reject finding"}
                  </button>
                </>
              ) : null}
            </div>
          </div>
        ))}
      </div>

      {rows.length > FIRST_FINDINGS ? (
        <button
          type="button"
          className="btn link"
          onClick={() => {
            setShowAll((current) => !current)
          }}
        >
          {showAll ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      ) : null}
    </>
  )
}
