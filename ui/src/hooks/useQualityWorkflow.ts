import { useCallback, useEffect, useMemo, useState } from "react"

import type {
  QualityCapabilities,
  QualityCoverageResponse,
  QualityOverview,
  QualityRunDetail,
  QualityRunSummary,
} from "../models/quality"
import {
  discoverQualityCapabilities,
  loadCoverageReport,
  loadQualityOverview,
  loadQualityRunDetail,
  loadQualityRuns,
  runAutomationQualitySummary,
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

const RUN_POLL_MS = 3000
const OVERVIEW_REFRESH_MS = 15000

export function useQualityWorkflow() {
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

  const initialize = useCallback(async (): Promise<void> => {
    setLoadingSetup(true)
    setSetupError(null)
    setErrorMessage("")

    try {
      const discovered = await discoverQualityCapabilities()
      setCapabilities(discovered)
      setLoadingSetup(false)

      await (async () => {
        setLoadingOverview(true)
        try {
          const nextOverview = await loadQualityOverview(discovered)
          setOverview(nextOverview)

          const nextBatchId = nextOverview.batches[0]?.batch_id ?? ""
          setSelectedBatchId(nextBatchId)
          setSelectedMeasureId("All")
          setSelectedRunId("")
          setRuns([])
          setRunDetail(null)
        } catch (error) {
          setErrorMessage(toRequestErrorMessage(error))
        } finally {
          setLoadingOverview(false)
        }
      })()
    } catch (error) {
      setLoadingSetup(false)
      setSetupError(toRequestErrorMessage(error))
      setCapabilities(null)
    }
  }, [])

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
      const nextOverview = await loadQualityOverview(capabilities)
      setOverview(nextOverview)

      const keepBatch = nextOverview.batches.some((batch) => batch.batch_id === selectedBatchId)
      const nextBatchId = keepBatch ? selectedBatchId : (nextOverview.batches[0]?.batch_id ?? "")
      setSelectedBatchId(nextBatchId)

      if (capabilities.mode === "integration" && nextBatchId) {
        const nextRuns = await loadQualityRuns(nextBatchId)
        setRuns(nextRuns)
        const keepRun = nextRuns.some((run) => String(run.run_id) === selectedRunId)
        const nextRunId = keepRun ? selectedRunId : (nextRuns[0] ? String(nextRuns[0].run_id) : "")
        setSelectedRunId(nextRunId)

        if (!nextRunId) {
          setRunDetail(null)
        } else if (nextRunId === selectedRunId) {
          setLoadingRunDetail(true)
          try {
            const detail = await loadQualityRunDetail(Number(nextRunId), selectedSeverity)
            setRunDetail(detail)
          } finally {
            setLoadingRunDetail(false)
          }
        }
      } else {
        setRuns([])
        setRunDetail(null)
      }
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingOverview(false)
    }
  }, [capabilities, initialize, selectedBatchId, selectedRunId, selectedSeverity])

  const reloadRuns = useCallback(async (options?: { silent?: boolean }): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration" || !selectedBatchId) {
      return
    }

    const silent = Boolean(options?.silent)

    if (!silent) {
      setLoadingOverview(true)
    }
    setErrorMessage("")
    try {
      const nextRuns = await loadQualityRuns(selectedBatchId)
      setRuns(nextRuns)
      const keepRun = nextRuns.some((run) => String(run.run_id) === selectedRunId)
      const nextRunId = keepRun ? selectedRunId : (nextRuns[0] ? String(nextRuns[0].run_id) : "")
      setSelectedRunId(nextRunId)

      if (!nextRunId) {
        setRunDetail(null)
      } else if (nextRunId === selectedRunId) {
        setLoadingRunDetail(true)
        try {
          const detail = await loadQualityRunDetail(Number(nextRunId), selectedSeverity)
          setRunDetail(detail)
        } finally {
          setLoadingRunDetail(false)
        }
      }
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      if (!silent) {
        setLoadingOverview(false)
      }
    }
  }, [capabilities, selectedBatchId, selectedRunId, selectedSeverity])

  const reloadRunDetail = useCallback(async (): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration" || !selectedRunId) {
      setRunDetail(null)
      return
    }

    setLoadingRunDetail(true)
    setErrorMessage("")
    try {
      const detail = await loadQualityRunDetail(Number(selectedRunId), selectedSeverity)
      setRunDetail(detail)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingRunDetail(false)
    }
  }, [capabilities, selectedRunId, selectedSeverity])

  useEffect(() => {
    void reloadRuns()
  }, [reloadRuns])

  useEffect(() => {
    void reloadRunDetail()
  }, [reloadRunDetail])

  useEffect(() => {
    if (!capabilities || capabilities.mode !== "integration" || !selectedRunId) {
      return
    }

    const selected = runs.find((run) => String(run.run_id) === selectedRunId)
    if (!selected || String(selected.status).toLowerCase() !== "running") {
      return
    }

    const timer = window.setInterval(() => {
      void reloadRuns({ silent: true })
    }, RUN_POLL_MS)

    return () => {
      window.clearInterval(timer)
    }
  }, [capabilities, reloadRuns, runs, selectedRunId])

  useEffect(() => {
    if (!capabilities || runningChecks) {
      return
    }

    const timer = window.setInterval(() => {
      void refreshOverview()
    }, OVERVIEW_REFRESH_MS)

    return () => {
      window.clearInterval(timer)
    }
  }, [capabilities, refreshOverview, runningChecks])

  const runChecksNow = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      return
    }

    setRunningChecks(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      if (capabilities.mode === "integration") {
        if (!selectedBatchId) {
          throw new Error("No batch has been loaded yet.")
        }
        const result = await triggerQualityRun(selectedBatchId, selectedMeasureId)
        setStatusMessage(result.message)
        await refreshOverview()
      } else if (capabilities.mode === "automation") {
        const result = await runAutomationQualitySummary()
        setStatusMessage(result.message)
        await refreshOverview()
      }
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningChecks(false)
    }
  }, [capabilities, refreshOverview, selectedBatchId, selectedMeasureId])

  const refreshCoverage = useCallback(async (): Promise<void> => {
    if (!capabilities || capabilities.mode !== "integration") {
      return
    }

    setLoadingCoverage(true)
    setErrorMessage("")
    try {
      const report = await loadCoverageReport(coverageFilters)
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
    setSelectedBatchId,
    setSelectedRunId,
    setSelectedMeasureId,
    setSelectedSeverity,
    setCoverageFilter,
    retrySetup: initialize,
    refreshOverview,
    runChecksNow,
    refreshCoverage,
  }
}
