import { envConfig } from "../config/env"
import type { StarCatalogResponse, StarDashboardResponse } from "../models/dashboard"
import type {
  StarCapabilities,
  StarCalculationResult,
  StarCatalogMeasure,
  StarComparisonResult,
  StarCutPoint,
  StarMeasureCard,
  StarOverview,
  StarWhatIfResult,
} from "../models/stars"
import { fetchJson, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

interface IntegrationCatalogMeasure {
  measure_id: string
  name?: string
  measurement_year: number
  weight?: number | null
  cut_points?: Array<{ stars: number; low: number; high?: number | null }>
}

interface IntegrationCatalogResponse {
  measurement_year: number
  star_year: number
  measures?: IntegrationCatalogMeasure[]
}

interface IntegrationCalculationMeasure {
  measure: string
  name?: string
  star_measure?: boolean
  denominator?: number
  met?: number
  excluded?: number
}

interface IntegrationCalculationResponse {
  measurement_year?: number
  measures?: IntegrationCalculationMeasure[]
}

interface IntegrationWhatIfResponse {
  disclaimer?: string
  measure?: {
    code: string
    name: string
    measurement_year: number
    cms_rating_year?: number
  }
  baseline?: {
    numerator?: number
    denominator?: number
    performance_rate?: number | null
    sample_star_rating?: number | null
  }
  recalculated?: {
    numerator?: number
    denominator?: number
    performance_rate?: number | null
    sample_star_rating?: number | null
  }
  changes?: {
    performance_rate_change_percentage_points?: number | null
    relative_improvement_percent?: number | null
    star_rating_changed?: boolean
  }
  selected_gap_ids?: string[]
  warnings?: string[]
  care_gaps?: Array<{
    gap_id: string
    description?: string
    evidence_status?: string
    eligible_for_closure?: boolean
  }>
}

interface IntegrationCalculateResponse {
  measure_code?: string
  measurement_year?: number
  cms_rating_year?: number
  input_scoring_value?: number | null
  applied_scoring_value?: number | null
  assigned_star_rating?: number | null
  rating_status?: string
  explanation?: string
  applied_cut_points?: Array<Record<string, unknown>>
}

const timeoutMs = envConfig.apiTimeoutMs
const STAR_OVERVIEW_CACHE_MS = 15_000
const STAR_WHAT_IF_CACHE_MS = 10_000

type StarOverviewCacheEntry = {
  expiresAt: number
  value: StarOverview
}

type StarWhatIfCacheEntry = {
  expiresAt: number
  value: StarWhatIfResult
}

const starOverviewCache = new Map<string, StarOverviewCacheEntry>()
const starOverviewInFlight = new Map<string, Promise<StarOverview>>()
const starWhatIfCache = new Map<string, StarWhatIfCacheEntry>()
const starWhatIfInFlight = new Map<string, Promise<StarWhatIfResult>>()

const now = (): number => Date.now()

const starOverviewCacheKey = (mode: StarCapabilities["mode"]): string => mode

const starWhatIfCacheKey = (
  mode: StarCapabilities["mode"],
  measureId: string,
  selectedGapIds: string[],
): string => `${mode}|${measureId}|${[...selectedGapIds].sort().join(",")}`

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const asRate = (numerator: number, denominator: number): number | null => {
  if (denominator <= 0) {
    return null
  }
  return (numerator / denominator) * 100
}

const mapCatalogCutPoints = (points: Array<{ stars: number; low: number; high?: number | null }> | undefined): StarCutPoint[] =>
  (points ?? []).map((point) => ({
    stars: point.stars,
    low: point.low,
    high: point.high ?? null,
  }))

const mapCutPointsFromCalculation = (points: Array<Record<string, unknown>> | undefined): StarCutPoint[] => {
  const rows = points ?? []
  return rows
    .map((row) => {
      const stars = typeof row.stars === "number" ? row.stars : Number(row.stars)
      const low = typeof row.low === "number"
        ? row.low
        : typeof row.minimum_value === "number"
          ? row.minimum_value
          : Number(row.low ?? row.minimum_value)
      const high = typeof row.high === "number"
        ? row.high
        : row.high == null
          ? null
          : Number(row.high)

      if (!Number.isFinite(stars) || !Number.isFinite(low)) {
        return null
      }

      return {
        stars,
        low,
        high: Number.isFinite(high as number) ? (high as number) : null,
      }
    })
    .filter((row): row is StarCutPoint => row !== null)
}

const mapDashboardCatalogMeasures = (catalog: StarCatalogResponse): StarCatalogMeasure[] =>
  catalog.measures.map((measure) => ({
    measureId: measure.measure_code,
    measureName: measure.measure_name,
    measurementYear: measure.measurement_year,
    weight: measure.weight,
    cutPoints: measure.cut_points.map((point) => ({
      stars: point.star,
      low: point.minimum_rate,
      high: null,
    })),
  }))

const mapIntegrationCatalogMeasures = (catalog: IntegrationCatalogResponse): StarCatalogMeasure[] =>
  (catalog.measures ?? []).map((measure) => ({
    measureId: measure.measure_id,
    measureName: measure.name ?? measure.measure_id,
    measurementYear: measure.measurement_year,
    weight: measure.weight ?? null,
    cutPoints: mapCatalogCutPoints(measure.cut_points),
  }))

const mapDashboardMeasureCards = (dashboard: StarDashboardResponse): StarMeasureCard[] =>
  dashboard.measure_cards.map((row) => ({
    measureId: row.measure_id,
    measureName: row.measure_name,
    measurementYear: row.measurement_year,
    starEligible: row.star_eligible,
    total: row.total_gaps,
    baselineNumerator: row.baseline_closed,
    projectedNumerator: row.projected_closed,
    baselineRate: row.baseline_rate,
    projectedRate: row.projected_rate,
    baselineStar: row.baseline_star,
    projectedStar: row.projected_star,
    starDelta: row.star_delta,
  }))

const mapIntegrationMeasureCards = (
  calculation: IntegrationCalculationResponse | null,
  catalogMeasures: StarCatalogMeasure[],
): StarMeasureCard[] => {
  const catalogByMeasureId = new Map<string, StarCatalogMeasure>()
  for (const measure of catalogMeasures) {
    catalogByMeasureId.set(measure.measureId, measure)
  }

  const rows = calculation?.measures ?? []
  if (rows.length === 0) {
    return catalogMeasures.map((measure) => ({
      measureId: measure.measureId,
      measureName: measure.measureName,
      measurementYear: measure.measurementYear,
      starEligible: measure.cutPoints.length > 0,
      total: 0,
      baselineNumerator: 0,
      projectedNumerator: 0,
      baselineRate: null,
      projectedRate: null,
      baselineStar: null,
      projectedStar: null,
      starDelta: null,
    }))
  }

  return rows.map((row) => {
    const catalog = catalogByMeasureId.get(row.measure)
    const denominator = Math.max(0, (row.denominator ?? 0) - (row.excluded ?? 0))
    const numerator = Math.max(0, row.met ?? 0)
    const rate = asRate(numerator, denominator)

    return {
      measureId: row.measure,
      measureName: row.name ?? catalog?.measureName ?? row.measure,
      measurementYear: catalog?.measurementYear ?? calculation?.measurement_year ?? new Date().getFullYear(),
      starEligible: Boolean(row.star_measure) || (catalog?.cutPoints.length ?? 0) > 0,
      total: denominator,
      baselineNumerator: numerator,
      projectedNumerator: numerator,
      baselineRate: rate,
      projectedRate: rate,
      baselineStar: null,
      projectedStar: null,
      starDelta: null,
    }
  })
}

export async function discoverStarCapabilities(): Promise<StarCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs)

  if (hasOpenApiPath(paths, "/api/stars/catalog") && hasOpenApiPath(paths, "/api/stars/calculate") && hasOpenApiPath(paths, "/api/stars/what-if")) {
    return { mode: "integration" }
  }

  if (hasOpenApiPath(paths, "/api/star-rating/catalog") && hasOpenApiPath(paths, "/api/star-rating/calculate") && hasOpenApiPath(paths, "/api/star-dashboard/calculate")) {
    return { mode: "dashboard" }
  }

  throw new Error("Star rating endpoints are not available in this backend profile.")
}

export async function loadStarOverview(
  capabilities: StarCapabilities,
  options?: { forceFresh?: boolean },
): Promise<StarOverview> {
  const forceFresh = Boolean(options?.forceFresh)
  const key = starOverviewCacheKey(capabilities.mode)

  if (!forceFresh) {
    const cached = starOverviewCache.get(key)
    if (cached && cached.expiresAt > now()) {
      return cached.value
    }

    const inFlight = starOverviewInFlight.get(key)
    if (inFlight) {
      return inFlight
    }
  }

  const request = (async () => {
    if (capabilities.mode === "integration") {
      const catalog = await fetchJson<IntegrationCatalogResponse>(withApiBase("/stars/catalog"), timeoutMs)
      const measures = mapIntegrationCatalogMeasures(catalog)

      const calculation = await fetchJson<IntegrationCalculationResponse>(withApiBase("/calculation"), timeoutMs)
        .catch(() => null)

      const measureCards = mapIntegrationMeasureCards(calculation, measures)

      return {
        mode: "integration",
        measurementYear: catalog.measurement_year ?? calculation?.measurement_year ?? null,
        cmsRatingYear: catalog.star_year ?? null,
        catalogUrl: null,
        notesUrl: null,
        measures,
        measureCards,
        overall: {
          ratedMeasures: measureCards.filter((row) => row.starEligible).length,
          baselineStar: null,
          projectedStar: null,
          delta: null,
        },
      } satisfies StarOverview
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
      mode: "dashboard",
      measurementYear: catalog.measures[0]?.measurement_year ?? null,
      cmsRatingYear: catalog.cms_rating_year,
      catalogUrl: catalog.cms_measure_set_url,
      notesUrl: catalog.cms_technical_notes_url,
      measures: mapDashboardCatalogMeasures(catalog),
      measureCards: mapDashboardMeasureCards(dashboard),
      overall: {
        ratedMeasures: dashboard.overall.rated_measure_count,
        baselineStar: dashboard.overall.baseline_star,
        projectedStar: dashboard.overall.projected_star,
        delta: dashboard.overall.star_delta,
      },
    } satisfies StarOverview
  })()
    .then((value) => {
      starOverviewCache.set(key, {
        expiresAt: now() + STAR_OVERVIEW_CACHE_MS,
        value,
      })
      return value
    })
    .finally(() => {
      starOverviewInFlight.delete(key)
    })

  starOverviewInFlight.set(key, request)
  return request
}

export async function loadStarWhatIf(
  capabilities: StarCapabilities,
  measureId: string,
  selectedGapIds: string[],
  options?: { forceFresh?: boolean },
): Promise<StarWhatIfResult> {
  const forceFresh = Boolean(options?.forceFresh)
  const key = starWhatIfCacheKey(capabilities.mode, measureId, selectedGapIds)

  if (!forceFresh) {
    const cached = starWhatIfCache.get(key)
    if (cached && cached.expiresAt > now()) {
      return cached.value
    }

    const inFlight = starWhatIfInFlight.get(key)
    if (inFlight) {
      return inFlight
    }
  }

  const request = (async () => {
    if (capabilities.mode === "integration") {
      const response = await postJson<IntegrationWhatIfResponse>(
        withApiBase("/stars/what-if"),
        timeoutMs,
        {
          measure_id: measureId,
          gap_ids: selectedGapIds,
        },
      )

      return {
        measureId: response.measure?.code ?? measureId,
        measureName: response.measure?.name ?? measureId,
        measurementYear: response.measure?.measurement_year ?? new Date().getFullYear(),
        cmsRatingYear: response.measure?.cms_rating_year ?? null,
        baselineRate: response.baseline?.performance_rate ?? null,
        projectedRate: response.recalculated?.performance_rate ?? null,
        baselineNumerator: response.baseline?.numerator ?? 0,
        baselineDenominator: response.baseline?.denominator ?? 0,
        projectedNumerator: response.recalculated?.numerator ?? 0,
        projectedDenominator: response.recalculated?.denominator ?? 0,
        baselineStar: response.baseline?.sample_star_rating ?? null,
        projectedStar: response.recalculated?.sample_star_rating ?? null,
        rateDelta: response.changes?.performance_rate_change_percentage_points ?? null,
        relativeImprovementPercent: response.changes?.relative_improvement_percent ?? null,
        starChanged: Boolean(response.changes?.star_rating_changed),
        selectedGapIds: response.selected_gap_ids ?? selectedGapIds,
        warnings: response.warnings ?? [],
        disclaimer: response.disclaimer ?? null,
        gaps: (response.care_gaps ?? []).map((gap) => {
          const status = gap.evidence_status ?? "unknown"
          const normalized = status.toLowerCase()
          const isClosed = normalized === "closed" || normalized === "complete" || normalized === "completed" || normalized === "resolved"
          return {
            gapId: gap.gap_id,
            memberId: "-",
            memberName: gap.description ?? "Gap",
            evidenceStatus: status,
            isClosed,
            eligibleForClosure: Boolean(gap.eligible_for_closure),
          }
        }),
      } satisfies StarWhatIfResult
    }

    const response = await postJson<StarDashboardResponse>(
      withApiBase(envConfig.dashboardSummaryPath),
      timeoutMs,
      {
        measure_id: measureId,
        selected_gap_ids: selectedGapIds,
      },
    )

    const selected = response.selected_measure

    return {
      measureId,
      measureName: selected?.measure_name ?? measureId,
      measurementYear: selected?.measurement_year ?? new Date().getFullYear(),
      cmsRatingYear: response.cms_reference.cms_rating_year,
      baselineRate: selected?.baseline_rate ?? null,
      projectedRate: selected?.projected_rate ?? null,
      baselineNumerator: selected?.baseline_closed ?? 0,
      baselineDenominator: selected?.total_gaps ?? 0,
      projectedNumerator: selected?.projected_closed ?? 0,
      projectedDenominator: selected?.total_gaps ?? 0,
      baselineStar: selected?.baseline_star ?? null,
      projectedStar: selected?.projected_star ?? null,
      rateDelta: selected && selected.baseline_rate != null && selected.projected_rate != null
        ? Number((selected.projected_rate - selected.baseline_rate).toFixed(2))
        : null,
      relativeImprovementPercent: selected && selected.baseline_rate != null && selected.projected_rate != null && selected.baseline_rate !== 0
        ? Number((((selected.projected_rate - selected.baseline_rate) / selected.baseline_rate) * 100).toFixed(2))
        : null,
      starChanged: Boolean(selected && selected.baseline_star != null && selected.projected_star != null && selected.baseline_star !== selected.projected_star),
      selectedGapIds: response.selected_gap_ids,
      warnings: [],
      disclaimer: response.cms_reference.note ?? null,
      gaps: (response.selected_measure_gaps ?? []).map((gap) => ({
        gapId: gap.care_gap_id,
        memberId: gap.member_id,
        memberName: gap.member_name,
        evidenceStatus: gap.status,
        isClosed: gap.is_closed,
        eligibleForClosure: !gap.is_closed,
      })),
    } satisfies StarWhatIfResult
  })()
    .then((value) => {
      starWhatIfCache.set(key, {
        expiresAt: now() + STAR_WHAT_IF_CACHE_MS,
        value,
      })
      return value
    })
    .finally(() => {
      starWhatIfInFlight.delete(key)
    })

  starWhatIfInFlight.set(key, request)
  return request
}

export async function runStarCalculation(
  capabilities: StarCapabilities,
  parameters: {
    measureId: string
    measurementYear: number
    cmsRatingYear: number
    rate: number | null
    denominator: number
  },
): Promise<StarCalculationResult> {
  if (capabilities.mode === "integration") {
    const response = await postJson<IntegrationCalculateResponse>(
      withApiBase("/stars/calculate"),
      timeoutMs,
      {
        measure_id: parameters.measureId,
        rate: parameters.rate,
        denominator: parameters.denominator,
      },
    )

    return {
      measureId: response.measure_code ?? parameters.measureId,
      measurementYear: response.measurement_year ?? parameters.measurementYear,
      cmsRatingYear: response.cms_rating_year ?? parameters.cmsRatingYear,
      inputScore: response.input_scoring_value ?? parameters.rate,
      appliedScore: response.applied_scoring_value ?? null,
      assignedStar: response.assigned_star_rating ?? null,
      ratingStatus: response.rating_status ?? "unknown",
      explanation: response.explanation ?? "No explanation returned.",
      cutPoints: mapCutPointsFromCalculation(response.applied_cut_points),
    }
  }

  const response = await postJson<IntegrationCalculateResponse>(
    withApiBase("/star-rating/calculate"),
    timeoutMs,
    {
      result: {
        measure_code: parameters.measureId,
        measurement_year: parameters.measurementYear,
        scoring_value: parameters.rate,
        denominator: parameters.denominator,
        result_status: "completed",
        scoring_value_kind: "cms_scoring_value",
      },
      cms_rating_year: parameters.cmsRatingYear,
    },
  )

  return {
    measureId: response.measure_code ?? parameters.measureId,
    measurementYear: response.measurement_year ?? parameters.measurementYear,
    cmsRatingYear: response.cms_rating_year ?? parameters.cmsRatingYear,
    inputScore: response.input_scoring_value ?? parameters.rate,
    appliedScore: response.applied_scoring_value ?? null,
    assignedStar: response.assigned_star_rating ?? null,
    ratingStatus: response.rating_status ?? "unknown",
    explanation: response.explanation ?? "No explanation returned.",
    cutPoints: mapCutPointsFromCalculation(response.applied_cut_points),
  }
}

export async function runStarComparison(
  capabilities: StarCapabilities,
  parameters: {
    measureId: string
    measurementYear: number
    cmsRatingYear: number
    denominator: number
    beforeRate: number | null
    afterRate: number | null
  },
): Promise<StarComparisonResult> {
  const [before, after] = await Promise.all([
    runStarCalculation(capabilities, {
      measureId: parameters.measureId,
      measurementYear: parameters.measurementYear,
      cmsRatingYear: parameters.cmsRatingYear,
      rate: parameters.beforeRate,
      denominator: parameters.denominator,
    }),
    runStarCalculation(capabilities, {
      measureId: parameters.measureId,
      measurementYear: parameters.measurementYear,
      cmsRatingYear: parameters.cmsRatingYear,
      rate: parameters.afterRate,
      denominator: parameters.denominator,
    }),
  ])

  return {
    before,
    after,
    starChanged: before.assignedStar != null && after.assignedStar != null && before.assignedStar !== after.assignedStar,
  }
}
