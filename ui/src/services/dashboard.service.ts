import { envConfig } from "../config/env"
import type {
  DashboardSnapshot,
  DataSection,
  StarCatalogResponse,
  StarDashboardResponse,
} from "../models/dashboard"
import { fetchJson, postJson, toRequestErrorMessage } from "./http"

const withBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

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

export async function fetchDashboardSnapshot(): Promise<DashboardSnapshot> {
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

  return {
    catalog,
    dashboard,
    fetchedAt: new Date().toISOString(),
  }
}
