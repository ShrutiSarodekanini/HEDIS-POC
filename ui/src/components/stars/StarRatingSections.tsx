import type { useStarsWorkflow } from "../../hooks/useStarsWorkflow"
import { StarCalculationResults } from "./StarCalculationResults"
import { StarControls } from "./StarControls"
import { StarCutPoints } from "./StarCutPoints"
import { StarGapSelection } from "./StarGapSelection"
import { StarMeasureCards } from "./StarMeasureCards"
import { StarMeasureProjection } from "./StarMeasureProjection"
import { StarOverallSnapshot } from "./StarOverallSnapshot"

interface StarRatingSectionsProps {
  workflow: ReturnType<typeof useStarsWorkflow>
}

export function StarRatingSections({ workflow }: StarRatingSectionsProps) {
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
      />

      <StarOverallSnapshot overview={workflow.overview} />

      <StarMeasureProjection
        whatIfResult={workflow.whatIfResult}
        selectedMeasureCard={workflow.selectedMeasureCard}
        selectedGapIds={workflow.selectedGapIds}
      />

      <StarGapSelection
        whatIfResult={workflow.whatIfResult}
        selectedGapIds={workflow.selectedGapIds}
        toggleGap={workflow.toggleGap}
        selectAllOpenGaps={workflow.selectAllOpenGaps}
        clearGapSelection={workflow.clearGapSelection}
      />

      <StarCalculationResults
        calculationResult={workflow.calculationResult}
        comparisonResult={workflow.comparisonResult}
      />

      <StarMeasureCards
        measureCards={workflow.overview?.measureCards}
        selectedMeasureId={workflow.selectedMeasureId}
        selectMeasure={workflow.selectMeasure}
      />

      <StarCutPoints measures={workflow.overview?.measures} />
    </>
  )
}
