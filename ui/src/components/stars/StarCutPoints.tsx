import type { StarCatalogMeasure } from "../../models/stars"
import { sortByMeasureId } from "./starFormat"
import { StarSection } from "./StarSection"

interface StarCutPointsProps {
  measures: StarCatalogMeasure[] | undefined
  measureFilter?: string
  bare?: boolean
}

export function StarCutPoints({ measures, measureFilter, bare }: StarCutPointsProps) {
  const rows = measureFilter
    ? (measures ?? []).filter((measure) => measure.measureId === measureFilter)
    : measures ?? []
  const emptyText = (measures ?? []).length > 0
    ? "No cut points for this measure."
    : "Catalog is unavailable for this profile."

  return (
    <StarSection
      title="Catalog cut points"
      bare={bare}
      aside={<span className="note">All measures and star bands</span>}
    >

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Measure</th>
              <th>Year</th>
              <th>Weight</th>
              <th>1 star</th>
              <th>2 stars</th>
              <th>3 stars</th>
              <th>4 stars</th>
              <th>5 stars</th>
            </tr>
          </thead>
          <tbody>
            {rows.length > 0 ? (
              sortByMeasureId(rows).map((measure) => {
                const byStars = new Map<number, { low: number; high: number | null }>()
                for (const cutPoint of measure.cutPoints) {
                  byStars.set(cutPoint.stars, { low: cutPoint.low, high: cutPoint.high })
                }

                return (
                  <tr key={measure.measureId}>
                    <td>
                      <div>{measure.measureName}</div>
                      <div className="note">{measure.measureId}</div>
                    </td>
                    <td>{measure.measurementYear}</td>
                    <td>{measure.weight ?? "-"}</td>
                    {[1, 2, 3, 4, 5].map((stars) => {
                      const range = byStars.get(stars)
                      return (
                        <td key={`${measure.measureId}-${stars}`}>
                          {range ? `${range.low}${range.high == null ? "+" : ` to ${range.high}`}%` : "-"}
                        </td>
                      )
                    })}
                  </tr>
                )
              })
            ) : (
              <tr>
                <td colSpan={8} className="empty">{emptyText}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </StarSection>
  )
}
