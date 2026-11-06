import { envConfig } from "../config/env"
import type {
  DashboardSnapshot,
  DataSection,
  StarCatalogResponse,
  StarDashboardResponse,
} from "../models/dashboard"
import { fetchJson, postJson, toRequestErrorMessage } from "./http"
import { toNumber } from "../utils/number"

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

/** Rows in care_gap with status 'open' (the measure cards' total_gaps also count closed gaps). */
async function loadOpenGapCount(): Promise<number | null> {
  try {
    const flow = await fetchJson<{ tables?: { care_gap_open?: number | string | null } }>(
      withBase("/flow"),
      envConfig.apiTimeoutMs,
    )
    return toNumber(flow.tables?.care_gap_open)
  } catch {
    // The KPI then shows "-"; the rest of the snapshot does not depend on this count.
    return null
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
    openGapCount: await loadOpenGapCount(),
    fetchedAt: new Date().toISOString(),
  }
}
