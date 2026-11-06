import type { useStarsWorkflow } from "../../hooks/useStarsWorkflow"
import { StarCalculationResults } from "../stars/StarCalculationResults"
import { StarControls } from "../stars/StarControls"
import { StarGapSelection } from "../stars/StarGapSelection"
import { StarMeasureCards } from "../stars/StarMeasureCards"
import { StarMeasureProjection } from "../stars/StarMeasureProjection"
import { StarOverallSnapshot } from "../stars/StarOverallSnapshot"

type StarsWorkflow = ReturnType<typeof useStarsWorkflow>

interface DashboardStarOverviewProps {
  workflow: StarsWorkflow
  onShowCutPoints: (measureId: string) => void
}

export function DashboardStarOverview({ workflow, onShowCutPoints }: DashboardStarOverviewProps) {
  return (
    <>
      <StarOverallSnapshot overview={workflow.overview} bare />
      <StarMeasureCards
        measureCards={workflow.overview?.measureCards}
        selectedMeasureId={workflow.selectedMeasureId}
        selectMeasure={workflow.selectMeasure}
        onShowCutPoints={onShowCutPoints}
        bare
      />
    </>
  )
}

export function DashboardStarWhatIf({ workflow }: { workflow: StarsWorkflow }) {
  return (
    <>
      <StarControls
        capabilities={workflow.capabilities}
        overview={workflow.overview}
        whatIfResult={workflow.whatIfResult}
        selectedMeasureId={workflow.selectedMeasureId}
        loadingWhatIf={workflow.loadingWhatIf}
        runningCalculation={workflow.runningCalculation}
        runningComparison={workflow.runningComparison}
        statusMessage={workflow.statusMessage}
        errorMessage={workflow.errorMessage}
        selectMeasure={workflow.selectMeasure}
        runProjection={workflow.runProjection}
        calculateCurrentStar={workflow.calculateCurrentStar}
        compareBeforeAfter={workflow.compareBeforeAfter}
        bare
      />

      <div className="star-side-by-side">
        <StarMeasureProjection
          whatIfResult={workflow.whatIfResult}
          selectedMeasureCard={workflow.selectedMeasureCard}
          selectedGapIds={workflow.selectedGapIds}
          bare
        />
        <StarCalculationResults
          calculationResult={workflow.calculationResult}
          comparisonResult={workflow.comparisonResult}
          bare
        />
      </div>

      <StarGapSelection
        whatIfResult={workflow.whatIfResult}
        selectedGapIds={workflow.selectedGapIds}
        toggleGap={workflow.toggleGap}
        selectAllOpenGaps={workflow.selectAllOpenGaps}
        clearGapSelection={workflow.clearGapSelection}
        bare
      />
    </>
  )
}
