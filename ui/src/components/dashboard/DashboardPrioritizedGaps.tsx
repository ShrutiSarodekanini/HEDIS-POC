import { useEffect, useRef, useState } from "react"

import { usePrioritizationWorkflow } from "../../hooks/usePrioritizationWorkflow"
import type { PrioritizationWorklistItem } from "../../models/prioritization"
import { GapDetailsModal } from "../prioritization/GapDetailsModal"
import { PrioritizedWorklist } from "../prioritization/PrioritizedWorklist"

interface DashboardPrioritizedGapsProps {
  dataVersion: number
  batchRunning: boolean
  onGapChanged: () => void
}

export function DashboardPrioritizedGaps({ dataVersion, batchRunning, onGapChanged }: DashboardPrioritizedGapsProps) {
  const {
    overview,
    worklist,
    filters,
    selectedGapId,
    loadingSetup,
    loadingWorklist,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup,
    refreshAll,
    setFilter,
    selectGap,
  } = usePrioritizationWorkflow()

  const [detailsItem, setDetailsItem] = useState<PrioritizationWorklistItem | null>(null)
  const seenVersionRef = useRef<number>(dataVersion)
  const refreshAllRef = useRef(refreshAll)

  useEffect(() => {
    refreshAllRef.current = refreshAll
  }, [refreshAll])

  useEffect(() => {
    if (seenVersionRef.current === dataVersion) {
      return
    }
    seenVersionRef.current = dataVersion
    void refreshAllRef.current()
  }, [dataVersion])

  if (loadingSetup) {
    return (
      <article className="card state-card">
        <p className="card-message">Loading prioritization capabilities from backend...</p>
      </article>
    )
  }

  return (
    <>
      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Prioritization API unavailable:</strong> {setupError}
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check prioritization endpoints
            </button>
          </div>
        </article>
      ) : null}

      <PrioritizedWorklist
        worklist={worklist}
        measures={overview?.measures ?? []}
        filters={filters}
        selectedGapId={selectedGapId}
        loadingWorklist={loadingWorklist}
        statusMessage={statusMessage}
        errorMessage={errorMessage}
        setFilter={setFilter}
        selectGap={selectGap}
        onOpenDetails={setDetailsItem}
        emptyText={batchRunning ? "Gaps appear here once the gaps step finishes." : undefined}
      />

      {detailsItem ? (
        <GapDetailsModal
          item={detailsItem}
          onGapChanged={onGapChanged}
          onClose={() => {
            setDetailsItem(null)
          }}
        />
      ) : null}
    </>
  )
}
