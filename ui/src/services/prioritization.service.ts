import { envConfig } from "../config/env"
import type { StarDashboardMeasureCard, StarDashboardResponse } from "../models/dashboard"
import type {
  PrioritizationBreakdown,
  PrioritizationCapabilities,
  PrioritizationGapDetail,
  PrioritizationImpact,
  PrioritizationMeasureSummary,
  PrioritizationOverview,
  PrioritizationWorklistFilters,
  PrioritizationWorklistItem,
} from "../models/prioritization"
import { fetchJson, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

interface RawMeasuresResponse {
  measures?: Array<{
    measure?: string
    name?: string
    open_gaps?: number
    numerator?: number
    denominator?: number
    rate?: number | null
    rounded?: number | null
    stars?: number | null
    projected_rate?: number | null
    projected_stars?: number | null
    value?: number | null
    value_reason?: string | null
    confirmed_pending_engine?: number
    excluded_pending_engine?: number
  }>
  tiers?: Partial<Record<"high" | "medium" | "low", number>>
  last_scored_at?: string | null
  rules_version?: string
  cms_rating_year?: number | null
  evidence?: {
    proposed?: number
    confirmed?: number
    rejected?: number
    supplemental_unexported?: number
    supplemental_waiting_for_parts?: number
  }
}

interface RawWorklistItem {
  key?: string
  gap_id?: string
  measure?: string
  measure_name?: string
  tier?: string
  composite?: number
  likelihood?: number
  value?: number
  engagement?: number
  channel?: string
  reasons?: string[] | string | null
  action?: string | null
  patient_id?: string
  missing_element?: string | null
  review_status?: string
  pending_count?: number
  next_visit?: string | null
  has_documents?: boolean
}

interface RawGapDetailResponse {
  gap?: {
    gap_id?: string
    key?: string
    patient_id?: string
    measure_id?: string
    measure_name?: string
    measurement_year?: number
    status?: string
    review_status?: string
    pending_count?: number
    missing_element?: string | null
    deadline_date?: string | null
    last_relevant_event_date?: string | null
    last_relevant_event_description?: string | null
    tier?: string | null
    composite_score?: number | null
    recommended_channel?: string | null
    reasons?: string[] | string | null
    action_reason?: string | null
  }
  breakdown?: {
    measure?: string
    as_of?: string
    likelihood?: {
      result?: number
      raw?: number
      min?: number
      max?: number
      steps?: Array<{ label?: string; change?: number }>
    }
    value?: {
      result?: number
      reason?: string
      weight?: number | null
      numerator?: number
      denominator?: number
      rate?: number | null
      rounded?: number | null
      stars?: number | null
      next_cut?: number | null
      target?: number | null
      needed?: number | null
    }
    engagement?: {
      result?: number
      cap?: number
      steps?: Array<{ label?: string; change?: number }>
    }
    composite?: number
    channel?: string
  } | null
  impact?: {
    available?: boolean
    note?: string | null
    rate_now?: number | null
    rate_after?: number | null
    change_points?: number | null
    stars_now?: number | null
    stars_after?: number | null
    closures_for_next_star?: number | null
    exclusion?: {
      rate_after?: number | null
      stars_after?: number | null
      change_points?: number | null
    } | null
  } | null
  documents?: unknown[]
  evidence?: Array<{ status?: string }>
  ai_runs?: unknown[]
}

const timeoutMs = envConfig.apiTimeoutMs
const DASHBOARD_SNAPSHOT_CACHE_MS = 20_000
const GAP_DETAIL_CACHE_MS = 15_000
const OVERVIEW_CACHE_MS = 10_000
const WORKLIST_CACHE_MS = 10_000

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const now = (): number => Date.now()

type DashboardSnapshotCacheEntry = {
  expiresAt: number
  value: StarDashboardResponse
}

type GapDetailCacheEntry = {
  expiresAt: number
  value: RawGapDetailResponse
}

type OverviewCacheEntry = {
  expiresAt: number
  value: RawMeasuresResponse
}

type WorklistCacheEntry = {
  expiresAt: number
  value: RawWorklistItem[]
}

const dashboardSnapshotCache = new Map<string, DashboardSnapshotCacheEntry>()
const dashboardSnapshotInFlight = new Map<string, Promise<StarDashboardResponse>>()
const gapDetailCache = new Map<string, GapDetailCacheEntry>()
const gapDetailInFlight = new Map<string, Promise<RawGapDetailResponse>>()
let overviewCache: OverviewCacheEntry | null = null
let overviewInFlight: Promise<RawMeasuresResponse> | null = null
const worklistCache = new Map<string, WorklistCacheEntry>()
const worklistInFlight = new Map<string, Promise<RawWorklistItem[]>>()

const dashboardSnapshotCacheKey = (measureId: string | null, selectedGapIds: string[]): string => {
  const selectedPart = selectedGapIds.length > 0 ? [...selectedGapIds].sort().join(",") : "none"
  return `${measureId ?? "__default__"}::${selectedPart}`
}

const thirdSize = (count: number): number => Math.max(1, Math.ceil(count / 3))

const buildTierByMeasure = (rows: StarDashboardMeasureCard[]): Map<string, "high" | "medium" | "low"> => {
  const sorted = [...rows].sort((left, right) => right.total_gaps - left.total_gaps)
  const step = thirdSize(sorted.length)
  const tierByMeasure = new Map<string, "high" | "medium" | "low">()

  sorted.forEach((row, index) => {
    if (index < step) {
      tierByMeasure.set(row.measure_id, "high")
      return
    }
    if (index < step * 2) {
      tierByMeasure.set(row.measure_id, "medium")
      return
    }
    tierByMeasure.set(row.measure_id, "low")
  })

  return tierByMeasure
}

const tierGapCounts = (rows: StarDashboardMeasureCard[]): { high: number; medium: number; low: number } => {
  const tierByMeasure = buildTierByMeasure(rows)
  let high = 0
  let medium = 0
  let low = 0

  for (const row of rows) {
    const tier = tierByMeasure.get(row.measure_id)
    const openGaps = Math.max(0, row.total_gaps - row.baseline_closed)
    if (tier === "high") {
      high += openGaps
    } else if (tier === "medium") {
      medium += openGaps
    } else {
      low += openGaps
    }
  }

  return { high, medium, low }
}

const loadDashboardSnapshot = async (
  measureId: string | null,
  selectedGapIds: string[] = [],
): Promise<StarDashboardResponse> => {
  const key = dashboardSnapshotCacheKey(measureId, selectedGapIds)
  const cached = dashboardSnapshotCache.get(key)
  if (cached && cached.expiresAt > now()) {
    return cached.value
  }

  const inFlight = dashboardSnapshotInFlight.get(key)
  if (inFlight) {
    return inFlight
  }

  const request = postJson<StarDashboardResponse>(
    withApiBase(envConfig.dashboardSummaryPath),
    timeoutMs,
    {
      measure_id: measureId,
      selected_gap_ids: selectedGapIds,
    },
  )
    .then((value) => {
      dashboardSnapshotCache.set(key, {
        expiresAt: now() + DASHBOARD_SNAPSHOT_CACHE_MS,
        value,
      })
      return value
    })
    .finally(() => {
      dashboardSnapshotInFlight.delete(key)
    })

  dashboardSnapshotInFlight.set(key, request)
  return request
}

const loadGapDetailCached = async (gapId: string): Promise<RawGapDetailResponse> => {
  const cached = gapDetailCache.get(gapId)
  if (cached && cached.expiresAt > now()) {
    return cached.value
  }

  const inFlight = gapDetailInFlight.get(gapId)
  if (inFlight) {
    return inFlight
  }

  const request = fetchJson<RawGapDetailResponse>(
    withApiBase(`/gaps/${encodeURIComponent(gapId)}`),
    timeoutMs,
  )
    .then((value) => {
      gapDetailCache.set(gapId, {
        expiresAt: now() + GAP_DETAIL_CACHE_MS,
        value,
      })
      return value
    })
    .finally(() => {
      gapDetailInFlight.delete(gapId)
    })

  gapDetailInFlight.set(gapId, request)
  return request
}

const loadOverviewCached = async (): Promise<RawMeasuresResponse> => {
  const cached = overviewCache
  if (cached && cached.expiresAt > now()) {
    return cached.value
  }

  if (overviewInFlight) {
    return overviewInFlight
  }

  overviewInFlight = fetchJson<RawMeasuresResponse>(withApiBase("/measures"), timeoutMs)
    .then((value) => {
      overviewCache = {
        expiresAt: now() + OVERVIEW_CACHE_MS,
        value,
      }
      return value
    })
    .finally(() => {
      overviewInFlight = null
    })

  return overviewInFlight
}

const loadWorklistCached = async (queryString: string): Promise<RawWorklistItem[]> => {
  const cached = worklistCache.get(queryString)
  if (cached && cached.expiresAt > now()) {
    return cached.value
  }

  const inFlight = worklistInFlight.get(queryString)
  if (inFlight) {
    return inFlight
  }

  const request = fetchJson<RawWorklistItem[]>(withApiBase(`/gaps?${queryString}`), timeoutMs)
    .then((value) => {
      worklistCache.set(queryString, {
        expiresAt: now() + WORKLIST_CACHE_MS,
        value,
      })
      return value
    })
    .finally(() => {
      worklistInFlight.delete(queryString)
    })

  worklistInFlight.set(queryString, request)
  return request
}

const toReasons = (value: string[] | string | null | undefined): string[] => {
  if (Array.isArray(value)) {
    return value.filter((item) => typeof item === "string" && item.trim().length > 0)
  }
  if (typeof value === "string" && value.trim().length > 0) {
    return [value.trim()]
  }
  return []
}

const toMeasureSummary = (row: NonNullable<RawMeasuresResponse["measures"]>[number]): PrioritizationMeasureSummary => ({
  measureId: row?.measure ?? "",
  measureName: row?.name ?? row?.measure ?? "",
  openGaps: row?.open_gaps ?? 0,
  numerator: row?.numerator ?? 0,
  denominator: row?.denominator ?? 0,
  rate: row?.rate ?? null,
  roundedRate: row?.rounded ?? null,
  stars: row?.stars ?? null,
  projectedRate: row?.projected_rate ?? null,
  projectedStars: row?.projected_stars ?? null,
  valueScore: row?.value ?? null,
  valueReason: row?.value_reason ?? null,
  confirmedPendingEngine: row?.confirmed_pending_engine ?? 0,
  excludedPendingEngine: row?.excluded_pending_engine ?? 0,
})

const toBreakdown = (value: RawGapDetailResponse["breakdown"]): PrioritizationBreakdown | null => {
  if (!value) {
    return null
  }

  return {
    measureId: value.measure ?? "",
    asOf: value.as_of ?? "",
    likelihood: {
      result: value.likelihood?.result ?? 0,
      raw: value.likelihood?.raw ?? 0,
      min: value.likelihood?.min ?? 0,
      max: value.likelihood?.max ?? 0,
      steps: (value.likelihood?.steps ?? []).map((step) => ({
        label: step.label ?? "",
        change: step.change ?? 0,
      })),
    },
    value: {
      result: value.value?.result ?? 0,
      reason: value.value?.reason ?? "",
      weight: value.value?.weight ?? null,
      numerator: value.value?.numerator ?? 0,
      denominator: value.value?.denominator ?? 0,
      rate: value.value?.rate ?? null,
      rounded: value.value?.rounded ?? null,
      stars: value.value?.stars ?? null,
      nextCut: value.value?.next_cut ?? null,
      target: value.value?.target ?? null,
      needed: value.value?.needed ?? null,
    },
    engagement: {
      result: value.engagement?.result ?? 0,
      cap: value.engagement?.cap ?? 0,
      steps: (value.engagement?.steps ?? []).map((step) => ({
        label: step.label ?? "",
        change: step.change ?? 0,
      })),
    },
    composite: value.composite ?? 0,
    channel: value.channel ?? "",
  }
}

const toImpact = (value: RawGapDetailResponse["impact"]): PrioritizationImpact | null => {
  if (!value) {
    return null
  }

  return {
    available: Boolean(value.available),
    note: value.note ?? null,
    rateNow: value.rate_now ?? null,
    rateAfter: value.rate_after ?? null,
    changePoints: value.change_points ?? null,
    starsNow: value.stars_now ?? null,
    starsAfter: value.stars_after ?? null,
    closuresForNextStar: value.closures_for_next_star ?? null,
    exclusion: value.exclusion
      ? {
        rateAfter: value.exclusion.rate_after ?? null,
        starsAfter: value.exclusion.stars_after ?? null,
        changePoints: value.exclusion.change_points ?? null,
      }
      : null,
  }
}

export async function discoverPrioritizationCapabilities(): Promise<PrioritizationCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs)

  if (hasOpenApiPath(paths, "/api/measures") && hasOpenApiPath(paths, "/api/gaps") && hasOpenApiPath(paths, "/api/gaps/{gap_id}")) {
    return { mode: "integration" }
  }

  if (hasOpenApiPath(paths, "/api/star-dashboard/calculate") && hasOpenApiPath(paths, "/api/star-rating/catalog")) {
    return { mode: "dashboard" }
  }

  throw new Error("Prioritization endpoints are not available in this backend profile.")
}

export async function loadPrioritizationOverview(
  capabilities: PrioritizationCapabilities,
): Promise<PrioritizationOverview> {
  if (capabilities.mode === "integration") {
    const response = await loadOverviewCached()

    return {
      mode: "integration",
      rulesVersion: response.rules_version ?? "-",
      cmsRatingYear: response.cms_rating_year ?? null,
      lastScoredAt: response.last_scored_at ?? null,
      tiers: {
        high: response.tiers?.high ?? 0,
        medium: response.tiers?.medium ?? 0,
        low: response.tiers?.low ?? 0,
      },
      evidence: {
        proposed: response.evidence?.proposed ?? 0,
        confirmed: response.evidence?.confirmed ?? 0,
        rejected: response.evidence?.rejected ?? 0,
        supplementalUnexported: response.evidence?.supplemental_unexported ?? 0,
        supplementalWaitingForParts: response.evidence?.supplemental_waiting_for_parts ?? 0,
      },
      measures: (response.measures ?? []).map(toMeasureSummary),
    }
  }

  const dashboard = await loadDashboardSnapshot(envConfig.dashboardDefaultMeasureId ?? null)
  const tiers = tierGapCounts(dashboard.measure_cards)
  const measures = dashboard.measure_cards.map((row) => ({
    measureId: row.measure_id,
    measureName: row.measure_name,
    openGaps: Math.max(0, row.total_gaps - row.baseline_closed),
    numerator: row.baseline_closed,
    denominator: row.total_gaps,
    rate: row.baseline_rate,
    roundedRate: row.baseline_rate == null ? null : Math.round(row.baseline_rate),
    stars: row.baseline_star,
    projectedRate: row.projected_rate,
    projectedStars: row.projected_star,
    valueScore: null,
    valueReason: null,
    confirmedPendingEngine: 0,
    excludedPendingEngine: 0,
  }))

  return {
    mode: "dashboard",
    rulesVersion: dashboard.cms_reference.note ?? "dashboard-profile",
    cmsRatingYear: dashboard.cms_reference.cms_rating_year,
    lastScoredAt: null,
    tiers,
    evidence: {
      proposed: null,
      confirmed: null,
      rejected: null,
      supplementalUnexported: null,
      supplementalWaitingForParts: null,
    },
    measures,
  }
}

export async function loadPrioritizationWorklist(
  capabilities: PrioritizationCapabilities,
  filters: PrioritizationWorklistFilters,
): Promise<PrioritizationWorklistItem[]> {
  if (capabilities.mode === "integration") {
    const query = new URLSearchParams()
    if (filters.role) {
      query.set("role", filters.role)
    }
    if (filters.tier) {
      query.set("tier", filters.tier)
    }
    if (filters.measure) {
      query.set("measure", filters.measure)
    }
    query.set("limit", String(filters.limit))
    const queryString = query.toString()

    const response = await loadWorklistCached(queryString)

    return response.map((row) => ({
      key: row.key ?? row.gap_id ?? "",
      gapId: row.gap_id ?? "",
      measureId: row.measure ?? "",
      measureName: row.measure_name ?? row.measure ?? "",
      tier: row.tier ?? "",
      composite: row.composite ?? 0,
      likelihood: row.likelihood ?? 0,
      value: row.value ?? 0,
      engagement: row.engagement ?? 0,
      channel: row.channel ?? null,
      reasons: toReasons(row.reasons),
      actionReason: row.action ?? null,
      patientId: row.patient_id ?? "",
      missingElement: row.missing_element ?? null,
      reviewStatus: row.review_status ?? "open",
      pendingCount: row.pending_count ?? 0,
      nextVisit: row.next_visit ?? null,
      hasDocuments: Boolean(row.has_documents),
    }))
  }

  const seed = await loadDashboardSnapshot(envConfig.dashboardDefaultMeasureId ?? null)
  const tierByMeasure = buildTierByMeasure(seed.measure_cards)

  const targetMeasures = filters.measure
    ? seed.measure_cards.filter((measure) => measure.measure_id === filters.measure)
    : seed.measure_cards

  const details = await Promise.all(
    targetMeasures.map((measure) => loadDashboardSnapshot(measure.measure_id)),
  )

  const rows: PrioritizationWorklistItem[] = []
  for (const item of details) {
    const measure = item.selected_measure
    if (!measure) {
      continue
    }

    const tier = tierByMeasure.get(measure.measure_id) ?? "low"
    if (filters.tier && filters.tier !== tier) {
      continue
    }

    for (const gap of item.selected_measure_gaps ?? []) {
      if (gap.is_closed) {
        continue
      }

      rows.push({
        key: gap.care_gap_id,
        gapId: gap.care_gap_id,
        measureId: measure.measure_id,
        measureName: measure.measure_name,
        tier,
        composite: null,
        likelihood: null,
        value: null,
        engagement: null,
        channel: null,
        reasons: [],
        actionReason: null,
        patientId: gap.member_id,
        missingElement: null,
        reviewStatus: gap.status,
        pendingCount: 0,
        nextVisit: null,
        hasDocuments: false,
      })
    }
  }

  const sorted = rows
    .sort((left, right) => {
      const tierRank = { high: 0, medium: 1, low: 2 } as const
      const leftRank = tierRank[(left.tier as "high" | "medium" | "low")] ?? 3
      const rightRank = tierRank[(right.tier as "high" | "medium" | "low")] ?? 3
      if (leftRank !== rightRank) {
        return leftRank - rightRank
      }
      if (left.measureId !== right.measureId) {
        return left.measureId.localeCompare(right.measureId)
      }
      return left.gapId.localeCompare(right.gapId)
    })
    .slice(0, filters.limit)

  return sorted
}

export async function loadPrioritizationGapDetail(
  capabilities: PrioritizationCapabilities,
  gapId: string,
  measureId: string,
): Promise<PrioritizationGapDetail> {
  if (capabilities.mode === "integration") {
    const response = await loadGapDetailCached(gapId)

    const evidenceRows = response.evidence ?? []
    const proposed = evidenceRows.filter((row) => (row.status ?? "").toLowerCase() === "proposed").length
    const confirmed = evidenceRows.filter((row) => (row.status ?? "").toLowerCase() === "confirmed").length
    const rejected = evidenceRows.filter((row) => (row.status ?? "").toLowerCase() === "rejected").length

    return {
      gap: {
        gapId: response.gap?.gap_id ?? gapId,
        key: response.gap?.key ?? gapId,
        patientId: response.gap?.patient_id ?? "",
        measureId: response.gap?.measure_id ?? "",
        measureName: response.gap?.measure_name ?? response.gap?.measure_id ?? "",
        measurementYear: response.gap?.measurement_year ?? new Date().getFullYear(),
        status: response.gap?.status ?? "open",
        reviewStatus: response.gap?.review_status ?? "open",
        pendingCount: response.gap?.pending_count ?? 0,
        missingElement: response.gap?.missing_element ?? null,
        deadlineDate: response.gap?.deadline_date ?? null,
        lastRelevantEventDate: response.gap?.last_relevant_event_date ?? null,
        lastRelevantEventDescription: response.gap?.last_relevant_event_description ?? null,
        tier: response.gap?.tier ?? null,
        compositeScore: response.gap?.composite_score ?? null,
        recommendedChannel: response.gap?.recommended_channel ?? null,
        reasons: toReasons(response.gap?.reasons),
        actionReason: response.gap?.action_reason ?? null,
      },
      breakdown: toBreakdown(response.breakdown),
      impact: toImpact(response.impact),
      documentsCount: response.documents?.length ?? 0,
      evidenceSummary: {
        proposed,
        confirmed,
        rejected,
      },
      aiRunsCount: response.ai_runs?.length ?? 0,
    }
  }

  const snapshot = await loadDashboardSnapshot(measureId, [gapId])

  const selectedMeasure = snapshot.selected_measure
  const selectedGap = snapshot.selected_measure_gaps.find((row) => row.care_gap_id === gapId)

  return {
    gap: {
      gapId,
      key: gapId,
      patientId: selectedGap?.member_id ?? "",
      measureId: measureId,
      measureName: selectedMeasure?.measure_name ?? measureId,
      measurementYear: selectedMeasure?.measurement_year ?? new Date().getFullYear(),
      status: selectedGap?.status ?? "open",
      reviewStatus: selectedGap?.selected_for_projection ? "selected" : "open",
      pendingCount: 0,
      missingElement: null,
      deadlineDate: null,
      lastRelevantEventDate: null,
      lastRelevantEventDescription: null,
      tier: null,
      compositeScore: null,
      recommendedChannel: null,
      reasons: [],
      actionReason: null,
    },
    breakdown: null,
    impact: selectedMeasure
      ? {
        available: true,
        note: snapshot.cms_reference.note ?? null,
        rateNow: selectedMeasure.baseline_rate,
        rateAfter: selectedMeasure.projected_rate,
        changePoints: selectedMeasure.baseline_rate == null || selectedMeasure.projected_rate == null
          ? null
          : Number((selectedMeasure.projected_rate - selectedMeasure.baseline_rate).toFixed(2)),
        starsNow: selectedMeasure.baseline_star,
        starsAfter: selectedMeasure.projected_star,
        closuresForNextStar: null,
        exclusion: null,
      }
      : {
        available: false,
        note: "Impact is not available for this gap in dashboard profile.",
        rateNow: null,
        rateAfter: null,
        changePoints: null,
        starsNow: null,
        starsAfter: null,
        closuresForNextStar: null,
        exclusion: null,
      },
    documentsCount: 0,
    evidenceSummary: {
      proposed: 0,
      confirmed: 0,
      rejected: 0,
    },
    aiRunsCount: 0,
  }
}
