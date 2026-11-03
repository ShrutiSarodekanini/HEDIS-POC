import { envConfig } from "../config/env"
import type {
  DashboardSnapshot,
  DataSection,
  StarCatalogResponse,
  StarDashboardResponse,
} from "../models/dashboard"
import { fetchJson, postJson, toRequestErrorMessage } from "./http"

const withBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`
const SNAPSHOT_CACHE_TTL_MS = 30_000

interface DashboardSnapshotCacheEntry {
  value: DashboardSnapshot
  expiresAt: number
}

interface DashboardSnapshotRequestOptions {
  forceFresh?: boolean
}

const now = (): number => Date.now()

let dashboardSnapshotCache: DashboardSnapshotCacheEntry | null = null
let dashboardSnapshotInFlight: Promise<DashboardSnapshot> | null = null

async function loadSectionGet<T>(path: string): Promise<DataSection<T>> {
  try {
    const data = await fetchJson<T>(withBase(path), envConfig.apiTimeoutMs)
    return {
      data,
      error: null,
    }
  } catch (error) {
    return {
      data: null,
      error: toRequestErrorMessage(error),
    }
  }
}

async function loadSectionPost<T>(
  path: string,
  body: unknown,
): Promise<DataSection<T>> {
  try {
    const data = await postJson<T>(withBase(path), envConfig.apiTimeoutMs, body)
    return {
      data,
      error: null,
    }
  } catch (error) {
    return {
      data: null,
      error: toRequestErrorMessage(error),
    }
  }
}

export async function fetchDashboardSnapshot(options?: DashboardSnapshotRequestOptions): Promise<DashboardSnapshot> {
  const forceFresh = Boolean(options?.forceFresh)

  if (!forceFresh && dashboardSnapshotCache && dashboardSnapshotCache.expiresAt > now()) {
    return dashboardSnapshotCache.value
  }

  if (!forceFresh && dashboardSnapshotInFlight) {
    return dashboardSnapshotInFlight
  }

  const request = (async () => {
    const catalog = await loadSectionGet<StarCatalogResponse>(
      envConfig.dashboardCatalogPath,
    )

    const selectedMeasure =
      envConfig.dashboardDefaultMeasureId ??
      catalog.data?.measures.find((measure) => measure.measure_code.trim().length > 0)
        ?.measure_code ??
      null

    const dashboard = await loadSectionPost<StarDashboardResponse>(
      envConfig.dashboardSummaryPath,
      {
        measure_id: selectedMeasure,
        selected_gap_ids: [],
      },
    )

    const value = {
      catalog,
      dashboard,
      fetchedAt: new Date().toISOString(),
    }

    dashboardSnapshotCache = {
      value,
      expiresAt: now() + SNAPSHOT_CACHE_TTL_MS,
    }

    return value
  })().finally(() => {
    dashboardSnapshotInFlight = null
  })

  dashboardSnapshotInFlight = request
  return request
}
