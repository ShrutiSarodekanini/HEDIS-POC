import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import type {
  PrioritizationCapabilities,
  PrioritizationGapDetail,
  PrioritizationOverview,
  PrioritizationWorklistFilters,
  PrioritizationWorklistItem,
} from "../models/prioritization"
import {
  discoverPrioritizationCapabilities,
  loadPrioritizationGapDetail,
  loadPrioritizationOverview,
  loadPrioritizationWorklist,
} from "../services/prioritization.service"
import { toRequestErrorMessage } from "../services/http"

const DEFAULT_FILTERS: PrioritizationWorklistFilters = {
  role: "",
  tier: "",
  measure: "",
  limit: 100,
}

export function usePrioritizationWorkflow() {
  const [capabilities, setCapabilities] = useState<PrioritizationCapabilities | null>(null)
  const [overview, setOverview] = useState<PrioritizationOverview | null>(null)
  const [worklist, setWorklist] = useState<PrioritizationWorklistItem[]>([])
  const [gapDetail, setGapDetail] = useState<PrioritizationGapDetail | null>(null)

  const [filters, setFilters] = useState<PrioritizationWorklistFilters>(DEFAULT_FILTERS)
  const [selectedGapId, setSelectedGapId] = useState<string>("")

  const [loadingSetup, setLoadingSetup] = useState<boolean>(true)
  const [loadingOverview, setLoadingOverview] = useState<boolean>(false)
  const [loadingWorklist, setLoadingWorklist] = useState<boolean>(false)
  const [loadingGapDetail, setLoadingGapDetail] = useState<boolean>(false)

  const [setupError, setSetupError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [statusMessage, setStatusMessage] = useState<string>("")

  const skipNextWorklistReloadRef = useRef<boolean>(false)

  const refreshOverview = useCallback(async (mode: PrioritizationCapabilities): Promise<void> => {
    setLoadingOverview(true)
    setErrorMessage("")

    try {
      const nextOverview = await loadPrioritizationOverview(mode)
      setOverview(nextOverview)

      if (filters.measure && !nextOverview.measures.some((item) => item.measureId === filters.measure)) {
        setFilters((previous) => ({
          ...previous,
          measure: "",
        }))
      }
    } catch (error) {
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingOverview(false)
    }
  }, [filters.measure])

  const refreshWorklist = useCallback(async (mode: PrioritizationCapabilities): Promise<void> => {
    setLoadingWorklist(true)
    setErrorMessage("")

    try {
      const nextRows = await loadPrioritizationWorklist(mode, filters)
      setWorklist(nextRows)

      if (nextRows.length === 0) {
        setSelectedGapId("")
        setGapDetail(null)
      } else if (!nextRows.some((item) => item.gapId === selectedGapId)) {
        setSelectedGapId(nextRows[0].gapId)
      }
    } catch (error) {
      setWorklist([])
      setSelectedGapId("")
      setGapDetail(null)
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingWorklist(false)
    }
  }, [filters, selectedGapId])

  const refreshGapDetail = useCallback(async (mode: PrioritizationCapabilities): Promise<void> => {
    if (!selectedGapId) {
      setGapDetail(null)
      return
    }

    const selectedGap = worklist.find((item) => item.gapId === selectedGapId)
    if (!selectedGap) {
      setGapDetail(null)
      return
    }

    setLoadingGapDetail(true)
    setErrorMessage("")

    try {
      const detail = await loadPrioritizationGapDetail(mode, selectedGapId, selectedGap.measureId)
      setGapDetail(detail)
    } catch (error) {
      setGapDetail(null)
      setErrorMessage(toRequestErrorMessage(error))
    } finally {
      setLoadingGapDetail(false)
    }
  }, [selectedGapId, worklist])

  const initialize = useCallback(async (): Promise<void> => {
    setLoadingSetup(true)
    setSetupError(null)
    setErrorMessage("")
    setStatusMessage("")

    try {
      const discovered = await discoverPrioritizationCapabilities()
      setCapabilities(discovered)
      setLoadingSetup(false)
      setStatusMessage("Loading prioritization data...")

      skipNextWorklistReloadRef.current = true
      await Promise.all([
        refreshOverview(discovered),
        refreshWorklist(discovered),
      ])
      setStatusMessage("Prioritization data loaded.")
    } catch (error) {
      setCapabilities(null)
      setSetupError(toRequestErrorMessage(error))
      setLoadingSetup(false)
    } finally {
      setLoadingSetup(false)
    }
  }, [refreshOverview, refreshWorklist])

  useEffect(() => {
    void initialize()
  }, [initialize])

  useEffect(() => {
    if (!capabilities) {
      return
    }

    if (skipNextWorklistReloadRef.current) {
      skipNextWorklistReloadRef.current = false
      return
    }

    void refreshWorklist(capabilities)
  }, [capabilities, filters.role, filters.tier, filters.measure, filters.limit, refreshWorklist])

  useEffect(() => {
    if (!capabilities) {
      return
    }
    void refreshGapDetail(capabilities)
  }, [capabilities, refreshGapDetail, selectedGapId])

  const refreshAll = useCallback(async (): Promise<void> => {
    if (!capabilities) {
      await initialize()
      return
    }

    await Promise.all([
      refreshOverview(capabilities),
      refreshWorklist(capabilities),
    ])
    setStatusMessage("Prioritization data refreshed.")
  }, [capabilities, initialize, refreshOverview, refreshWorklist])

  const setFilter = useCallback(<K extends keyof PrioritizationWorklistFilters>(
    key: K,
    value: PrioritizationWorklistFilters[K],
  ): void => {
    setFilters((previous) => ({
      ...previous,
      [key]: value,
    }))
  }, [])

  const selectGap = useCallback((gapId: string): void => {
    setSelectedGapId(gapId)
  }, [])

  const channelMix = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of worklist) {
      const key = item.channel ?? "unknown"
      counts[key] = (counts[key] ?? 0) + 1
    }
    return counts
  }, [worklist])

  return {
    capabilities,
    overview,
    worklist,
    gapDetail,
    filters,
    selectedGapId,
    channelMix,
    loadingSetup,
    loadingOverview,
    loadingWorklist,
    loadingGapDetail,
    setupError,
    errorMessage,
    statusMessage,
    retrySetup: initialize,
    refreshAll,
    setFilter,
    selectGap,
  }
}
