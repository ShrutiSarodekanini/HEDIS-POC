import { useCallback, useEffect, useState } from "react"

import type {
  IntegrationActionResult,
  IntegrationCapabilities,
} from "../models/integration"
import {
  fetchIntegrationCapabilities,
  runProcessGapsAction,
  runSeedDataAction,
} from "../services/integration.service"

interface UseIntegrationActionsResult {
  capabilities: IntegrationCapabilities
  lastAction: IntegrationActionResult | null
  loadingCapabilities: boolean
  runningSeed: boolean
  runningProcess: boolean
  refreshCapabilities: () => Promise<void>
  runSeed: () => Promise<void>
  runProcess: () => Promise<void>
}

const EMPTY_CAPABILITIES: IntegrationCapabilities = {
  hasSeedEndpoint: false,
  hasProcessEndpoint: false,
}

export function useIntegrationActions(
  onCompleted: () => Promise<void>,
): UseIntegrationActionsResult {
  const [capabilities, setCapabilities] =
    useState<IntegrationCapabilities>(EMPTY_CAPABILITIES)
  const [loadingCapabilities, setLoadingCapabilities] = useState<boolean>(true)
  const [runningSeed, setRunningSeed] = useState<boolean>(false)
  const [runningProcess, setRunningProcess] = useState<boolean>(false)
  const [lastAction, setLastAction] = useState<IntegrationActionResult | null>(null)

  const refreshCapabilities = useCallback(async () => {
    setLoadingCapabilities(true)
    const next = await fetchIntegrationCapabilities()
    setCapabilities(next)
    setLoadingCapabilities(false)
  }, [])

  useEffect(() => {
    void refreshCapabilities()
  }, [refreshCapabilities])

  const runSeed = useCallback(async () => {
    setRunningSeed(true)
    const result = await runSeedDataAction()
    setLastAction(result)
    setRunningSeed(false)

    if (result.success) {
      await onCompleted()
    }
  }, [onCompleted])

  const runProcess = useCallback(async () => {
    setRunningProcess(true)
    const result = await runProcessGapsAction()
    setLastAction(result)
    setRunningProcess(false)

    if (result.success) {
      await onCompleted()
    }
  }, [onCompleted])

  return {
    capabilities,
    lastAction,
    loadingCapabilities,
    runningSeed,
    runningProcess,
    refreshCapabilities,
    runSeed,
    runProcess,
  }
}
