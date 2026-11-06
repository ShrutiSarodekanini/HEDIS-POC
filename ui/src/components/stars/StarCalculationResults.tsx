import type { StarCalculationResult, StarComparisonResult } from "../../models/stars"
import { formatRate } from "../../utils/format"
import { toStatusClass } from "./starFormat"
import { StarSection } from "./StarSection"

interface StarCalculationResultsProps {
  calculationResult: StarCalculationResult | null
  comparisonResult: StarComparisonResult | null
  bare?: boolean
}

export function StarCalculationResults({ calculationResult, comparisonResult, bare }: StarCalculationResultsProps) {
  return (
    <StarSection
      title="Direct calculation results"
      bare={bare}
      aside={<span className="note">Selected measure star assignment</span>}
    >

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
    </StarSection>
  )
}
