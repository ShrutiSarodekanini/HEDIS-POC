import { useEffect, useState } from "react"

import type { QualityAnomaly, QualityCheck, QualityRunSummary } from "../models/quality"
import { HttpError, toRequestErrorMessage } from "../services/http"
import { discoverQualityCapabilities, loadQualityOverview, loadQualityRunDetail } from "../services/quality.service"

interface QualitySummary {
  run: QualityRunSummary
  checks: QualityCheck[]
  anomalies: QualityAnomaly[]
}

interface UseQualitySummaryResult {
  summary: QualitySummary | null
  loading: boolean
  error: string | null
  forbidden: boolean
  unavailable: boolean
}

/**
 * Read-only: the latest data quality run of the newest batch that has one. Never starts a run.
 * Reloads when dataVersion changes and keeps the previous summary on screen meanwhile.
 */
export function useQualitySummary(dataVersion: number): UseQualitySummaryResult {
  const [summary, setSummary] = useState<QualitySummary | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState<boolean>(false)
  const [unavailable, setUnavailable] = useState<boolean>(false)

  useEffect(() => {
    let cancelled = false

    const load = async (): Promise<void> => {
      try {
        const capabilities = await discoverQualityCapabilities()
        if (capabilities.mode !== "integration") {
          if (!cancelled) {
            setUnavailable(true)
          }
          return
        }
        const overview = await loadQualityOverview(capabilities)
        const runId = overview.batches.find((batch) => batch.latest_run_id != null)?.latest_run_id
        if (runId == null) {
          return
        }
        const detail = await loadQualityRunDetail(runId, "All")
        if (!cancelled) {
          setError(null)
          setForbidden(false)
          setSummary({ run: detail.run, checks: detail.checks, anomalies: detail.anomalies })
        }
      } catch (loadError) {
        if (cancelled) {
          return
        }
        if (loadError instanceof HttpError && loadError.status === 403) {
          setForbidden(true)
        } else {
          setError(toRequestErrorMessage(loadError))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void load()

    return () => {
      cancelled = true
    }
  }, [dataVersion])

  return { summary, loading, error, forbidden, unavailable }
}
