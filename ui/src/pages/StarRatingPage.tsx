import { useMemo } from "react"

import { useStarsWorkflow } from "../hooks/useStarsWorkflow"
import { formatInteger, formatRate } from "../utils/format"

const toStatusClass = (status: string): string => {
  const normalized = status.trim().toLowerCase()
  if (normalized === "assigned" || normalized === "completed") {
    return "status status-ok"
  }
  if (normalized === "unavailable" || normalized === "running") {
    return "status status-warn"
  }
  return "status"
}

const sortByMeasureId = <T extends { measureId: string }>(rows: T[]): T[] =>
  [...rows].sort((left, right) => left.measureId.localeCompare(right.measureId))

interface MeasureOption {
  measureId: string
  measureName: string
}

export function StarRatingPage() {
  const {
    capabilities,
    overview,
    selectedMeasureCard,
    whatIfResult,
    calculationResult,
    comparisonResult,
    selectedMeasureId,
    selectedGapIds,
    loadingSetup,
    loadingOverview,
    loadingWhatIf,
    runningCalculation,
    runningComparison,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup,
    refreshOverview,
    selectMeasure,
    toggleGap,
    selectAllOpenGaps,
    clearGapSelection,
    runProjection,
    calculateCurrentStar,
    compareBeforeAfter,
  } = useStarsWorkflow()

  const sortedMeasureCards = useMemo(
    () => sortByMeasureId(overview?.measureCards ?? []),
    [overview?.measureCards],
  )

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

  if (loadingSetup) {
    return (
      <section id="view-stars" className="view">
        <article className="card state-card">
          <h2>Star Rating</h2>
          <p className="card-message">Loading star rating capabilities from backend...</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-stars" className="view">
      <div className="page-head">
        <div>
          <h1>Star Rating</h1>
          <p>Evaluate current stars, simulate gap closures, and compare before versus after outcomes.</p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={loadingOverview}
            onClick={() => {
              void refreshOverview()
            }}
          >
            {loadingOverview ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

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

      <article className="card">
        <div className="card-head row-split">
          <h2>Overall snapshot</h2>
          <span className="note">
            Measurement year {overview?.measurementYear ?? "-"}
            {overview?.cmsRatingYear ? ` | CMS rating year ${overview.cmsRatingYear}` : ""}
          </span>
        </div>

        <div className="kpi-grid four">
          <article className="kpi-card">
            <span>Rated measures</span>
            <strong>{formatInteger(overview?.overall?.ratedMeasures ?? 0)}</strong>
          </article>
          <article className="kpi-card">
            <span>Baseline overall star</span>
            <strong>{overview?.overall?.baselineStar?.toFixed(2) ?? "-"}</strong>
          </article>
          <article className="kpi-card">
            <span>Projected overall star</span>
            <strong>{overview?.overall?.projectedStar?.toFixed(2) ?? "-"}</strong>
          </article>
          <article className="kpi-card">
            <span>Expected change</span>
            <strong className="good-text">
              {overview?.overall?.delta == null ? "-" : `${overview.overall.delta >= 0 ? "+" : ""}${overview.overall.delta.toFixed(2)}`}
            </strong>
          </article>
        </div>

        <p className="note">
          {overview?.catalogUrl ? (
            <>
              Measure set: <a className="text-link" href={overview.catalogUrl} target="_blank" rel="noreferrer">CMS reference</a>
            </>
          ) : (
            "Catalog URL is not available in this backend profile."
          )}
          {overview?.notesUrl ? (
            <>
              {" "}| Technical notes: <a className="text-link" href={overview.notesUrl} target="_blank" rel="noreferrer">details</a>
            </>
          ) : null}
        </p>
      </article>

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

      <article className="card">
        <div className="card-head row-split">
          <h2>Gap selection</h2>
          <span className="note">{formatInteger(whatIfResult?.gaps.length ?? 0)} gaps</span>
        </div>

        <div className="actions">
          <button type="button" className="btn outline" onClick={selectAllOpenGaps} disabled={!whatIfResult?.gaps.length}>
            Select all open
          </button>
          <button type="button" className="btn outline" onClick={clearGapSelection} disabled={selectedGapIds.length === 0}>
            Clear
          </button>
          <span className="note">{formatInteger(selectedGapIds.length)} selected</span>
        </div>

        <div className="star-gap-list">
          {(whatIfResult?.gaps ?? []).length > 0 ? (
            (whatIfResult?.gaps ?? []).map((gap) => (
              <label key={gap.gapId} className={`star-gap-item${gap.isClosed ? " closed" : ""}`}>
                <input
                  type="checkbox"
                  checked={selectedGapIds.includes(gap.gapId)}
                  disabled={!gap.eligibleForClosure}
                  onChange={() => {
                    toggleGap(gap.gapId)
                  }}
                />
                <span>
                  <strong>{gap.gapId.slice(0, 8)}</strong> {gap.memberId !== "-" ? `| ${gap.memberId}` : ""} | {gap.memberName}
                </span>
                <span className={gap.isClosed ? "status status-ok" : "status status-warn"}>{gap.evidenceStatus}</span>
              </label>
            ))
          ) : (
            <p className="empty">No gaps are available for this measure.</p>
          )}
        </div>
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Direct calculation results</h2>
          <span className="note">Selected measure star assignment</span>
        </div>

        {calculationResult ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Measure</th>
                  <th>Input score</th>
                  <th>Applied score</th>
                  <th>Assigned star</th>
                  <th>Status</th>
                  <th>Explanation</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{calculationResult.measureId}</td>
                  <td>{formatRate(calculationResult.inputScore)}</td>
                  <td>{formatRate(calculationResult.appliedScore)}</td>
                  <td>{calculationResult.assignedStar ?? "-"}</td>
                  <td><span className={toStatusClass(calculationResult.ratingStatus)}>{calculationResult.ratingStatus}</span></td>
                  <td>{calculationResult.explanation}</td>
                </tr>
              </tbody>
            </table>
          </div>
        ) : (
          <p className="card-message">Run "Calculate current star" to view output.</p>
        )}

        {comparisonResult ? (
          <div className="table-wrap star-compare-table">
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Score</th>
                  <th>Star</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Before</td>
                  <td>{formatRate(comparisonResult.before.appliedScore)}</td>
                  <td>{comparisonResult.before.assignedStar ?? "-"}</td>
                  <td><span className={toStatusClass(comparisonResult.before.ratingStatus)}>{comparisonResult.before.ratingStatus}</span></td>
                </tr>
                <tr>
                  <td>After</td>
                  <td>{formatRate(comparisonResult.after.appliedScore)}</td>
                  <td>{comparisonResult.after.assignedStar ?? "-"}</td>
                  <td><span className={toStatusClass(comparisonResult.after.ratingStatus)}>{comparisonResult.after.ratingStatus}</span></td>
                </tr>
              </tbody>
            </table>
            <p className="note">Star changed: {comparisonResult.starChanged ? "Yes" : "No"}</p>
          </div>
        ) : null}
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Measure cards</h2>
          <span className="note">Current baselines from backend</span>
        </div>

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
                      <div>{row.measureName}</div>
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
      </article>

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
              {(overview?.measures ?? []).length > 0 ? (
                sortByMeasureId(overview?.measures ?? []).map((measure) => {
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
    </section>
  )
}
