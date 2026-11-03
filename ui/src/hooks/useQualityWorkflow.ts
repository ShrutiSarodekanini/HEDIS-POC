import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type {
  QualityCapabilities,
  QualityCoverageResponse,
  QualityOverview,
  QualityRunDetail,
  QualityRunSummary,
} from "../models/quality"
import {
  clearQualityDataCaches,
  discoverQualityCapabilities,
  loadCoverageReport,
  loadQualityOverview,
  loadQualityRunDetail,
  loadQualityRuns,
  triggerQualityRun,
} from "../services/quality.service"
import { toRequestErrorMessage } from "../services/http"

interface CoverageFilters {
  payer: string
  compliance: string
  startDate: string
  endDate: string
}

const DEFAULT_COVERAGE_FILTERS: CoverageFilters = {
  payer: "All",
  compliance: "All",
  startDate: "",
  endDate: "",
}

export function useQualityWorkflow() {
  const initializedRef = useRef(false)
  const runsRequestRef = useRef(0)
  const detailRequestRef = useRef(0)

  const [capabilities, setCapabilities] = useState<QualityCapabilities | null>(null)
  const [overview, setOverview] = useState<QualityOverview | null>(null)
  const [runs, setRuns] = useState<QualityRunSummary[]>([])
  const [runDetail, setRunDetail] = useState<QualityRunDetail | null>(null)

  const [loadingSetup, setLoadingSetup] = useState<boolean>(true)
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false)
  const [loadingRunDetail, setLoadingRunDetail] = useState<boolean>(false)
  const [loadingCoverage, setLoadingCoverage] = useState<boolean>(false)
  const [runningChecks, setRunningChecks] = useState<boolean>(false)

  const [setupError, setSetupError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [statusMessage, setStatusMessage] = useState<string>("")

  const [selectedBatchId, setSelectedBatchId] = useState<string>("")
  const [selectedRunId, setSelectedRunId] = useState<string>("")
  const [selectedMeasureId, setSelectedMeasureId] = useState<string>("All")
  const [selectedSeverity, setSelectedSeverity] = useState<string>("All")

  const [coverageFilters, setCoverageFilters] = useState<CoverageFilters>(DEFAULT_COVERAGE_FILTERS)
  const [coverage, setCoverage] = useState<QualityCoverageResponse | null>(null)

  const [runsRefreshToken, setRunsRefreshToken] = useState(0)
  const [detailRefreshToken, setDetailRefreshToken] = useState(0)

  const syncOverview = useCallback((nextOverview: QualityOverview): string => {
    setOverview(nextOverview)

    const nextBatchId = nextOverview.batches[0]?.batch_id ?? ""
    const batchStillExists = nextOverview.batches.some((batch) => batch.batch_id === selectedBatchId)
    const resolvedBatchId = batchStillExists ? selectedBatchId : nextBatchId
    setSelectedBatchId(resolvedBatchId)

    const measureStillExists = selectedMeasureId === "All"
      || nextOverview.measures.some((measure) => measure.measure_id === selectedMeasureId)
    if (!measureStillExists) {
      setSelectedMeasureId("All")
    }

    if (!resolvedBatchId) {
      setRuns([])
      setSelectedRunId("")
      setRunDetail(null)
    }

    return resolvedBatchId
  }, [selectedBatchId, selectedMeasureId])

  const initialize = useCallback(async (): Promise<void> => {
    setLoadingSetup(true)
    setSetupError(null)
    setErrorMessage("")
    setStatusMessage("")
    setCoverage(null)

    try {
      const discovered = await discoverQualityCapabilities()
      setCapabilities(discovered)

      setLoadingOverview(true)
      const nextOverview = await loadQualityOverview(discovered, { forceFresh: true })
      const nextBatchId = syncOverview(nextOverview)

      if (nextBatchId) {
        setRunsRefreshToken((value) => value + 1)
      }
    } catch (error) {
      setSetupError(toRequestErrorMessage(error))
      setCapabilities(null)
      setOverview(null)
      setRuns([])
      setRunDetail(null)
    } finally {
      setLoadingOverview(false)
      setLoadingSetup(false)
    }
  }, [syncOverview])

  useEffect(() => {
    if (initializedRef.current) {
      return
    }
    initializedRef.current = true
    void initialize()
  }, [initialize])

  const refreshOverview = useCallback(async (options?: { forceFresh?: boolean }): Promise<void> => {
    if (!capabilities) {
      await initialize()
      return
    }

    setLoadingOverview(true)
    setErrorMessage("")
    try {
      const forceFresh = options?.forceFresh ?? true
      const nextOverview = await loadQualityOverview(capabilities, { forceFresh })
      const resolvedBatchId = syncOverview(nextOverview)

      if (resolvedBatchId) {
        setRunsRefreshToken((value) => value + 1)
        setDetailRefreshToken((value) => value + 1)
      }
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingOverview(false)
    }
  }, [capabilities, initialize, syncOverview])

  const reloadRuns = useCallback(async (options?: { silent?: boolean; forceFresh?: boolean }): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration" || !selectedBatchId) {
      setRuns([])
      setSelectedRunId("")
      setRunDetail(null)
      return
    }

    const silent = Boolean(options?.silent)
    const forceFresh = Boolean(options?.forceFresh)
    const requestId = runsRequestRef.current + 1
    runsRequestRef.current = requestId

    if (!silent) {
      setLoadingOverview(true)
    }
    setErrorMessage("")
    try {
      const nextRuns = await loadQualityRuns(selectedBatchId, { forceFresh })
      if (requestId !== runsRequestRef.current) {
        return
      }

      setRuns(nextRuns)
      const keepRun = nextRuns.some((run) => String(run.run_id) === selectedRunId)
      const nextRunId = keepRun ? selectedRunId : (nextRuns[0] ? String(nextRuns[0].run_id) : "")
      setSelectedRunId(nextRunId)

      if (!nextRunId) {
        setRunDetail(null)
      }
    } catch (error) {
      if (requestId !== runsRequestRef.current) {
        return
      }
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      if (!silent) {
        setLoadingOverview(false)
      }
    }
  }, [capabilities, selectedBatchId, selectedRunId])

  const reloadRunDetail = useCallback(async (options?: { forceFresh?: boolean }): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration" || !selectedRunId) {
      setRunDetail(null)
      return
    }

    const forceFresh = Boolean(options?.forceFresh)
    const requestId = detailRequestRef.current + 1
    detailRequestRef.current = requestId

    setLoadingRunDetail(true)
    setErrorMessage("")
    try {
      const detail = await loadQualityRunDetail(Number(selectedRunId), selectedSeverity, { forceFresh })
      if (requestId !== detailRequestRef.current) {
        return
      }
      setRunDetail(detail)
    } catch (error) {
      if (requestId !== detailRequestRef.current) {
        return
      }
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingRunDetail(false)
    }
  }, [capabilities, selectedRunId, selectedSeverity])

  useEffect(() => {
    void reloadRuns()
  }, [reloadRuns, runsRefreshToken])

  useEffect(() => {
    void reloadRunDetail()
  }, [reloadRunDetail, detailRefreshToken])

  const runChecksNow = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      return
    }

    setRunningChecks(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      if (capabilities.mode !== "integration") {
        throw new Error("Data quality endpoints are unavailable in this backend profile.")
      }
      if (!selectedBatchId) {
        throw new Error("No batch has been loaded yet.")
      }

      const result = await triggerQualityRun(selectedBatchId, selectedMeasureId)
      setStatusMessage(result.message)
      await refreshOverview({ forceFresh: true })
      setRunsRefreshToken((value) => value + 1)
      setDetailRefreshToken((value) => value + 1)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningChecks(false)
    }
  }, [capabilities, refreshOverview, selectedBatchId, selectedMeasureId])

  const refreshCoverage = useCallback(async (options?: { forceFresh?: boolean }): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration") {
      return
    }

    setLoadingCoverage(true)
    setErrorMessage("")
    try {
      const report = await loadCoverageReport(coverageFilters, options)
      setCoverage(report)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingCoverage(false)
    }
  }, [capabilities, coverageFilters])

  const setCoverageFilter = useCallback((key: keyof CoverageFilters, value: string): void => {
    setCoverageFilters((previous) => ({
      ...previous,
      [key]: value,
    }))
  }, [])

  const selectBatch = useCallback((batchId: string): void => {
    setSelectedBatchId(batchId)
    setSelectedRunId("")
    setRunDetail(null)
    setStatusMessage("")
  }, [])

  const selectRun = useCallback((runId: string): void => {
    setSelectedRunId(runId)
    setStatusMessage("")
  }, [])

  const selectSeverity = useCallback((severity: string): void => {
    setSelectedSeverity(severity)
  }, [])

  const forceRunDetailRefresh = useCallback((): void => {
    setDetailRefreshToken((value) => value + 1)
  }, [])

  const clearViewCaches = useCallback((): void => {
    clearQualityDataCaches(selectedBatchId || undefined)
  }, [selectedBatchId])

  const severityBreakdown = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const anomaly of runDetail?.anomalies ?? []) {
      const key = String(anomaly.severity ?? "unknown").toLowerCase()
      counts[key] = (counts[key] ?? 0) + 1
    }
    return counts
  }, [runDetail?.anomalies])

  return {
    capabilities,
    overview,
    runs,
    runDetail,
    coverage,
    coverageFilters,
    loadingSetup,
    loadingOverview,
    loadingRunDetail,
    loadingCoverage,
    runningChecks,
    setupError,
    errorMessage,
    statusMessage,
    selectedBatchId,
    selectedRunId,
    selectedMeasureId,
    selectedSeverity,
    severityBreakdown,
    setSelectedBatchId: selectBatch,
    setSelectedRunId: selectRun,
    setSelectedMeasureId,
    setSelectedSeverity: selectSeverity,
    setCoverageFilter,
    retrySetup: initialize,
    refreshOverview,
    runChecksNow,
    refreshCoverage,
    forceRunDetailRefresh,
    clearViewCaches,
  }
}
