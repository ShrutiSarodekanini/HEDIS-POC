import type { StarWhatIfResult } from "../../models/stars"
import { formatInteger } from "../../utils/format"

interface StarGapSelectionProps {
  whatIfResult: StarWhatIfResult | null
  selectedGapIds: string[]
  toggleGap: (gapId: string) => void
  selectAllOpenGaps: () => void
  clearGapSelection: () => void
}

export function StarGapSelection({
  whatIfResult,
  selectedGapIds,
  toggleGap,
  selectAllOpenGaps,
  clearGapSelection,
}: StarGapSelectionProps) {
  return (
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
  )
}
