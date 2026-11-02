import { useCallback, useEffect, useMemo, useState } from "react"

import { envConfig } from "../config/env"
import type {
  EvidenceCapabilities,
  EvidenceGapDetail,
  EvidenceLatestRun,
  EvidenceOverview,
  EvidenceQueueItem,
  EvidenceScopeGap,
} from "../models/evidence"
import { toRequestErrorMessage } from "../services/http"
import {
  clearEvidenceServiceCaches,
  discoverEvidenceCapabilities,
  exportSupplementalEvidence,
  loadEvidenceGapDetail,
  loadEvidenceOverview,
  runEvidenceDashboardProcess,
  runEvidenceDashboardSeed,
  runEvidenceExtraction,
  submitEvidenceDecision,
} from "../services/evidence.service"

const REVIEWER_STORAGE_KEY = "hedis.evidence.reviewer"

const sanitizeDefaultReviewer = (value: string): string => {
  const normalized = value.trim().toLowerCase().replace(/\s+/g, ".")
  return normalized.replace(/[^a-z0-9._@-]/g, "")
}

const readStoredReviewer = (): string => {
  try {
    const fromStorage = localStorage.getItem(REVIEWER_STORAGE_KEY)
    if (fromStorage && fromStorage.trim().length > 0) {
      return fromStorage.trim()
    }
  } catch {
    // Storage can fail in strict browser privacy mode.
  }

  const fallback = sanitizeDefaultReviewer(envConfig.defaultUserName)
  return fallback.length > 0 ? fallback : "reviewer.user"
}

const rememberReviewer = (value: string): void => {
  try {
    localStorage.setItem(REVIEWER_STORAGE_KEY, value)
  } catch {
    // Ignore storage errors; reviewer stays in memory.
  }
}

export function useEvidenceWorkflow() {
  const [capabilities, setCapabilities] = useState<EvidenceCapabilities | null>(null)
  const [overview, setOverview] = useState<EvidenceOverview | null>(null)
  const [scopeGapDetail, setScopeGapDetail] = useState<EvidenceGapDetail | null>(null)
  const [latestRun, setLatestRun] = useState<EvidenceLatestRun | null>(null)

  const [selectedScopeGapId, setSelectedScopeGapId] = useState<string>("")
  const [selectedProposalId, setSelectedProposalId] = useState<string>("")

  const [reviewerId, setReviewerId] = useState<string>(readStoredReviewer)
  const [decisionReason, setDecisionReason] = useState<string>("")

  const [loadingSetup, setLoadingSetup] = useState<boolean>(true)
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false)
  const [loadingGapDetail, setLoadingGapDetail] = useState<boolean>(false)
  const [runningExtraction, setRunningExtraction] = useState<boolean>(false)
  const [runningDecision, setRunningDecision] = useState<boolean>(false)
  const [runningExport, setRunningExport] = useState<boolean>(false)

  const [setupError, setSetupError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [statusMessage, setStatusMessage] = useState<string>("")

  const applySelection = useCallback((nextOverview: EvidenceOverview): void => {
    if (!nextOverview.scopeGaps.length) {
      setSelectedScopeGapId("")
      setScopeGapDetail(null)
    } else {
      setSelectedScopeGapId((previous) => {
        if (nextOverview.scopeGaps.some((item) => item.gapId === previous)) {
          return previous
        }
        return nextOverview.scopeGaps[0].gapId
      })
    }

    if (!nextOverview.queue.length) {
      setSelectedProposalId("")
    } else {
      setSelectedProposalId((previous) => {
        if (nextOverview.queue.some((item) => item.proposalId === previous)) {
          return previous
        }
        return nextOverview.queue[0].proposalId
      })
    }
  }, [])

  const refreshOverview = useCallback(async (mode: EvidenceCapabilities): Promise<void> => {
    setLoadingOverview(true)
    setErrorMessage("")

    try {
      const nextOverview = await loadEvidenceOverview(mode)
      setOverview(nextOverview)
      applySelection(nextOverview)
    } catch (error) {
      setOverview(null)
      setScopeGapDetail(null)
      setLatestRun(null)
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingOverview(false)
    }
  }, [applySelection])

  const initialize = useCallback(async (): Promise<void> => {
    setLoadingSetup(true)
    setSetupError(null)
    setErrorMessage("")
    setStatusMessage("")

    try {
      clearEvidenceServiceCaches()
      const discovered = await discoverEvidenceCapabilities()
      setCapabilities(discovered)
      await refreshOverview(discovered)
      setStatusMessage("Evidence review data loaded.")
    } catch (error) {
      setCapabilities(null)
      setOverview(null)
      setScopeGapDetail(null)
      setLatestRun(null)
      setSetupError(toRequestErrorMessage(error))
    } finally {
      setLoadingSetup(false)
    }
  }, [refreshOverview])

  useEffect(() => {
    void initialize()
  }, [initialize])

  const selectedQueueItem = useMemo<EvidenceQueueItem | null>(
    () => overview?.queue.find((row) => row.proposalId === selectedProposalId) ?? null,
    [overview?.queue, selectedProposalId],
  )

  const selectedScopeGap = useMemo<EvidenceScopeGap | null>(
    () => overview?.scopeGaps.find((row) => row.gapId === selectedScopeGapId) ?? null,
    [overview?.scopeGaps, selectedScopeGapId],
  )

  useEffect(() => {
    if (!capabilities || !selectedScopeGapId) {
      setScopeGapDetail(null)
      return
    }

    if (capabilities.mode === "dashboard") {
      const selectedScope = overview?.scopeGaps.find((row) => row.gapId === selectedScopeGapId)
      if (!selectedScope) {
        setScopeGapDetail(null)
        return
      }

      const queueByMeasure = (overview?.queue ?? []).filter((row) => row.measureId === selectedScope.measureId)
      setScopeGapDetail({
        gapId: selectedScope.gapId,
        key: selectedScope.key,
        patientId: selectedScope.patientId,
        measureId: selectedScope.measureId,
        measureName: selectedScope.measureName,
        missingElement: selectedScope.missingElement,
        tier: selectedScope.tier,
        deadlineDate: selectedScope.deadlineDate,
        lastRelevantEventDate: null,
        lastRelevantEventDescription: null,
        reviewStatus: selectedScope.reviewStatus,
        pendingCount: selectedScope.pendingCount,
        breakdown: null,
        impact: null,
        documents: [],
        evidence: queueByMeasure,
        aiRuns: [],
      })
      return
    }

    let cancelled = false

    const loadDetail = async (): Promise<void> => {
      setLoadingGapDetail(true)
      try {
        const detail = await loadEvidenceGapDetail(capabilities, selectedScopeGapId)
        if (!cancelled) {
          setScopeGapDetail(detail)
        }
      } catch (error) {
        if (!cancelled) {
          setScopeGapDetail(null)
          setErrorMessage(toRequestErrorMessage(error))
        }
      } finally {
        if (!cancelled) {
          setLoadingGapDetail(false)
        }
      }
    }

    void loadDetail()

    return () => {
      cancelled = true
    }
  }, [capabilities, overview?.queue, overview?.scopeGaps, selectedScopeGapId])

  useEffect(() => {
    if (!latestRun || latestRun.gapId === selectedScopeGapId) {
      return
    }
    setLatestRun(null)
  }, [latestRun, selectedScopeGapId])

  const refreshAll = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      await initialize()
      return
    }

    await refreshOverview(capabilities)
    setStatusMessage("Evidence review data refreshed.")
  }, [capabilities, initialize, refreshOverview])

  const updateReviewer = useCallback((value: string): void => {
    setReviewerId(value)
    rememberReviewer(value)
  }, [])

  const runExtractionNow = useCallback(async (): Promise<void> => {
    if (!capabilities || !selectedScopeGap?.gapId) {
      return
    }

    setRunningExtraction(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const result = capabilities.mode === "integration"
        ? await runEvidenceExtraction(capabilities, selectedScopeGap.gapId)
        : await runEvidenceDashboardProcess()
      setLatestRun(result.run)
      setStatusMessage(result.message)
      await refreshOverview(capabilities)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningExtraction(false)
    }
  }, [capabilities, refreshOverview, selectedScopeGap?.gapId])

  const submitDecision = useCallback(async (decision: "confirm" | "reject"): Promise<void> => {
    if (!capabilities || !selectedQueueItem?.proposalId) {
      return
    }

    const reviewer = reviewerId.trim()
    if (!reviewer) {
      setErrorMessage("Reviewer ID is required.")
      return
    }

    if (decision === "reject" && decisionReason.trim().length === 0) {
      setErrorMessage("Reject requires a reason.")
      return
    }

    setRunningDecision(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const result = await submitEvidenceDecision(capabilities, selectedQueueItem.proposalId, {
        decision,
        reviewer,
        reason: decision === "reject" ? decisionReason.trim() : undefined,
      })
      setDecisionReason("")
      setStatusMessage(result.message)
      await refreshOverview(capabilities)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningDecision(false)
    }
  }, [capabilities, decisionReason, refreshOverview, reviewerId, selectedQueueItem?.proposalId])

  const runExport = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      return
    }

    const reviewer = reviewerId.trim()
    if (capabilities.mode === "integration" && !reviewer) {
      setErrorMessage("Reviewer ID is required for export.")
      return
    }

    setRunningExport(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const result = capabilities.mode === "integration"
        ? await exportSupplementalEvidence(capabilities, reviewer)
        : await runEvidenceDashboardSeed()
      setStatusMessage(result.message)
      await refreshOverview(capabilities)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningExport(false)
    }
  }, [capabilities, refreshOverview, reviewerId])

  return {
    capabilities,
    overview,
    scopeGapDetail,
    latestRun,
    selectedScopeGap,
    selectedQueueItem,
    selectedScopeGapId,
    selectedProposalId,
    reviewerId,
    decisionReason,
    loadingSetup,
    loadingOverview,
    loadingGapDetail,
    runningExtraction,
    runningDecision,
    runningExport,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup: initialize,
    refreshAll,
    setSelectedScopeGapId,
    setSelectedProposalId,
    setReviewerId: updateReviewer,
    setDecisionReason,
    runExtractionNow,
    submitDecision,
    runExport,
  }
}
