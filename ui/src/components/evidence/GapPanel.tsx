import type { EvidenceGapDetail, EvidenceLatestRun } from "../../models/evidence"
import { GapAiRuns } from "./GapAiRuns"
import { GapFacts } from "./GapFacts"

export function GapPanel({
  loadingGapDetail,
  scopeGapDetail,
  latestRun,
}: {
  loadingGapDetail: boolean
  scopeGapDetail: EvidenceGapDetail | null
  latestRun: EvidenceLatestRun | null
}) {
  if (loadingGapDetail) {
    return <p className="note">Loading gap panel...</p>
  }

  if (!scopeGapDetail) {
    return <p className="empty">Choose a scope gap to load full gap facts, score breakdown, and AI run history.</p>
  }

  return (
    <div className="gap-panel">
      <GapFacts detail={scopeGapDetail} />
      <GapAiRuns detail={scopeGapDetail} latestRun={latestRun} />
    </div>
  )
}
