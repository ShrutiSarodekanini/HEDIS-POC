import { envConfig } from "../config/env"
import type {
  QualityCapabilities,
  QualityCoverageResponse,
  QualityMode,
  QualityOverview,
  QualityRunActionResult,
  QualityRunDetail,
  QualityRunSummary,
} from "../models/quality"
import { fetchJson, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

interface QualityOverviewResponse {
  measures?: Array<{ measure_id: string; phase?: string; description?: string | null }>
  batches?: Array<{
    batch_id: string
    started_at?: string | null
    status?: string
    step?: string
    loaded?: boolean
    runs?: number
    latest_run_id?: number | null
  }>
  not_available?: Array<{ name?: string; reason?: string }>
}

interface QualityRunsResponse {
  runs?: QualityRunSummary[]
}

const timeoutMs = envConfig.apiTimeoutMs
const CACHE_TTL_MS = 15_000

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const toOverviewMode = (mode: QualityMode): QualityOverview => ({
  mode,
  measures: [],
  batches: [],
  not_available: [],
})

interface RequestOptions {
  forceFresh?: boolean
}

interface CacheEntry<T> {
  value: T
  expiresAt: number
}

const now = (): number => Date.now()

let overviewCache: CacheEntry<QualityOverview> | null = null
let overviewInFlight: Promise<QualityOverview> | null = null

const runsCache = new Map<string, CacheEntry<QualityRunSummary[]>>()
const runsInFlight = new Map<string, Promise<QualityRunSummary[]>>()

const runDetailCache = new Map<string, CacheEntry<QualityRunDetail>>()
const runDetailInFlight = new Map<string, Promise<QualityRunDetail>>()

const coverageCache = new Map<string, CacheEntry<QualityCoverageResponse>>()
const coverageInFlight = new Map<string, Promise<QualityCoverageResponse>>()

const fromCache = <T>(entry: CacheEntry<T> | undefined | null): T | null => {
  if (!entry || entry.expiresAt <= now()) {
    return null
  }
  return entry.value
}

const normalizeSeverity = (severity: string): string => {
  const normalized = severity.trim().toLowerCase()
  return normalized === "all" ? "" : normalized
}

export function clearQualityDataCaches(batchId?: string): void {
  overviewCache = null
  overviewInFlight = null

  if (batchId) {
    runsCache.delete(batchId)
    runsInFlight.delete(batchId)
  } else {
    runsCache.clear()
    runsInFlight.clear()
  }

  runDetailCache.clear()
  runDetailInFlight.clear()
  coverageCache.clear()
  coverageInFlight.clear()
}

export async function discoverQualityCapabilities(): Promise<QualityCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))

  if (
    hasOpenApiPath(paths, "/api/quality")
    && hasOpenApiPath(paths, "/api/quality/runs")
    && hasOpenApiPath(paths, "/api/quality/runs/{run_id}")
    && hasOpenApiPath(paths, "/api/quality/coverage")
  ) {
    return { mode: "integration" }
  }

  return { mode: "unavailable" }
}

export async function loadQualityOverview(
  capabilities: QualityCapabilities,
  options?: RequestOptions,
): Promise<QualityOverview> {
  if (capabilities.mode !== "integration") {
    return {
      ...toOverviewMode("unavailable"),
      not_available: [
        {
          name: "Data quality endpoint",
          reason: "The backend does not expose /api/quality routes in this profile.",
        },
      ],
    }
  }

  const forceFresh = Boolean(options?.forceFresh)
  if (!forceFresh) {
    const cached = fromCache(overviewCache)
    if (cached) {
      return cached
    }
    if (overviewInFlight) {
      return overviewInFlight
    }
  }

  const request = fetchJson<QualityOverviewResponse>(withApiBase("/quality"), timeoutMs)
    .then((response) => {
      const value: QualityOverview = {
        mode: "integration",
        measures: response.measures ?? [],
        batches: response.batches ?? [],
        not_available: response.not_available ?? [],
      }
      overviewCache = {
        value,
        expiresAt: now() + CACHE_TTL_MS,
      }
      return value
    })
    .finally(() => {
      overviewInFlight = null
    })

  overviewInFlight = request
  return request
}

export async function loadQualityRuns(batchId: string, options?: RequestOptions): Promise<QualityRunSummary[]> {
  const key = batchId
  const forceFresh = Boolean(options?.forceFresh)

  if (!forceFresh) {
    const cached = fromCache(runsCache.get(key))
    if (cached) {
      return cached
    }

    const inFlight = runsInFlight.get(key)
    if (inFlight) {
      return inFlight
    }
  }

  const request = fetchJson<QualityRunsResponse>(
    withApiBase(`/quality/runs?batch_id=${encodeURIComponent(batchId)}`),
    timeoutMs,
  )
    .then((response) => {
      const value = response.runs ?? []
      runsCache.set(key, { value, expiresAt: now() + CACHE_TTL_MS })
      return value
    })
    .finally(() => {
      runsInFlight.delete(key)
    })

  runsInFlight.set(key, request)
  return request
}

export async function loadQualityRunDetail(
  runId: number,
  severity: string,
  options?: RequestOptions,
): Promise<QualityRunDetail> {
  const normalizedSeverity = normalizeSeverity(severity)
  const key = `${runId}|${normalizedSeverity || "all"}`
  const forceFresh = Boolean(options?.forceFresh)

  if (!forceFresh) {
    const cached = fromCache(runDetailCache.get(key))
    if (cached) {
      return cached
    }

    const inFlight = runDetailInFlight.get(key)
    if (inFlight) {
      return inFlight
    }
  }

  const query = normalizedSeverity ? `?severity=${encodeURIComponent(normalizedSeverity)}` : ""
  const request = fetchJson<QualityRunDetail>(withApiBase(`/quality/runs/${encodeURIComponent(String(runId))}${query}`), timeoutMs)
    .then((response) => {
      runDetailCache.set(key, { value: response, expiresAt: now() + CACHE_TTL_MS })
      return response
    })
    .finally(() => {
      runDetailInFlight.delete(key)
    })

  runDetailInFlight.set(key, request)
  return request
}

export async function triggerQualityRun(batchId: string, measureId: string): Promise<QualityRunActionResult> {
  await postJson<Record<string, unknown>>(
    withApiBase("/quality/runs"),
    timeoutMs,
    {
      batch_id: batchId,
      measure_id: measureId === "All" ? null : measureId,
    },
  )

  clearQualityDataCaches(batchId)

  return {
    success: true,
    message: "Data quality checks finished.",
  }
}

export async function loadCoverageReport(filters: {
  payer: string
  compliance: string
  startDate: string
  endDate: string
}, options?: RequestOptions): Promise<QualityCoverageResponse> {
  const query = new URLSearchParams()
  if (filters.payer !== "All") {
    query.set("payer", filters.payer)
  }
  if (filters.compliance !== "All") {
    query.set("compliance", filters.compliance)
  }
  if (filters.startDate) {
    query.set("start_date", filters.startDate)
  }
  if (filters.endDate) {
    query.set("end_date", filters.endDate)
  }

  const key = query.toString() || "all"
  const forceFresh = Boolean(options?.forceFresh)

  if (!forceFresh) {
    const cached = fromCache(coverageCache.get(key))
    if (cached) {
      return cached
    }

    const inFlight = coverageInFlight.get(key)
    if (inFlight) {
      return inFlight
    }
  }

  const request = fetchJson<QualityCoverageResponse>(withApiBase(`/quality/coverage?${query}`), timeoutMs)
    .then((response) => {
      coverageCache.set(key, { value: response, expiresAt: now() + CACHE_TTL_MS })
      return response
    })
    .finally(() => {
      coverageInFlight.delete(key)
    })

  coverageInFlight.set(key, request)
  return request
}
