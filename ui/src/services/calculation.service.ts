import { envConfig } from "../config/env"
import type {
  CalculationCapabilities,
  CalculationOverview,
  CalculationOverviewMeasure,
  CalculationPatientRow,
  CalculationRunResult,
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
    await postJson<Record<string, unknown>>(withApiBase("/calculation/run"), timeoutMs, body)
    return {
      success: true,
      message: selectedMeasureIds.length > 0
        ? `Calculation started for ${selectedMeasureIds.length} measure(s).`
        : "Calculation started for all measures.",
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
