import { useMemo } from "react"

import type { StarMeasureCard } from "../../models/stars"
import { formatInteger, formatRate } from "../../utils/format"
import { sortByMeasureId } from "./starFormat"
import { StarInfoButton } from "./StarInfoButton"
import { StarSection } from "./StarSection"

interface StarMeasureCardsProps {
  measureCards: StarMeasureCard[] | undefined
  selectedMeasureId: string
  selectMeasure: (measureId: string) => Promise<void>
  bare?: boolean
  onShowCutPoints?: (measureId: string) => void
}

export function StarMeasureCards({
  measureCards,
  selectedMeasureId,
  selectMeasure,
  bare,
  onShowCutPoints,
}: StarMeasureCardsProps) {
  const sortedMeasureCards = useMemo(
    () => sortByMeasureId(measureCards ?? []),
    [measureCards],
  )

  return (
    <StarSection
      title="Measure cards"
      bare={bare}
      aside={<span className="note">Current baselines from backend</span>}
    >

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Measure</th>
              <th>Type</th>
              <th>Total</th>
              <th>Baseline rate</th>
              <th>Projected rate</th>
              <th>Baseline star</th>
              <th>Projected star</th>
            </tr>
          </thead>
          <tbody>
            {sortedMeasureCards.length > 0 ? (
              sortedMeasureCards.map((row) => (
                <tr
                  key={row.measureId}
                  className={row.measureId === selectedMeasureId ? "history-row-active" : ""}
                  onClick={() => {
                    void selectMeasure(row.measureId)
                  }}
                >
                  <td>
                    {onShowCutPoints ? (
                      <div className="info-title">
                        <span>{row.measureName}</span>
                        <StarInfoButton
                          label={`Star cut points for ${row.measureId}`}
                          onClick={() => {
                            onShowCutPoints(row.measureId)
                          }}
                        />
                      </div>
                    ) : (
                      <div>{row.measureName}</div>
                    )}
                    <div className="note">{row.measureId}</div>
                  </td>
                  <td>{row.starEligible ? "Star measure" : "Not a Star measure"}</td>
                  <td>{formatInteger(row.total)}</td>
                  <td>{formatRate(row.baselineRate)}</td>
                  <td>{formatRate(row.projectedRate)}</td>
                  <td>{row.baselineStar ?? "-"}</td>
                  <td>{row.projectedStar ?? "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={7} className="empty">No measure rows available.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </StarSection>
  )
}
