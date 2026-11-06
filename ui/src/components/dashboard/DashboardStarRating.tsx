import { useEffect, useRef, useState } from "react"

import { useStarsWorkflow } from "../../hooks/useStarsWorkflow"
import { formatDateTime } from "../../utils/format"
import { StarCutPointsModal } from "../stars/StarCutPointsModal"
import { StarInfoButton } from "../stars/StarInfoButton"
import { DashboardStarOverview, DashboardStarWhatIf } from "./DashboardStarPanels"

type StarTab = "overview" | "what-if"

const TABS: { id: StarTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "what-if", label: "What-if" },
]

interface DashboardStarRatingProps {
  dataVersion: number
  fetchedAt: string
  isAdmin: boolean
}

export function DashboardStarRating({ dataVersion, fetchedAt, isAdmin }: DashboardStarRatingProps) {
  const workflow = useStarsWorkflow()
  const { loadingSetup, setupError, retrySetup, refreshOverview } = workflow
  const [tab, setTab] = useState<StarTab>("overview")
  // null keeps the cut points window closed; "" opens it on all measures.
  const [cutPointsMeasure, setCutPointsMeasure] = useState<string | null>(null)

  const seenVersionRef = useRef<number>(dataVersion)
  const refreshOverviewRef = useRef(refreshOverview)

  useEffect(() => {
    refreshOverviewRef.current = refreshOverview
  }, [refreshOverview])

  useEffect(() => {
    if (seenVersionRef.current === dataVersion) {
      return
    }
    seenVersionRef.current = dataVersion
    void refreshOverviewRef.current()
  }, [dataVersion])

  return (
    <article className="card star-rating-card">
      <div className="card-head row-split">
        <div className="info-title">
          <h2>Star rating</h2>
          <StarInfoButton
            label="Star cut points"
            onClick={() => {
              setCutPointsMeasure("")
            }}
          />
        </div>
        {isAdmin ? (
          <a className="text-link" href="#view-stars">
            Open Star Rating
          </a>
        ) : null}
      </div>
      <p className="freshness">
        Last updated {formatDateTime(fetchedAt)} | Source: star dashboard overall
      </p>

      {loadingSetup ? (
        <p className="card-message">Loading star rating capabilities from backend...</p>
      ) : (
        <>
          {setupError ? (
            <div className="danger-box">
              <p>
                <strong>Star rating API unavailable:</strong> {setupError}
              </p>
              <div className="actions">
                <button
                  type="button"
                  className="btn outline"
                  onClick={() => {
                    void retrySetup()
                  }}
                >
                  Re-check star endpoints
                </button>
              </div>
            </div>
          ) : null}

          <div className="tabs" role="tablist" aria-label="Star rating">
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

          {tab === "overview" ? (
            <DashboardStarOverview workflow={workflow} onShowCutPoints={setCutPointsMeasure} />
          ) : (
            <DashboardStarWhatIf workflow={workflow} />
          )}
        </>
      )}

      {cutPointsMeasure !== null ? (
        <StarCutPointsModal
          measures={workflow.overview?.measures}
          initialMeasureId={cutPointsMeasure}
          onClose={() => {
            setCutPointsMeasure(null)
          }}
        />
      ) : null}
    </article>
  )
}
