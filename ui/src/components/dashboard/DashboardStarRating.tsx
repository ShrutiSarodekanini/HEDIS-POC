import { useEffect, useRef } from "react"

import { useStarsWorkflow } from "../../hooks/useStarsWorkflow"
import { formatDateTime } from "../../utils/format"
import { StarRatingSections } from "../stars/StarRatingSections"

interface DashboardStarRatingProps {
  dataVersion: number
  fetchedAt: string
  isAdmin: boolean
}

export function DashboardStarRating({ dataVersion, fetchedAt, isAdmin }: DashboardStarRatingProps) {
  const workflow = useStarsWorkflow()
  const { loadingSetup, setupError, retrySetup, refreshOverview } = workflow

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
    <>
      <div className="card-head row-split">
        <p className="freshness">
          Last updated {formatDateTime(fetchedAt)} | Source: star dashboard overall
        </p>
        {isAdmin ? (
          <a className="text-link" href="#view-stars">
            Open Star Rating
          </a>
        ) : null}
      </div>

      {loadingSetup ? (
        <article className="card state-card">
          <p className="card-message">Loading star rating capabilities from backend...</p>
        </article>
      ) : (
        <>
          {setupError ? (
            <article className="card danger-box">
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
            </article>
          ) : null}

          <StarRatingSections workflow={workflow} />
        </>
      )}
    </>
  )
}
