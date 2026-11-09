import { useCallback, useEffect, useMemo, useState } from "react"

import type {
  EvidenceCapabilities,
  EvidenceGapDetail,
  EvidenceLatestRun,
  EvidenceOverview,
  EvidenceQueueItem,
  EvidenceScopeGap,
} from "../models/evidence"
import { loadAuthSession } from "../services/authSession"
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

export function useEvidenceWorkflow() {
  const [capabilities, setCapabilities] = useState<EvidenceCapabilities | null>(null)
  const [overview, setOverview] = useState<EvidenceOverview | null>(null)
  const [scopeGapDetail, setScopeGapDetail] = useState<EvidenceGapDetail | null>(null)
  const [latestRun, setLatestRun] = useState<EvidenceLatestRun | null>(null)

  const [selectedScopeGapId, setSelectedScopeGapId] = useState<string>("")
  const [selectedProposalId, setSelectedProposalId] = useState<string>("")

  // Display only: the backend records the signed-in user from the session token.
  const reviewerEmail = loadAuthSession()?.user.email ?? ""
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

  const submitDecision = useCallback(async (decision: "confirm" | "reject", proposalId?: string): Promise<void> => {
    // State set in the same click is not visible yet, so callers may pass the proposal explicitly.
    const targetProposalId = proposalId ?? selectedQueueItem?.proposalId
    if (!capabilities || !targetProposalId) {
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
      const result = await submitEvidenceDecision(capabilities, targetProposalId, {
        decision,
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
  }, [capabilities, decisionReason, refreshOverview, selectedQueueItem?.proposalId])

  const runExport = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      return
    }

    setRunningExport(true)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const result = capabilities.mode === "integration"
        ? await exportSupplementalEvidence(capabilities)
        : await runEvidenceDashboardSeed()
      setStatusMessage(result.message)
      await refreshOverview(capabilities)
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setRunningExport(false)
    }
  }, [capabilities, refreshOverview])

  return {
    capabilities,
    overview,
    scopeGapDetail,
    latestRun,
    selectedScopeGap,
    selectedQueueItem,
    selectedScopeGapId,
    selectedProposalId,
    reviewerEmail,
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
    setDecisionReason,
    runExtractionNow,
    submitDecision,
    runExport,
  }
}
