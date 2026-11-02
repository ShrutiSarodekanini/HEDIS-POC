import { useCallback, useEffect, useState } from "react"

import type { DashboardSnapshot } from "../models/dashboard"
import { fetchDashboardSnapshot } from "../services/dashboard.service"

interface UseDashboardDataResult {
  snapshot: DashboardSnapshot | null
  loading: boolean
  reload: () => Promise<void>
}

export function useDashboardData(enabled = true): UseDashboardDataResult {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null)
  const [loading, setLoading] = useState<boolean>(enabled)

  const reload = useCallback(async () => {
    if (!enabled) {
      return
    }

    setLoading(true)
    try {
      const nextSnapshot = await fetchDashboardSnapshot()
      setSnapshot(nextSnapshot)
    } finally {
      setLoading(false)
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }

    void reload()
  }, [enabled, reload])

  return { snapshot, loading, reload }
}
