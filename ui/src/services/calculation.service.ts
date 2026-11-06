import { envConfig } from "../config/env"
import type {
  CalculationCapabilities,
  CalculationOverview,
  CalculationOverviewMeasure,
  CalculationPatientRow,
  CalculationRunResult,
  LatestBatch,
} from "../models/calculation"
import type { StarCatalogResponse, StarDashboardResponse } from "../models/dashboard"
import { fetchJson, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

interface IntegrationCalculationLatestRun {
  run_id?: number
  run_at?: string
}

interface IntegrationCalculationMeasure {
  measure: string
  name?: string
  star_measure?: boolean
  denominator?: number
  met?: number
  excluded?: number
  not_met?: number
  gaps_open?: number
  gaps_closed?: number
  confirmed_by_reviewer?: number
  computed_at?: string
}

interface IntegrationCalculationResponse {
  measurement_year?: number
  latest_run?: IntegrationCalculationLatestRun | null
  measures?: IntegrationCalculationMeasure[]
}

interface IntegrationCalculationPatient {
  patient_id: string
  in_denominator?: boolean
  excluded?: boolean
  outcome?: string
  gap_status?: string
  review_status?: string
  computed_at?: string
  gap_id?: string
  missing_element?: string
}

interface IntegrationCalculationPatientsResponse {
  patients?: IntegrationCalculationPatient[]
}

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const timeoutMs = envConfig.apiTimeoutMs

const asRate = (met: number, inMeasure: number, excluded: number): number | null => {
  const denominator = Math.max(0, inMeasure - excluded)
  if (denominator <= 0) {
    return null
  }
  return (met / denominator) * 100
}

const mapStarMeasures = (
  catalog: StarCatalogResponse,
  dashboard: StarDashboardResponse,
): CalculationOverviewMeasure[] => {
  const measureNameByCode = new Map<string, string>()
  for (const measure of catalog.measures) {
    measureNameByCode.set(measure.measure_code, measure.measure_name)
  }

  return dashboard.measure_cards.map((row) => {
    const inMeasure = row.total_gaps
    const met = row.baseline_closed
    const excluded = 0
    const notMet = Math.max(0, inMeasure - met)

    return {
      measureId: row.measure_id,
      measureName: measureNameByCode.get(row.measure_id) ?? row.measure_name,
      measureType: row.star_eligible ? "Star measure" : "Not a Star measure",
      inMeasure,
      met,
      excluded,
      notMet,
      rate: asRate(met, inMeasure, excluded),
      gapsOpen: row.total_gaps - row.baseline_closed,
      closedByEngine: row.projected_closed,
      confirmedByReviewer: 0,
      computedAt: null,
    }
  })
}

const mapIntegrationMeasures = (response: IntegrationCalculationResponse): CalculationOverviewMeasure[] =>
  (response.measures ?? []).map((row) => {
    const inMeasure = row.denominator ?? 0
    const met = row.met ?? 0
    const excluded = row.excluded ?? 0
    return {
      measureId: row.measure,
      measureName: row.name ?? row.measure,
      measureType: row.star_measure ? "Star measure" : "Not a Star measure",
      inMeasure,
      met,
      excluded,
      notMet: row.not_met ?? Math.max(0, inMeasure - excluded - met),
      rate: asRate(met, inMeasure, excluded),
      gapsOpen: row.gaps_open ?? 0,
      closedByEngine: row.gaps_closed ?? 0,
      confirmedByReviewer: row.confirmed_by_reviewer ?? 0,
      computedAt: row.computed_at ?? null,
    }
  })

export async function discoverCalculationCapabilities(): Promise<CalculationCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs)

  if (hasOpenApiPath(paths, "/api/calculation") && hasOpenApiPath(paths, "/api/calculation/run")) {
    return {
      mode: "integration",
      supportsSelectedMeasures: true,
    }
  }

  if (hasOpenApiPath(paths, "/api/star-dashboard/calculate") && hasOpenApiPath(paths, "/api/automation/process-gaps")) {
    return {
      mode: "star",
      supportsSelectedMeasures: false,
    }
  }

  throw new Error("Calculation endpoints are not available in this backend profile.")
}

export async function loadCalculationOverview(
  capabilities: CalculationCapabilities,
): Promise<CalculationOverview> {
  if (capabilities.mode === "integration") {
    const response = await fetchJson<IntegrationCalculationResponse>(withApiBase("/calculation"), timeoutMs)
    const run = response.latest_run
    return {
      mode: "integration",
      measurementYear: response.measurement_year ?? null,
      latestRunLabel: run?.run_id ? `Run ${run.run_id} at ${run.run_at ?? "-"}` : "No run stored yet",
      measures: mapIntegrationMeasures(response),
    }
  }

  const [catalog, dashboard] = await Promise.all([
    fetchJson<StarCatalogResponse>(withApiBase(envConfig.dashboardCatalogPath), timeoutMs),
    postJson<StarDashboardResponse>(
      withApiBase(envConfig.dashboardSummaryPath),
      timeoutMs,
      {
        measure_id: envConfig.dashboardDefaultMeasureId ?? null,
        selected_gap_ids: [],
      },
    ),
  ])

  return {
    mode: "star",
    measurementYear: catalog.measures[0]?.measurement_year ?? null,
    latestRunLabel: "Derived from current star dashboard snapshot",
    measures: mapStarMeasures(catalog, dashboard),
  }
}

export async function loadCalculationPatients(
  capabilities: CalculationCapabilities,
  measureId: string,
): Promise<CalculationPatientRow[]> {
  if (capabilities.mode === "integration") {
    const response = await fetchJson<IntegrationCalculationPatientsResponse>(
      withApiBase(`/calculation/${encodeURIComponent(measureId)}/patients`),
      timeoutMs,
    )

    return (response.patients ?? []).map((row) => ({
      patientId: row.patient_id,
      inDenominator: Boolean(row.in_denominator),
      met: String(row.outcome ?? "").toLowerCase() === "met",
      excluded: Boolean(row.excluded),
      outcome: row.outcome ?? "unknown",
      gapStatus: row.gap_status ?? "-",
      reviewStatus: row.review_status ?? "-",
      computedAt: row.computed_at ?? null,
      gapId: row.gap_id ?? null,
      missingElement: row.missing_element ?? null,
    }))
  }

  const response = await postJson<StarDashboardResponse>(
    withApiBase(envConfig.dashboardSummaryPath),
    timeoutMs,
    {
      measure_id: measureId,
      selected_gap_ids: [],
    },
  )

  return (response.selected_measure_gaps ?? []).map((row) => ({
    patientId: row.member_id,
    inDenominator: true,
    met: row.is_closed,
    excluded: false,
    outcome: row.is_closed ? "met" : "gap",
    gapStatus: row.status,
    reviewStatus: row.selected_for_projection ? "selected" : "not_selected",
    computedAt: null,
    gapId: row.care_gap_id,
    missingElement: null,
  }))
}

export async function runCalculation(
  capabilities: CalculationCapabilities,
  selectedMeasureIds: string[],
): Promise<CalculationRunResult> {
  if (capabilities.mode === "integration") {
    const body = selectedMeasureIds.length > 0 ? { measures: selectedMeasureIds } : {}
    const response = await postJson<Record<string, unknown>>(withApiBase("/calculation/run"), timeoutMs, body)
    return {
      success: true,
      message: selectedMeasureIds.length > 0
        ? `Calculation running for ${selectedMeasureIds.length} measure(s)...`
        : "Calculation running for all measures...",
      batchId: typeof response.batch_id === "string" ? response.batch_id : undefined,
    }
  }

  const response = await postJson<Record<string, unknown>>(withApiBase(envConfig.integrationProcessPath), timeoutMs, {})
  const processed = Number(response.processed ?? 0)
  const skipped = Boolean(response.skipped)
  const skipReason = typeof response.skip_reason === "string" ? response.skip_reason : ""

  if (skipped) {
    return {
      success: true,
      message: `Gap automation processed ${processed.toLocaleString()} rows and skipped writeback: ${skipReason}`,
    }
  }

  return {
    success: true,
    message: `Gap processing completed using star/automation backend profile (${processed.toLocaleString()} rows).`,
  }
}

interface IntegrationBatchStatus {
  status?: string
  step?: string
  error?: string | null
}

const CALCULATION_POLL_MS = 2000
const CALCULATION_WAIT_MS = 15 * 60 * 1000

const delay = (ms: number): Promise<void> => new Promise((resolve) => window.setTimeout(resolve, ms))

/** The run endpoint answers 202 and calculates in the background; poll the batch until it leaves "running". */
export async function waitForCalculation(batchId: string): Promise<CalculationRunResult> {
  const deadline = Date.now() + CALCULATION_WAIT_MS
  while (Date.now() < deadline) {
    await delay(CALCULATION_POLL_MS)
    const batch = await fetchJson<IntegrationBatchStatus>(
      withApiBase(`/batches/${encodeURIComponent(batchId)}`),
      timeoutMs,
    )
    if (batch.status === "running") {
      continue
    }
    if (batch.status === "failed") {
      return {
        success: false,
        message: `Calculation failed while ${batch.step ?? "running"}: ${batch.error ?? "see the server log."}`,
      }
    }
    return { success: true, message: "Calculation finished." }
  }
  return { success: false, message: "Calculation is still running. Press Refresh to check again." }
}

interface IntegrationLatestBatch extends IntegrationBatchStatus {
  batch_id?: string
  started_at?: string | null
  updated_at?: string | null
  counts?: unknown
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null

/** The message of the newest stage in counts.progress that has not completed yet. */
const currentProgressMessage = (counts: unknown): string | null => {
  const progress = asRecord(asRecord(counts)?.progress)
  if (!progress) {
    return null
  }
  let newest: { at: number; message: string } | null = null
  for (const entry of Object.values(progress)) {
    const stage = asRecord(entry)
    if (!stage || stage.status === "complete" || typeof stage.message !== "string" || !stage.message) {
      continue
    }
    const at = typeof stage.updated_at === "string" ? Date.parse(stage.updated_at) : Number.NaN
    const time = Number.isNaN(at) ? 0 : at
    if (!newest || time > newest.at) {
      newest = { at: time, message: stage.message }
    }
  }
  return newest?.message ?? null
}

/** The most recently started batch, or null when no batch has run yet. */
export async function fetchLatestBatch(): Promise<LatestBatch | null> {
  const batch = await fetchJson<IntegrationLatestBatch | null>(withApiBase("/batches/latest"), timeoutMs)
  if (!batch || !batch.batch_id) {
    return null
  }
  return {
    batchId: batch.batch_id,
    status: batch.status ?? "unknown",
    step: batch.step ?? null,
    error: batch.error ?? null,
    startedAt: batch.started_at ?? null,
    updatedAt: batch.updated_at ?? null,
    progressMessage: currentProgressMessage(batch.counts),
  }
}

interface FlowLatestIngestJob {
  status?: string | null
  resources_persisted?: number | null
  has_batch?: boolean
}

const FINISHED_INGEST_STATUSES = new Set(["completed", "partially_completed"])
const STOPPED_INGEST_STATUSES = new Set(["failed", "cancelled", "canceled", "error"])

/** True while the newest ingestion job is still loading, or persisted and waiting for its batch to start. */
export async function fetchIngestionAwaitingBatch(): Promise<boolean> {
  const flow = await fetchJson<{ latest_ingest_job?: FlowLatestIngestJob | null }>(withApiBase("/flow"), timeoutMs)
  const job = flow.latest_ingest_job
  if (!job || job.has_batch !== false) {
    return false
  }
  const status = (job.status ?? "").trim().toLowerCase()
  if (STOPPED_INGEST_STATUSES.has(status)) {
    return false
  }
  if (FINISHED_INGEST_STATUSES.has(status)) {
    return (job.resources_persisted ?? 0) > 0
  }
  return true
}
