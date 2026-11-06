import { useEffect, useMemo, useState } from "react"

import { useEvidenceWorkflow } from "../../hooks/useEvidenceWorkflow"
import { useGapClose } from "../../hooks/useGapClose"
import type { DocumentPageRef } from "../../models/evidence"
import type { PrioritizationWorklistItem } from "../../models/prioritization"
import { GapFacts } from "../evidence/GapFacts"
import { titleCase } from "../evidence/evidenceFormat"
import { Modal } from "../layout/Modal"
import { DocumentViewer } from "./DocumentViewer"
import { GapEvidenceTab } from "./GapEvidenceTab"
import { GapSummaryTab } from "./GapSummaryTab"

type DetailsTab = "summary" | "why" | "evidence"

const TABS: Array<{ id: DetailsTab; label: string }> = [
  { id: "summary", label: "Summary" },
  { id: "why", label: "Why open" },
  { id: "evidence", label: "AI evidence" },
]

const noop = (): void => {}

interface GapDetailsModalProps {
  item: PrioritizationWorklistItem
  onClose: () => void
  onGapChanged?: () => void
}

export function GapDetailsModal({ item, onClose, onGapChanged = noop }: GapDetailsModalProps) {
  const gapId = item.gapId
  const [tab, setTab] = useState<DetailsTab>("summary")
  const [viewing, setViewing] = useState<DocumentPageRef | null>(null)
  const {
    capabilities,
    overview,
    scopeGapDetail,
    selectedScopeGap,
    selectedScopeGapId,
    selectedProposalId,
    reviewerId,
    decisionReason,
    loadingSetup,
    loadingGapDetail,
    runningExtraction,
    runningDecision,
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
  } = useEvidenceWorkflow()
  const gapClose = useGapClose(gapId, onGapChanged)

  // The hook resets the scope to its first gap when the overview loads, so re-apply until it sticks.
  useEffect(() => {
    if (selectedScopeGapId !== gapId) {
      setSelectedScopeGapId(gapId)
    }
  }, [gapId, selectedScopeGapId, setSelectedScopeGapId])

  const evidenceRows = useMemo(
    () => (overview?.queue ?? []).filter((row) => row.gapId === gapId),
    [overview?.queue, gapId],
  )

  const integration = capabilities?.mode === "integration"
  const detail = selectedScopeGapId === gapId ? scopeGapDetail : null
  // runExtractionNow acts on the hook's scope gap, so the button waits until that is this gap.
  const scopeReady = selectedScopeGapId === gapId && selectedScopeGap?.gapId === gapId
  const noDocuments = integration && scopeReady && !selectedScopeGap.hasDocuments
  const subtitle = `Patient ${item.patientId.slice(0, 8)} | ${titleCase(item.tier)} priority`

  return (
    <Modal title={item.measureName || item.measureId} subtitle={subtitle} onClose={onClose}>
      {viewing ? (
        <DocumentViewer
          pageRef={viewing}
          onBack={() => {
            setViewing(null)
          }}
        />
      ) : (
        <>
          <div className="tabs" role="tablist" aria-label="Gap details">
            {TABS.map((option) => (
              <button
                key={option.id}
                type="button"
                role="tab"
                aria-selected={tab === option.id}
                className={tab === option.id ? "tab active" : "tab"}
                onClick={() => {
                  setTab(option.id)
                }}
              >
                {option.label}
              </button>
            ))}
          </div>

          {loadingSetup ? <p className="card-message">Loading evidence review data...</p> : null}
          {setupError ? (
            <div className="danger-box">
              <p>
                <strong>Evidence review API unavailable:</strong> {setupError}
              </p>
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
          ) : null}
          {statusMessage && tab === "evidence" ? <p className="card-message">{statusMessage}</p> : null}
          {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}

          {tab === "summary" ? (
            <GapSummaryTab
              item={item}
              detail={detail}
              closing={gapClose.closing}
              closedStatus={gapClose.closedStatus}
              closeError={gapClose.error}
              onClose={() => {
                void gapClose.closeGap()
              }}
            />
          ) : null}

          {tab === "why" ? (
            loadingGapDetail || !detail ? (
              <p className="note">Loading gap facts...</p>
            ) : (
              <div className="gap-facts-grid">
                <GapFacts detail={detail} />
              </div>
            )
          ) : null}

          {tab === "evidence" && !loadingSetup && !setupError ? (
            <GapEvidenceTab
              rows={evidenceRows}
              canDecide={integration}
              canRun={integration && scopeReady && !noDocuments}
              noDocuments={noDocuments}
              runningExtraction={runningExtraction}
              selectedProposalId={selectedProposalId}
              reviewerId={reviewerId}
              decisionReason={decisionReason}
              runningDecision={runningDecision}
              onRun={() => {
                void runExtractionNow()
              }}
              onReviewerChange={setReviewerId}
              onReasonChange={setDecisionReason}
              onDecide={(decision, proposalId) => {
                setSelectedProposalId(proposalId)
                void submitDecision(decision, proposalId)
              }}
              onViewDocument={setViewing}
            />
          ) : null}
        </>
      )}
    </Modal>
  )
}
