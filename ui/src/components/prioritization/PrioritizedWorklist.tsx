import { useEffect, useMemo, useState } from "react"

import type {
  PrioritizationMeasureSummary,
  PrioritizationWorklistFilters,
  PrioritizationWorklistItem,
} from "../../models/prioritization"
import { formatDateTime } from "../../utils/format"
import { CHANNEL_LABELS, formatNumber, toTierClass } from "./prioritizationFormat"
import { WorklistToolbar } from "./WorklistToolbar"

const ROWS_PER_PAGE = 10

interface PrioritizedWorklistProps {
  worklist: PrioritizationWorklistItem[]
  measures: PrioritizationMeasureSummary[]
  filters: PrioritizationWorklistFilters
  selectedGapId: string
  loadingWorklist: boolean
  statusMessage: string
  errorMessage: string
  setFilter: <K extends keyof PrioritizationWorklistFilters>(key: K, value: PrioritizationWorklistFilters[K]) => void
  selectGap: (gapId: string) => void
  onOpenDetails: (item: PrioritizationWorklistItem) => void
  emptyText?: string
  showAdvancedFilters?: boolean
}

export function PrioritizedWorklist({
  worklist,
  measures,
  filters,
  selectedGapId,
  loadingWorklist,
  statusMessage,
  errorMessage,
  setFilter,
  selectGap,
  onOpenDetails,
  emptyText = "No open gaps found for selected filters.",
  showAdvancedFilters = false,
}: PrioritizedWorklistProps) {
  const [currentPage, setCurrentPage] = useState<number>(1)

  const totalPages = Math.max(1, Math.ceil(worklist.length / ROWS_PER_PAGE))

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages)
    }
  }, [currentPage, totalPages])

  useEffect(() => {
    setCurrentPage(1)
  }, [filters.role, filters.tier, filters.measure, filters.limit])

  const pageStart = (currentPage - 1) * ROWS_PER_PAGE
  const pageEnd = pageStart + ROWS_PER_PAGE
  const visibleRows = useMemo(
    () => worklist.slice(pageStart, pageEnd),
    [worklist, pageStart, pageEnd],
  )

  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>Prioritized worklist</h2>
        <span className="note">Click a row to inspect full scoring breakdown | Page {currentPage} of {totalPages}</span>
      </div>

      <WorklistToolbar
        measures={measures}
        filters={filters}
        setFilter={setFilter}
        showAdvancedFilters={showAdvancedFilters}
      />

      {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
      {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}

      <div className="actions">
        <button
          type="button"
          className="btn outline"
          disabled={currentPage <= 1}
          onClick={() => {
            setCurrentPage((previous) => Math.max(1, previous - 1))
          }}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn outline"
          disabled={currentPage >= totalPages}
          onClick={() => {
            setCurrentPage((previous) => Math.min(totalPages, previous + 1))
          }}
        >
          Next
        </button>
        <span className="note">
          Showing {worklist.length === 0 ? 0 : pageStart + 1}-{Math.min(pageEnd, worklist.length)} of {worklist.length}
        </span>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Patient</th>
              <th>Measure</th>
              <th>Missing</th>
              <th>Tier</th>
              <th>Composite</th>
              <th>Channel</th>
              <th>Next visit</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {loadingWorklist ? (
              <tr>
                <td colSpan={8} className="empty">Loading worklist...</td>
              </tr>
            ) : visibleRows.length > 0 ? (
              visibleRows.map((item) => (
                <tr
                  key={item.gapId}
                  className={item.gapId === selectedGapId ? "history-row-active" : ""}
                  onClick={() => {
                    selectGap(item.gapId)
                  }}
                >
                  <td>{item.patientId}</td>
                  <td>
                    <div>{item.measureName}</div>
                    <div className="note">{item.measureId}</div>
                  </td>
                  <td>{item.missingElement ?? "-"}</td>
                  <td><span className={toTierClass(item.tier)}>{item.tier}</span></td>
                  <td>{formatNumber(item.composite, 4)}</td>
                  <td>{CHANNEL_LABELS[item.channel ?? ""] ?? item.channel ?? "-"}</td>
                  <td>{item.nextVisit ? formatDateTime(item.nextVisit) : "-"}</td>
                  <td>
                    <button
                      type="button"
                      className="btn outline"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenDetails(item)
                      }}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={8} className="empty">{emptyText}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </article>
  )
}
