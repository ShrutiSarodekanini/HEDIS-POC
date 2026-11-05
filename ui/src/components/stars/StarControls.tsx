import { useMemo } from "react"

import type { StarCapabilities, StarOverview, StarWhatIfResult } from "../../models/stars"
import { sortByMeasureId } from "./starFormat"

interface MeasureOption {
  measureId: string
  measureName: string
}

interface StarControlsProps {
  capabilities: StarCapabilities | null
  overview: StarOverview | null
  whatIfResult: StarWhatIfResult | null
  selectedMeasureId: string
  loadingWhatIf: boolean
  runningCalculation: boolean
  runningComparison: boolean
  statusMessage: string
  errorMessage: string
  selectMeasure: (measureId: string) => Promise<void>
  runProjection: () => Promise<void>
  calculateCurrentStar: () => Promise<void>
  compareBeforeAfter: () => Promise<void>
}

export function StarControls({
  capabilities,
  overview,
  whatIfResult,
  selectedMeasureId,
  loadingWhatIf,
  runningCalculation,
  runningComparison,
  statusMessage,
  errorMessage,
  selectMeasure,
  runProjection,
  calculateCurrentStar,
  compareBeforeAfter,
}: StarControlsProps) {
  const measureOptions = useMemo(() => {
    const options: MeasureOption[] = []
    const seen = new Set<string>()

    for (const row of overview?.measureCards ?? []) {
      if (!seen.has(row.measureId)) {
        seen.add(row.measureId)
        options.push({
          measureId: row.measureId,
          measureName: row.measureName,
        })
      }
    }

    for (const row of overview?.measures ?? []) {
      if (!seen.has(row.measureId)) {
        seen.add(row.measureId)
        options.push({
          measureId: row.measureId,
          measureName: row.measureName,
        })
      }
    }

    return sortByMeasureId(options)
  }, [overview?.measureCards, overview?.measures])

  return (
    <article className="card">
      <div className="card-head row-split">
        <h2>Star controls</h2>
        <span className="chip info">
          {capabilities?.mode === "integration" ? "Integration mode" : "Dashboard mode"}
        </span>
      </div>

      <div className="calc-toolbar quality-filters">
        <label className="calc-check">
          <span>Measure</span>
          <select
            className="text-input"
            value={selectedMeasureId}
            onChange={(event) => {
              void selectMeasure(event.target.value)
            }}
          >
            {measureOptions.map((measure) => (
              <option key={measure.measureId} value={measure.measureId}>
                {measure.measureId} - {measure.measureName}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="actions">
        <button
          type="button"
          className="btn primary"
          disabled={loadingWhatIf || !selectedMeasureId}
          onClick={() => {
            void runProjection()
          }}
        >
          {loadingWhatIf ? "Running..." : "Apply selected closures"}
        </button>
        <button
          type="button"
          className="btn outline"
          disabled={runningCalculation || !whatIfResult}
          onClick={() => {
            void calculateCurrentStar()
          }}
        >
          {runningCalculation ? "Calculating..." : "Calculate current star"}
        </button>
        <button
          type="button"
          className="btn outline"
          disabled={runningComparison || !whatIfResult}
          onClick={() => {
            void compareBeforeAfter()
          }}
        >
          {runningComparison ? "Comparing..." : "Compare before vs after"}
        </button>
      </div>

      {statusMessage ? <p className="card-message">{statusMessage}</p> : null}
      {errorMessage ? <p className="card-message error">{errorMessage}</p> : null}
    </article>
  )
}
