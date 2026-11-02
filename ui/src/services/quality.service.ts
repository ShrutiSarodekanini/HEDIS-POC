import { envConfig } from "../config/env"
import type { StarDashboardResponse } from "../models/dashboard"
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

interface FlowBatch {
  batch_id?: string
  status?: string
  step?: string
  updated_at?: string
}

interface FlowResponse {
  batch?: FlowBatch | null
}

interface IngestionJobsResponse {
  jobs?: Array<{
    id: string
    started_at?: string | null
    status?: string
  }>
}

interface StarDashboardMeasureCard {
  measure_id: string
  measure_name: string
  total_gaps: number
  baseline_closed: number
  projected_closed: number
  baseline_rate: number | null
  projected_rate: number | null
  baseline_star: number | null
  projected_star: number | null
}

const loadAutomationJobs = async (): Promise<IngestionJobsResponse> => {
  const adapter = await fetchJson<IngestionJobsResponse>(withApiBase("/ingestion/jobs"), timeoutMs).catch(() => null)
  if (adapter?.jobs && adapter.jobs.length >= 0) {
    return adapter
  }

  const direct = await fetchJson<IngestionJobsResponse>(withApiBase("/jobs"), timeoutMs).catch(() => null)
  if (direct?.jobs && direct.jobs.length >= 0) {
    return direct
  }

  return { jobs: [] }
}

const timeoutMs = envConfig.apiTimeoutMs

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const toOverviewMode = (mode: QualityMode): QualityOverview => ({
  mode,
  measures: [],
  batches: [],
  not_available: [],
})

const toPercent = (value: number | null | undefined): string => (
  typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)}%` : "-"
)

const loadDashboardQualitySnapshot = async (): Promise<StarDashboardResponse | null> => postJson<StarDashboardResponse>(
  withApiBase(envConfig.dashboardSummaryPath),
  timeoutMs,
  {
    measure_id: envConfig.dashboardDefaultMeasureId ?? null,
    selected_gap_ids: [],
  },
).catch(() => null)

const toAutomationMeasures = (cards: StarDashboardMeasureCard[] | undefined): QualityOverview["measures"] => (cards ?? []).map((card) => {
  const openGaps = Math.max(0, card.total_gaps - card.baseline_closed)
  const description = [
    `${openGaps} open gap${openGaps === 1 ? "" : "s"}`,
    `baseline ${card.baseline_closed}/${card.total_gaps} (${toPercent(card.baseline_rate)})`,
    `projected ${card.projected_closed}/${card.total_gaps} (${toPercent(card.projected_rate)})`,
  ].join(" | ")

  return {
    measure_id: card.measure_id,
    phase: "dashboard_snapshot",
    description,
    open_gaps: openGaps,
    total_gaps: card.total_gaps,
    baseline_closed: card.baseline_closed,
    projected_closed: card.projected_closed,
    baseline_rate: card.baseline_rate,
    projected_rate: card.projected_rate,
    baseline_star: card.baseline_star,
    projected_star: card.projected_star,
  }
})

export async function discoverQualityCapabilities(): Promise<QualityCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))

  if (
    hasOpenApiPath(paths, "/api/quality")
    && hasOpenApiPath(paths, "/api/quality/runs")
    && hasOpenApiPath(paths, "/api/quality/runs/{run_id}")
  ) {
    return { mode: "integration" }
  }

  if (hasOpenApiPath(paths, "/api/automation/process-gaps")) {
    return { mode: "automation" }
  }

  return { mode: "unavailable" }
}

export async function loadQualityOverview(capabilities: QualityCapabilities): Promise<QualityOverview> {
  if (capabilities.mode === "integration") {
    const response = await fetchJson<QualityOverviewResponse>(withApiBase("/quality"), timeoutMs)
    return {
      mode: "integration",
      measures: response.measures ?? [],
      batches: response.batches ?? [],
      not_available: response.not_available ?? [],
    }
  }

  if (capabilities.mode === "automation") {
    const [flow, jobs, dashboard] = await Promise.all([
      fetchJson<FlowResponse>(withApiBase("/flow"), timeoutMs).catch(() => ({ batch: null })),
      loadAutomationJobs(),
      loadDashboardQualitySnapshot(),
    ])

    const batchId = flow.batch?.batch_id ?? jobs.jobs?.[0]?.id ?? "latest"
    const measures = toAutomationMeasures(dashboard?.measure_cards as StarDashboardMeasureCard[] | undefined)

    return {
      mode: "automation",
      measures,
      batches: [
        {
          batch_id: batchId,
          status: flow.batch?.status ?? jobs.jobs?.[0]?.status ?? "unknown",
          step: flow.batch?.step,
          started_at: flow.batch?.updated_at ?? jobs.jobs?.[0]?.started_at ?? null,
          loaded: true,
          runs: 0,
          latest_run_id: null,
        },
      ],
      not_available: [
        {
          name: "Detailed checks",
          reason:
            "Detailed quality endpoints are unavailable in this backend profile. Showing automation and dashboard snapshot data from available endpoints.",
        },
      ],
    }
  }

  return toOverviewMode("unavailable")
}

export async function loadQualityRuns(batchId: string): Promise<QualityRunSummary[]> {
  const response = await fetchJson<QualityRunsResponse>(
    withApiBase(`/quality/runs?batch_id=${encodeURIComponent(batchId)}`),
    timeoutMs,
  )
  return response.runs ?? []
}

export async function loadQualityRunDetail(runId: number, severity: string): Promise<QualityRunDetail> {
  const query = severity && severity !== "All" ? `?severity=${encodeURIComponent(severity.toLowerCase())}` : ""
  return fetchJson<QualityRunDetail>(withApiBase(`/quality/runs/${encodeURIComponent(String(runId))}${query}`), timeoutMs)
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
}): Promise<QualityCoverageResponse> {
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

  return fetchJson<QualityCoverageResponse>(withApiBase(`/quality/coverage?${query}`), timeoutMs)
}

export async function runAutomationQualitySummary(): Promise<QualityRunActionResult> {
  const response = await postJson<Record<string, unknown>>(
    withApiBase(envConfig.integrationProcessPath),
    timeoutMs,
    {},
  )

  const processed = Number(response.processed ?? 0)
  const skipped = Boolean(response.skipped)
  const skipReason = typeof response.skip_reason === "string" ? response.skip_reason : ""

  if (skipped) {
    return {
      success: true,
      message: `Processed ${processed.toLocaleString()} gaps and skipped writeback: ${skipReason}`,
    }
  }

  return {
    success: true,
    message: `Processed ${processed.toLocaleString()} gaps in automation mode.`,
  }
}
