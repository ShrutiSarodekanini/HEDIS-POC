import type { StarMeasureCard, StarWhatIfResult } from "../../models/stars"
import { formatInteger, formatRate } from "../../utils/format"

interface StarMeasureProjectionProps {
  whatIfResult: StarWhatIfResult | null
  selectedMeasureCard: StarMeasureCard | null
  selectedGapIds: string[]
}

export function StarMeasureProjection({ whatIfResult, selectedMeasureCard, selectedGapIds }: StarMeasureProjectionProps) {
  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>Selected measure projection</h2>
        <span className="note">{whatIfResult?.measureName ?? selectedMeasureCard?.measureName ?? "-"}</span>
      </div>

      {whatIfResult ? (
        <>
          <div className="kpi-grid four">
            <article className="kpi-card">
              <span>Before rate</span>
              <strong>{formatRate(whatIfResult.baselineRate)}</strong>
            </article>
            <article className="kpi-card">
              <span>After rate</span>
              <strong>{formatRate(whatIfResult.projectedRate)}</strong>
            </article>
            <article className="kpi-card">
              <span>Before star</span>
              <strong>{whatIfResult.baselineStar ?? "-"}</strong>
            </article>
            <article className="kpi-card">
              <span>After star</span>
              <strong>{whatIfResult.projectedStar ?? "-"}</strong>
            </article>
          </div>

          <p className="note">
            Selected closures: {formatInteger(selectedGapIds.length)} | Rate change: {whatIfResult.rateDelta == null
              ? "-"
              : `${whatIfResult.rateDelta >= 0 ? "+" : ""}${whatIfResult.rateDelta.toFixed(2)} pts`} | Star changed: {whatIfResult.starChanged ? "Yes" : "No"}
          </p>

          {whatIfResult.disclaimer ? <p className="note">{whatIfResult.disclaimer}</p> : null}
          {whatIfResult.warnings.length > 0 ? (
            <div className="card-message">
              {whatIfResult.warnings.join(" ")}
            </div>
          ) : null}
        </>
      ) : (
        <p className="card-message">No projection available yet.</p>
      )}
    </article>
  )
}
