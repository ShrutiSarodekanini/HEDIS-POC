import type { StarCatalogMeasure } from "../../models/stars"
import { sortByMeasureId } from "./starFormat"

interface StarCutPointsProps {
  measures: StarCatalogMeasure[] | undefined
}

export function StarCutPoints({ measures }: StarCutPointsProps) {
  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>Catalog cut points</h2>
        <span className="note">All measures and star bands</span>
      </div>

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
            {(measures ?? []).length > 0 ? (
              sortByMeasureId(measures ?? []).map((measure) => {
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
                <td colSpan={8} className="empty">Catalog is unavailable for this profile.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </article>
  )
}
