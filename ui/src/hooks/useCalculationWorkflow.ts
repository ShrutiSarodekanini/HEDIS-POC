import { useCallback, useEffect, useMemo, useState } from "react"

import type {
  CalculationCapabilities,
  CalculationOverview,
  CalculationPatientRow,
} from "../models/calculation"
import {
  discoverCalculationCapabilities,
  loadCalculationOverview,
  loadCalculationPatients,
  runCalculation,
} from "../services/calculation.service"
import { toRequestErrorMessage } from "../services/http"

interface CalculationState {
  capabilities: CalculationCapabilities | null
  loadingSetup: boolean
  setupError: string | null
  loadingOverview: boolean
  loadingPatients: boolean
  runningCalculation: boolean
  overview: CalculationOverview | null
  selectedMeasureId: string
  selectedMeasureIdsForRun: string[]
  patients: CalculationPatientRow[]
  patientError: string | null
  statusMessage: string
  statusError: boolean
}

const INITIAL_STATE: CalculationState = {
  capabilities: null,
  loadingSetup: true,
  setupError: null,
  loadingOverview: false,
  loadingPatients: false,
  runningCalculation: false,
  overview: null,
  selectedMeasureId: "",
  selectedMeasureIdsForRun: [],
  patients: [],
  patientError: null,
  statusMessage: "",
  statusError: false,
}

const withDefaultSelection = (
  overview: CalculationOverview,
  selectedMeasureId: string,
): string => {
  if (selectedMeasureId && overview.measures.some((measure) => measure.measureId === selectedMeasureId)) {
    return selectedMeasureId
  }
  return overview.measures[0]?.measureId ?? ""
}

export function useCalculationWorkflow() {
  const [state, setState] = useState<CalculationState>(INITIAL_STATE)

  const setStatus = useCallback((message: string, isError = false): void => {
    setState((previous) => ({
      ...previous,
      statusMessage: message,
      statusError: isError,
    }))
  }, [])

  const loadPatientsForMeasure = useCallback(async (
    capabilities: CalculationCapabilities,
    measureId: string,
  ): Promise<void> => {
    if (!measureId) {
      setState((previous) => ({
        ...previous,
        loadingPatients: false,
        patients: [],
        patientError: null,
      }))
      return
    }

    setState((previous) => ({
      ...previous,
      loadingPatients: true,
      patientError: null,
    }))

    try {
      const patients = await loadCalculationPatients(capabilities, measureId)
      setState((previous) => ({
        ...previous,
        loadingPatients: false,
        patients,
        patientError: null,
      }))
    } catch (error) {
      setState((previous) => ({
        ...previous,
        loadingPatients: false,
        patients: [],
        patientError: toRequestErrorMessage(error),
      }))
    }
  }, [])

  const loadOverviewAndPatients = useCallback(async (
    capabilities: CalculationCapabilities,
    preferredMeasureId: string,
  ): Promise<void> => {
    setState((previous) => ({
      ...previous,
      loadingOverview: true,
    }))

    try {
      const overview = await loadCalculationOverview(capabilities)
      const nextSelectedMeasure = withDefaultSelection(overview, preferredMeasureId)
      setState((previous) => ({
        ...previous,
        loadingOverview: false,
        overview,
        selectedMeasureId: nextSelectedMeasure,
        selectedMeasureIdsForRun: previous.selectedMeasureIdsForRun.length > 0
          ? previous.selectedMeasureIdsForRun.filter((id) => overview.measures.some((measure) => measure.measureId === id))
          : overview.measures.map((measure) => measure.measureId),
      }))
      await loadPatientsForMeasure(capabilities, nextSelectedMeasure)
    } catch (error) {
      setState((previous) => ({
        ...previous,
        loadingOverview: false,
        overview: null,
        selectedMeasureId: "",
        patients: [],
      }))
      setStatus(toRequestErrorMessage(error), true)
    }
  }, [loadPatientsForMeasure, setStatus])

  const initialize = useCallback(async (): Promise<void> => {
    setState((previous) => ({
      ...previous,
      loadingSetup: true,
      setupError: null,
      statusMessage: "",
      statusError: false,
    }))

    try {
      const capabilities = await discoverCalculationCapabilities()
      setState((previous) => ({
        ...previous,
        capabilities,
        loadingSetup: false,
      }))

      await loadOverviewAndPatients(capabilities, "")
    } catch (error) {
      setState((previous) => ({
        ...previous,
        loadingSetup: false,
        setupError: toRequestErrorMessage(error),
        capabilities: null,
      }))
    }
  }, [loadOverviewAndPatients])

  useEffect(() => {
    void initialize()
  }, [initialize])

  const selectMeasure = useCallback(async (measureId: string): Promise<void> => {
    setState((previous) => ({
      ...previous,
      selectedMeasureId: measureId,
    }))

    if (!state.capabilities) {
      return
    }

    await loadPatientsForMeasure(state.capabilities, measureId)
  }, [loadPatientsForMeasure, state.capabilities])

  const toggleMeasureForRun = useCallback((measureId: string): void => {
    setState((previous) => {
      const exists = previous.selectedMeasureIdsForRun.includes(measureId)
      const next = exists
        ? previous.selectedMeasureIdsForRun.filter((id) => id !== measureId)
        : [...previous.selectedMeasureIdsForRun, measureId]
      return {
        ...previous,
        selectedMeasureIdsForRun: next,
      }
    })
  }, [])

  const selectAllMeasuresForRun = useCallback((): void => {
    setState((previous) => ({
      ...previous,
      selectedMeasureIdsForRun: previous.overview?.measures.map((measure) => measure.measureId) ?? [],
    }))
  }, [])

  const clearMeasuresForRun = useCallback((): void => {
    setState((previous) => ({
      ...previous,
      selectedMeasureIdsForRun: [],
    }))
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    if (!state.capabilities) {
      await initialize()
      return
    }

    await loadOverviewAndPatients(state.capabilities, state.selectedMeasureId)
  }, [initialize, loadOverviewAndPatients, state.capabilities, state.selectedMeasureId])

  const run = useCallback(async (): Promise<void> => {
    if (!state.capabilities) {
      setStatus("Calculation endpoints are unavailable.", true)
      return
    }

    if (
      state.capabilities.supportsSelectedMeasures
      && state.selectedMeasureIdsForRun.length === 0
    ) {
      setStatus("Choose at least one measure.", true)
      return
    }

    setState((previous) => ({
      ...previous,
      runningCalculation: true,
    }))
    setStatus("Starting calculation...")

    try {
      const result = await runCalculation(state.capabilities, state.selectedMeasureIdsForRun)
      setStatus(result.message)
      await loadOverviewAndPatients(state.capabilities, state.selectedMeasureId)
    } catch (error) {
      setStatus(toRequestErrorMessage(error), true)
    } finally {
      setState((previous) => ({
        ...previous,
        runningCalculation: false,
      }))
    }
  }, [loadOverviewAndPatients, setStatus, state.capabilities, state.selectedMeasureId, state.selectedMeasureIdsForRun])

  const selectedMeasure = useMemo(
    () => state.overview?.measures.find((measure) => measure.measureId === state.selectedMeasureId) ?? null,
    [state.overview, state.selectedMeasureId],
  )

  return {
    capabilities: state.capabilities,
    loadingSetup: state.loadingSetup,
    setupError: state.setupError,
    loadingOverview: state.loadingOverview,
    loadingPatients: state.loadingPatients,
    runningCalculation: state.runningCalculation,
    overview: state.overview,
    selectedMeasure,
    selectedMeasureId: state.selectedMeasureId,
    selectedMeasureIdsForRun: state.selectedMeasureIdsForRun,
    patients: state.patients,
    patientError: state.patientError,
    statusMessage: state.statusMessage,
    statusError: state.statusError,
    retrySetup: initialize,
    selectMeasure,
    toggleMeasureForRun,
    selectAllMeasuresForRun,
    clearMeasuresForRun,
    refresh,
    run,
  }
}
