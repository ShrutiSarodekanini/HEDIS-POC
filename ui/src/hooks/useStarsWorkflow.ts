import { useCallback, useEffect, useMemo, useState } from "react"

import type {
  StarCalculationResult,
  StarCapabilities,
  StarComparisonResult,
  StarMeasureCard,
  StarOverview,
  StarWhatIfResult,
} from "../models/stars"
import {
  discoverStarCapabilities,
  loadStarOverview,
  loadStarWhatIf,
  refreshStarCalculationData,
  runStarCalculation,
  runStarComparison,
} from "../services/stars.service"
import { toRequestErrorMessage } from "../services/http"

const NO_GAPS_STAR_MESSAGE = "No gaps to calculate the star."

const isZeroDenominatorMessage = (message: string): boolean => {
  const normalized = message.trim().toLowerCase()
  return normalized.includes("denominator") && (normalized.includes("zero") || normalized.includes("greater than"))
}

const firstMeasureId = (overview: StarOverview | null): string =>
  overview?.measureCards[0]?.measureId ?? overview?.measures[0]?.measureId ?? ""

const toUniqueIds = (ids: string[]): string[] => Array.from(new Set(ids))

const recalculateOverall = (measureCards: StarMeasureCard[]): NonNullable<StarOverview["overall"]> => {
  const ratedMeasures = measureCards.filter((row) => row.starEligible).length
  const ratedWithData = measureCards.filter((row) => row.starEligible && row.baselineStar != null && row.projectedStar != null)

  const weightedAverage = (rows: StarMeasureCard[], key: "baselineStar" | "projectedStar"): number | null => {
    let totalWeight = 0
    let weightedSum = 0

    for (const row of rows) {
      const star = row[key]
      if (star == null) {
        continue
      }
      const weight = row.weight != null && row.weight > 0 ? row.weight : 1
      totalWeight += weight
      weightedSum += star * weight
    }

    if (totalWeight <= 0) {
      return null
    }
    return Number((weightedSum / totalWeight).toFixed(2))
  }

  const baselineStar = weightedAverage(ratedWithData, "baselineStar")
  const projectedStar = weightedAverage(ratedWithData, "projectedStar")

  return {
    ratedMeasures,
    ratedMeasuresWithData: ratedWithData.length,
    totalMeasures: measureCards.length,
    unratedMeasures: Math.max(measureCards.length - ratedMeasures, 0),
    baselineStar,
    projectedStar,
    delta: baselineStar == null || projectedStar == null ? null : Number((projectedStar - baselineStar).toFixed(2)),
  }
}

const applyProjectionToOverview = (
  current: StarOverview | null,
  projection: StarWhatIfResult,
): StarOverview | null => {
  if (!current) {
    return current
  }

  let found = false
  const measureCards = current.measureCards.map((row) => {
    if (row.measureId !== projection.measureId) {
      return row
    }

    found = true
    return {
      ...row,
      projectedNumerator: projection.projectedNumerator,
      projectedRate: projection.projectedRate,
      projectedStar: projection.projectedStar,
      starDelta: projection.baselineStar == null || projection.projectedStar == null
        ? null
        : Number((projection.projectedStar - projection.baselineStar).toFixed(2)),
    }
  })

  if (!found) {
    return current
  }

  return {
    ...current,
    measureCards,
    overall: recalculateOverall(measureCards),
  }
}

export function useStarsWorkflow() {
  const [capabilities, setCapabilities] = useState<StarCapabilities | null>(null)
  const [overview, setOverview] = useState<StarOverview | null>(null)
  const [whatIfResult, setWhatIfResult] = useState<StarWhatIfResult | null>(null)
  const [calculationResult, setCalculationResult] = useState<StarCalculationResult | null>(null)
  const [comparisonResult, setComparisonResult] = useState<StarComparisonResult | null>(null)

  const [selectedMeasureId, setSelectedMeasureId] = useState<string>("")
  const [selectedGapIds, setSelectedGapIds] = useState<string[]>([])

  const [loadingSetup, setLoadingSetup] = useState<boolean>(true)
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false)
  const [loadingWhatIf, setLoadingWhatIf] = useState<boolean>(false)
  const [runningCalculation, setRunningCalculation] = useState<boolean>(false)
  const [runningComparison, setRunningComparison] = useState<boolean>(false)

  const [setupError, setSetupError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [statusMessage, setStatusMessage] = useState<string>("")

  const loadWhatIfForMeasure = useCallback(async (
    currentCapabilities: StarCapabilities,
    measureId: string,
    gaps: string[],
    options?: { forceFresh?: boolean },
  ): Promise<StarWhatIfResult | null> => {
    if (!measureId) {
      setWhatIfResult(null)
      return null
    }

    setLoadingWhatIf(true)
    setErrorMessage("")
    try {
      const result = await loadStarWhatIf(currentCapabilities, measureId, gaps, options)
      setWhatIfResult(result)
      setSelectedGapIds(result.selectedGapIds)
      return result
    } catch (error) {
      setWhatIfResult(null)
      setErrorMessage(toRequestErrorMessage(error))
      return null
    } finally {
      setLoadingWhatIf(false)
    }
  }, [])

  const initialize = useCallback(async (): Promise<void> => {
    setLoadingSetup(true)
    setSetupError(null)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const discovered = await discoverStarCapabilities()
      setCapabilities(discovered)

      setStatusMessage("Refreshing measure calculation...")
      await refreshStarCalculationData()

      setLoadingOverview(true)
      try {
        const nextOverview = await loadStarOverview(discovered)
        setOverview(nextOverview)

        const measureId = firstMeasureId(nextOverview)
        setSelectedMeasureId(measureId)
        setSelectedGapIds([])
        setCalculationResult(null)
        setComparisonResult(null)

        setLoadingSetup(false)

        if (measureId) {
          void loadWhatIfForMeasure(discovered, measureId, [])
        } else {
          setWhatIfResult(null)
        }
        setStatusMessage("Star data refreshed from latest calculation.")
      } finally {
        setLoadingOverview(false)
      }
    } catch (error) {
      setCapabilities(null)
      setSetupError(toRequestErrorMessage(error))
    } finally {
      setLoadingSetup(false)
    }
  }, [loadWhatIfForMeasure])

  useEffect(() => {
    void initialize()
  }, [initialize])

  const refreshOverview = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      await initialize()
      return
    }

    setLoadingOverview(true)
    setErrorMessage("")
    try {
      setStatusMessage("Refreshing measure calculation...")
      await refreshStarCalculationData()

      const nextOverview = await loadStarOverview(capabilities, {
        forceFresh: true,
        measureId: selectedMeasureId || null,
        selectedGapIds,
      })
      setOverview(nextOverview)

      const keepSelected = nextOverview.measureCards.some((row) => row.measureId === selectedMeasureId)
        || nextOverview.measures.some((row) => row.measureId === selectedMeasureId)
      const measureId = keepSelected ? selectedMeasureId : firstMeasureId(nextOverview)

      setSelectedMeasureId(measureId)
      setCalculationResult(null)
      setComparisonResult(null)

      if (measureId) {
        await loadWhatIfForMeasure(capabilities, measureId, keepSelected ? selectedGapIds : [], { forceFresh: true })
      } else {
        setWhatIfResult(null)
      }
      setStatusMessage("Star data refreshed from latest calculation.")
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingOverview(false)
    }
  }, [capabilities, initialize, loadWhatIfForMeasure, selectedGapIds, selectedMeasureId])

  const selectMeasure = useCallback(async (measureId: string): Promise<void> => {
    if (!capabilities) {
      return
    }

    setSelectedMeasureId(measureId)
    setSelectedGapIds([])
    setCalculationResult(null)
    setComparisonResult(null)
    setStatusMessage("")

    await loadWhatIfForMeasure(capabilities, measureId, [])
  }, [capabilities, loadWhatIfForMeasure])

  const toggleGap = useCallback((gapId: string): void => {
    setSelectedGapIds((previous) => {
      if (previous.includes(gapId)) {
        return previous.filter((item) => item !== gapId)
      }
      return [...previous, gapId]
    })
  }, [])

  const selectAllOpenGaps = useCallback((): void => {
    const openGaps = (whatIfResult?.gaps ?? []).filter((gap) => gap.eligibleForClosure).map((gap) => gap.gapId)
    setSelectedGapIds(toUniqueIds(openGaps))
  }, [whatIfResult?.gaps])

  const clearGapSelection = useCallback((): void => {
    setSelectedGapIds([])
  }, [])

  const runProjection = useCallback(async (): Promise<void> => {
    if (!capabilities || !selectedMeasureId) {
      return
    }

    setStatusMessage("")
    setErrorMessage("")
    const projection = await loadWhatIfForMeasure(capabilities, selectedMeasureId, selectedGapIds, { forceFresh: true })
    if (capabilities.mode === "dashboard") {
      const overviewWithSelection = await loadStarOverview(capabilities, {
        forceFresh: true,
        measureId: selectedMeasureId,
        selectedGapIds: projection?.selectedGapIds ?? selectedGapIds,
      })
      setOverview(overviewWithSelection)
    } else if (projection) {
      setOverview((current) => applyProjectionToOverview(current, projection))
    }
    setComparisonResult(null)
    setStatusMessage("Projection refreshed.")
  }, [capabilities, loadWhatIfForMeasure, selectedGapIds, selectedMeasureId])

  const calculateCurrentStar = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      return
    }

    const baselineMeasureId = whatIfResult?.measureId ?? selectedMeasureId
    const baselineMeasurementYear = whatIfResult?.measurementYear
      ?? overview?.measureCards.find((row) => row.measureId === selectedMeasureId)?.measurementYear
    const denominator = whatIfResult?.baselineDenominator
      ?? overview?.measureCards.find((row) => row.measureId === selectedMeasureId)?.total
      ?? 0
    const rate = whatIfResult?.baselineRate
      ?? overview?.measureCards.find((row) => row.measureId === selectedMeasureId)?.baselineRate
      ?? null

    if (!baselineMeasureId || !baselineMeasurementYear) {
      setErrorMessage("No measure data available for current star calculation.")
      return
    }

    if (denominator <= 0) {
      setErrorMessage(NO_GAPS_STAR_MESSAGE)
      return
    }

    setRunningCalculation(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const result = await runStarCalculation(capabilities, {
        measureId: baselineMeasureId,
        measurementYear: baselineMeasurementYear,
        cmsRatingYear: whatIfResult?.cmsRatingYear ?? overview?.cmsRatingYear ?? new Date().getFullYear(),
        rate,
        denominator,
      })
      setCalculationResult(result)
      setStatusMessage("Current star calculation completed.")
    } catch (error) {
      const message = toRequestErrorMessage(error)
      setErrorMessage(isZeroDenominatorMessage(message) ? NO_GAPS_STAR_MESSAGE : message)
    } finally {
      setRunningCalculation(false)
    }
  }, [
    capabilities,
    overview?.cmsRatingYear,
    overview?.measureCards,
    selectedMeasureId,
    whatIfResult?.baselineDenominator,
    whatIfResult?.baselineRate,
    whatIfResult?.cmsRatingYear,
    whatIfResult?.measureId,
    whatIfResult?.measurementYear,
  ])

  const compareBeforeAfter = useCallback(async (): Promise<void> => {
    if (!capabilities || !selectedMeasureId) {
      return
    }

    setRunningComparison(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const projection = await loadStarWhatIf(capabilities, selectedMeasureId, selectedGapIds, { forceFresh: true })
      setWhatIfResult(projection)
      setSelectedGapIds(projection.selectedGapIds)

      if (capabilities.mode === "dashboard") {
        const overviewWithSelection = await loadStarOverview(capabilities, {
          forceFresh: true,
          measureId: selectedMeasureId,
          selectedGapIds: projection.selectedGapIds,
        })
        setOverview(overviewWithSelection)
      } else {
        setOverview((current) => applyProjectionToOverview(current, projection))
      }

      if (projection.baselineDenominator <= 0) {
        setErrorMessage(NO_GAPS_STAR_MESSAGE)
        return
      }

      const comparison = await runStarComparison(capabilities, {
        measureId: projection.measureId,
        measurementYear: projection.measurementYear,
        cmsRatingYear: projection.cmsRatingYear ?? overview?.cmsRatingYear ?? new Date().getFullYear(),
        denominator: projection.baselineDenominator,
        beforeRate: projection.baselineRate,
        afterRate: projection.projectedRate,
      })
      setComparisonResult(comparison)
      setStatusMessage("Before and after star comparison completed.")
    } catch (error) {
      const message = toRequestErrorMessage(error)
      setErrorMessage(isZeroDenominatorMessage(message) ? NO_GAPS_STAR_MESSAGE : message)
    } finally {
      setRunningComparison(false)
    }
  }, [capabilities, overview?.cmsRatingYear, selectedGapIds, selectedMeasureId])

  const selectedMeasureCard = useMemo(
    () => overview?.measureCards.find((row) => row.measureId === selectedMeasureId) ?? null,
    [overview?.measureCards, selectedMeasureId],
  )

  return {
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
    retrySetup: initialize,
    refreshOverview,
    selectMeasure,
    toggleGap,
    selectAllOpenGaps,
    clearGapSelection,
    runProjection,
    calculateCurrentStar,
    compareBeforeAfter,
  }
}
