import { useState } from "react"

import type { DocumentPageRef, EvidenceQueueItem } from "../../models/evidence"
import { findingSentence, formatConfidence, quoteBlock } from "../evidence/evidenceFormat"

const FIRST_FINDINGS = 3
const LONG_QUOTE_CHARS = 220

const quoteOf = (row: EvidenceQueueItem): string | null => {
  if (row.context?.text) {
    const start = Math.max(0, row.context.quoteStart)
    return row.context.text.slice(start, Math.max(start, row.context.quoteEnd)) || null
  }
  return row.excerpt || null
}

function FindingQuote({ text }: { text: string }) {
  const [expanded, setExpanded] = useState<boolean>(false)
  const long = text.length > LONG_QUOTE_CHARS
  return (
    <div className="fsec">
      <b>QUOTE</b>
      <p className={long && !expanded ? "quote-text clamped" : "quote-text"}>{text}</p>
      {long ? (
        <button
          type="button"
          className="btn link"
          onClick={() => {
            setExpanded((current) => !current)
          }}
        >
          {expanded ? "Show less" : "Show more"}
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
      <div className="finding-grid">
        {visibleRows.map((row) => (
          <div
            key={row.proposalId}
            className={row.proposalId === selectedProposalId ? "finding-card selected" : "finding-card"}
          >
            <div className="finding-head">
              <p className="title">{row.proposedFinding}</p>
              <span className="chip">{formatConfidence(row.confidence)}</span>
            </div>
            <p className="note">{findingSentence(row)}</p>
            <FindingQuote text={quoteBlock(row)} />
            <div className="actions">
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
            </div>

            {canDecide ? (
              <div className="actions">
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
              </div>
            ) : null}
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
