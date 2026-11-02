import { envConfig } from "../config/env"
import type { StarDashboardResponse } from "../models/dashboard"
import type {
  EvidenceAttempt,
  EvidenceCallExchange,
  EvidenceCapabilities,
  EvidenceContextQuote,
  EvidenceDecisionResult,
  EvidenceExportResult,
  EvidenceExportRun,
  EvidenceExtractionResult,
  EvidenceGapBreakdown,
  EvidenceGapDetail,
  EvidenceGapImpact,
  EvidenceLatestRun,
  EvidenceModelAcceptance,
  EvidenceOverview,
  EvidenceRuleCheck,
  EvidenceRuleCheckPart,
  EvidenceQueueItem,
  EvidenceScopeGap,
} from "../models/evidence"
import { fetchJson, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

interface RawMeasuresResponse {
  evidence?: {
    proposed?: number
    confirmed?: number
    rejected?: number
    supplemental_total?: number
    supplemental_unexported?: number
    supplemental_waiting_for_parts?: number
    exports?: Array<{
      id?: number
      run_at?: string | null
      row_count?: number
      file_sha256?: string
    }>
    by_model?: Array<{
      model?: string
      proposed?: number
      confirmed?: number
      rejected?: number
      acceptance_rate?: number | null
    }>
  }
}

interface RawGapRow {
  key?: string
  gap_id?: string
  patient_id?: string
  measure?: string
  measure_name?: string
  missing_element?: string
  deadline_date?: string | null
  review_status?: string
  pending_count?: number
  has_documents?: boolean
  tier?: string
  composite?: number
}

interface RawRuleCheckPart {
  part?: string
  status?: string
  met_by?: number
  required?: number
}

interface RawRuleCheck {
  window_from?: string | null
  window_to?: string | null
  window_text?: string | null
  service_date_from?: string | null
  service_date_to?: string | null
  inside_window?: boolean | null
  parts?: RawRuleCheckPart[]
  part_label?: string | null
  part_count?: number
}

interface RawContextQuote {
  text?: string
  quote_start?: number
  quote_end?: number
  starts_mid_page?: boolean
  ends_mid_page?: boolean
}

interface RawAttempt {
  attempt_id?: number
  model?: string | null
  started_at?: string | null
  duration_ms?: number | null
  kept?: number
  dropped?: number
}

interface RawGapImpact {
  available?: boolean
  note?: string | null
  rate_now?: number | null
  rate_after?: number | null
  change_points?: number | null
  stars_now?: number | null
  stars_after?: number | null
  star_changed?: boolean
  closures_for_next_star?: number | null
  exclusion?: {
    rate_after?: number | null
    stars_after?: number | null
    change_points?: number | null
  }
}

interface RawGapBreakdown {
  likelihood?: {
    steps?: Array<{ label?: string; change?: number }>
    raw?: number
    min?: number
    max?: number
    result?: number
  }
  value?: {
    weight?: number | null
    numerator?: number | null
    denominator?: number | null
    rate?: number | null
    rounded?: number | null
    stars?: number | null
    next_cut?: number | null
    target?: number | null
    needed?: number | null
    divisor?: number | null
    reason?: string | null
    result?: number | null
  }
  engagement?: {
    steps?: Array<{ label?: string; change?: number }>
    cap?: number
    result?: number
  }
  composite?: number
  channel?: string
}

interface RawGapDetail {
  gap?: {
    key?: string
    gap_id?: string
    patient_id?: string
    measure_id?: string
    measure_name?: string
    missing_element?: string
    tier?: string
    deadline_date?: string | null
    last_relevant_event_date?: string | null
    last_relevant_event_description?: string | null
    review_status?: string
    pending_count?: number
  }
  breakdown?: RawGapBreakdown | null
  impact?: RawGapImpact | null
  documents?: Array<{
    document_id?: string
    doc_type?: string
    document_date?: string | null
    conversion_status?: string | null
    page_count?: number | null
  }>
  evidence?: Array<{
    evidence_id?: string
    status?: string
    excerpt?: string | null
    proposed_finding?: string | null
    confidence?: number | null
    model?: string | null
    decision_reason?: string | null
    reason?: string | null
    evidence_kind?: string | null
    evidence_type?: string | null
    service_date_raw?: string | null
    service_date_from?: string | null
    service_date_to?: string | null
    value?: string | null
    document_id?: string
    page_no?: number
    context?: RawContextQuote | null
    rule_check?: RawRuleCheck | null
    attempt?: RawAttempt | null
  }>
  ai_runs?: Array<{
    run_id?: number
    started_at?: string | null
    model_name?: string | null
    status?: string
    kept?: number
    dropped_count?: number
    duration_ms?: number | null
    error?: string | null
    prompt_version?: string | null
    pages_sent?: Array<{
      document_id?: string
      page_no?: number
      method?: string | null
      handwritten?: boolean
    }>
    dropped?: Array<{
      evidence_type?: string | null
      reason?: string
      evidence_id?: string
    }>
    raw_response?: string | null
  }>
}

interface RawHealthResponse {
  openrouter?: {
    reachable?: boolean
    key_valid?: boolean | null
    model?: string | null
    model_listed?: boolean | null
  }
}

interface RawExtractionResponse {
  run_id?: number
  pages?: Array<{
    document_id?: string
    page_no?: number
    method?: string | null
    handwritten?: boolean
  }>
  calls?: RawExtractionCall[]
  kept?: Array<{
    evidence_type?: string | null
    document_id?: string
    page_no?: number
    excerpt?: string
  }>
  dropped?: Array<{
    evidence_type?: string | null
    document_id?: string | null
    reason?: string
    evidence_id?: string
  }>
  saved?: number
  skipped_already_saved?: number
  note?: string | null
}

interface RawExtractionCall {
  status?: number
  seconds?: number
  error?: string | null
  request?: {
    messages?: Array<{
      role?: string
      content?: string
    }>
  }
  answer?: string | null
}

interface RawDecisionResponse {
  status?: string
  gap_id?: string
  review_status?: string
  supplemental_record_id?: number | null
}

interface RawExportResponse {
  rows?: number
  export_run_id?: number
  file_path?: string
  sha256?: string
}

const timeoutMs = envConfig.apiTimeoutMs

const MAX_QUEUE_GAPS = 60
const HEALTH_TIMEOUT_MS = Math.min(timeoutMs, 1200)
const HEALTH_CACHE_MS = 30_000
const GAP_DETAIL_CACHE_MS = 20_000

const withApiBase = (path: string): string => `${envConfig.apiBaseUrl}${path}`

const now = (): number => Date.now()

type GapDetailCacheEntry = {
  expiresAt: number
  value: RawGapDetail
}

type HealthCacheEntry = {
  expiresAt: number
  value: RawHealthResponse | null
}

let healthCache: HealthCacheEntry | null = null
const gapDetailCache = new Map<string, GapDetailCacheEntry>()
const gapDetailInFlight = new Map<string, Promise<RawGapDetail>>()

const tierRank = (tier: string | null | undefined): number => {
  const normalized = (tier ?? "").trim().toLowerCase()
  if (normalized === "high") {
    return 0
  }
  if (normalized === "medium") {
    return 1
  }
  if (normalized === "low") {
    return 2
  }
  return 3
}

const toRuleCheckParts = (parts: RawRuleCheckPart[] | undefined): EvidenceRuleCheckPart[] => (parts ?? []).map((part) => ({
  part: part.part ?? "",
  status: part.status ?? "",
  metBy: part.met_by ?? 0,
  required: part.required ?? 0,
}))

const toRuleCheck = (value: RawRuleCheck | null | undefined): EvidenceRuleCheck | null => {
  if (!value) {
    return null
  }

  return {
    windowFrom: value.window_from ?? null,
    windowTo: value.window_to ?? null,
    windowText: value.window_text ?? null,
    serviceDateFrom: value.service_date_from ?? null,
    serviceDateTo: value.service_date_to ?? null,
    insideWindow: value.inside_window ?? null,
    parts: toRuleCheckParts(value.parts),
    partLabel: value.part_label ?? null,
    partCount: value.part_count ?? 0,
  }
}

const toContextQuote = (value: RawContextQuote | null | undefined): EvidenceContextQuote | null => {
  if (!value || !value.text) {
    return null
  }

  return {
    text: value.text,
    quoteStart: value.quote_start ?? 0,
    quoteEnd: value.quote_end ?? 0,
    startsMidPage: Boolean(value.starts_mid_page),
    endsMidPage: Boolean(value.ends_mid_page),
  }
}

const toAttempt = (value: RawAttempt | null | undefined): EvidenceAttempt | null => {
  if (!value || typeof value.attempt_id !== "number") {
    return null
  }

  return {
    attemptId: value.attempt_id,
    model: value.model ?? null,
    startedAt: value.started_at ?? null,
    durationMs: value.duration_ms ?? null,
    kept: value.kept ?? 0,
    dropped: value.dropped ?? 0,
  }
}

const toImpact = (value: RawGapImpact | null | undefined): EvidenceGapImpact | null => {
  if (!value) {
    return null
  }

  return {
    available: value.available !== false,
    note: value.note ?? null,
    rateNow: value.rate_now ?? null,
    rateAfter: value.rate_after ?? null,
    changePoints: value.change_points ?? null,
    starsNow: value.stars_now ?? null,
    starsAfter: value.stars_after ?? null,
    starChanged: value.star_changed ?? null,
    closuresForNextStar: value.closures_for_next_star ?? null,
    exclusionRateAfter: value.exclusion?.rate_after ?? null,
    exclusionStarsAfter: value.exclusion?.stars_after ?? null,
    exclusionChangePoints: value.exclusion?.change_points ?? null,
  }
}

const toBreakdown = (value: RawGapBreakdown | null | undefined): EvidenceGapBreakdown | null => {
  if (!value) {
    return null
  }

  return {
    likelihood: value.likelihood
      ? {
        steps: (value.likelihood.steps ?? []).map((step) => ({
          label: step.label ?? "",
          change: step.change ?? 0,
        })),
        raw: value.likelihood.raw ?? null,
        min: value.likelihood.min ?? null,
        max: value.likelihood.max ?? null,
        result: value.likelihood.result ?? null,
      }
      : null,
    value: value.value
      ? {
        weight: value.value.weight ?? null,
        numerator: value.value.numerator ?? null,
        denominator: value.value.denominator ?? null,
        rate: value.value.rate ?? null,
        rounded: value.value.rounded ?? null,
        stars: value.value.stars ?? null,
        nextCut: value.value.next_cut ?? null,
        target: value.value.target ?? null,
        needed: value.value.needed ?? null,
        divisor: value.value.divisor ?? null,
        reason: value.value.reason ?? null,
        result: value.value.result ?? null,
      }
      : null,
    engagement: value.engagement
      ? {
        steps: (value.engagement.steps ?? []).map((step) => ({
          label: step.label ?? "",
          change: step.change ?? 0,
        })),
        cap: value.engagement.cap ?? null,
        result: value.engagement.result ?? null,
      }
      : null,
    composite: value.composite ?? null,
    channel: value.channel ?? null,
  }
}

const toAcceptanceByModel = (
  rows: NonNullable<RawMeasuresResponse["evidence"]>["by_model"] | undefined,
): EvidenceModelAcceptance[] => (rows ?? []).map((row) => ({
  model: row.model ?? "unknown",
  proposed: row.proposed ?? 0,
  confirmed: row.confirmed ?? 0,
  rejected: row.rejected ?? 0,
  acceptanceRate: row.acceptance_rate ?? null,
}))

const toScopeGap = (row: RawGapRow): EvidenceScopeGap => ({
  gapId: row.gap_id ?? "",
  key: row.key ?? row.gap_id ?? "",
  patientId: row.patient_id ?? "",
  measureId: row.measure ?? "",
  measureName: row.measure_name ?? row.measure ?? "",
  tier: row.tier ?? "",
  pendingCount: row.pending_count ?? 0,
  hasDocuments: Boolean(row.has_documents),
  missingElement: row.missing_element ?? "",
  reviewStatus: row.review_status ?? "open",
  deadlineDate: row.deadline_date ?? null,
})

const toQueueRows = (detail: RawGapDetail): EvidenceQueueItem[] => {
  const gap = detail.gap
  const impact = toImpact(detail.impact)
  const gapId = gap?.gap_id ?? ""
  const gapKey = gap?.key ?? gapId
  const patientId = gap?.patient_id ?? ""
  const measureId = gap?.measure_id ?? ""
  const measureName = gap?.measure_name ?? measureId
  const tier = gap?.tier ?? ""
  const missingElement = gap?.missing_element ?? ""
  const deadlineDate = gap?.deadline_date ?? null
  const reviewStatus = gap?.review_status ?? "open"

  return (detail.evidence ?? [])
    .filter((row) => row.status === "proposed")
    .map((row) => ({
      proposalId: row.evidence_id ?? "",
      gapId,
      gapKey,
      patientId,
      measureId,
      measureName,
      tier,
      missingElement,
      deadlineDate,
      documentId: row.document_id ?? "",
      pageNo: row.page_no ?? null,
      evidenceKind: row.evidence_kind ?? "",
      evidenceType: row.evidence_type ?? "",
      serviceDateRaw: row.service_date_raw ?? null,
      serviceDateFrom: row.service_date_from ?? null,
      serviceDateTo: row.service_date_to ?? null,
      value: row.value ?? null,
      excerpt: row.excerpt?.trim() || "No excerpt available from backend.",
      proposedFinding: row.proposed_finding?.trim() || "No proposed finding text available.",
      confidence: row.confidence ?? null,
      model: row.model ?? null,
      reason: row.reason ?? null,
      status: row.status ?? "proposed",
      reviewStatus,
      decisionReason: row.decision_reason ?? null,
      context: toContextQuote(row.context),
      ruleCheck: toRuleCheck(row.rule_check),
      attempt: toAttempt(row.attempt),
      impact,
    }))
    .filter((row) => row.proposalId.length > 0)
}

const toDashboardQueueRows = (dashboardRows: Array<{ measureId: string; measureName: string; gaps: StarDashboardResponse["selected_measure_gaps"] }>): EvidenceQueueItem[] => {
  const byGap = new Map<string, EvidenceQueueItem>()

  for (const row of dashboardRows) {
    for (const gap of row.gaps) {
      if (byGap.has(gap.care_gap_id)) {
        continue
      }

      byGap.set(gap.care_gap_id, {
        proposalId: `dashboard-${gap.care_gap_id}`,
        gapId: gap.care_gap_id,
        gapKey: gap.care_gap_id,
        patientId: gap.member_id,
        measureId: row.measureId,
        measureName: row.measureName,
        tier: "",
        missingElement: "",
        deadlineDate: null,
        documentId: "",
        pageNo: null,
        evidenceKind: "",
        evidenceType: "",
        serviceDateRaw: null,
        serviceDateFrom: null,
        serviceDateTo: null,
        value: null,
        excerpt: "Evidence proposal details are available in integration profile.",
        proposedFinding: gap.status,
        confidence: null,
        model: null,
        reason: null,
        status: gap.is_closed ? "closed" : "open",
        reviewStatus: gap.status,
        decisionReason: null,
        context: null,
        ruleCheck: null,
        attempt: null,
        impact: null,
      })
    }
  }

  return Array.from(byGap.values())
}

const getHealthFast = async (): Promise<RawHealthResponse | null> => {
  const current = healthCache
  if (current && current.expiresAt > now()) {
    return current.value
  }

  const fetched = await fetchJson<RawHealthResponse>(withApiBase("/health"), HEALTH_TIMEOUT_MS).catch(() => null)
  healthCache = {
    expiresAt: now() + HEALTH_CACHE_MS,
    value: fetched,
  }
  return fetched
}

const fetchGapDetailCached = async (gapId: string): Promise<RawGapDetail> => {
  const cached = gapDetailCache.get(gapId)
  if (cached && cached.expiresAt > now()) {
    return cached.value
  }

  const inFlight = gapDetailInFlight.get(gapId)
  if (inFlight) {
    return inFlight
  }

  const request = fetchJson<RawGapDetail>(withApiBase(`/gaps/${encodeURIComponent(gapId)}`), timeoutMs)
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

const loadDashboardSnapshot = async (measureId: string | null): Promise<StarDashboardResponse> =>
  postJson<StarDashboardResponse>(
    withApiBase(envConfig.dashboardSummaryPath),
    timeoutMs,
    {
      measure_id: measureId,
      selected_gap_ids: [],
    },
  )

const toQueueLine = (queueLength: number): string => {
  if (queueLength <= 0) {
    return "No queue line is active: no proposed evidence is waiting for review."
  }
  return `A queue line appears when pending_count > 0 on a gap. ${queueLength} proposed finding(s) are waiting for review.`
}

const toExportHistory = (
  rows: NonNullable<RawMeasuresResponse["evidence"]>["exports"] | undefined,
): EvidenceExportRun[] => (rows ?? []).map((row) => ({
  id: row.id ?? 0,
  runAt: row.run_at ?? null,
  rowCount: row.row_count ?? 0,
  fileSha256: row.file_sha256 ?? "",
}))

const toCallExchange = (row: RawExtractionCall): EvidenceCallExchange => ({
  status: row.status ?? null,
  seconds: row.seconds ?? null,
  error: row.error ?? null,
  requestMessages: (row.request?.messages ?? []).map((message) => ({
    role: message.role ?? "",
    content: message.content ?? "",
  })),
  answer: row.answer ?? null,
})

const toLatestRun = (gapId: string, row: RawExtractionResponse): EvidenceLatestRun | null => {
  if (!row.run_id && !(row.pages ?? []).length && !(row.calls ?? []).length) {
    return null
  }

  return {
    gapId,
    runId: row.run_id ?? null,
    pages: (row.pages ?? []).map((page) => ({
      documentId: page.document_id ?? "",
      pageNo: page.page_no ?? 0,
      method: page.method ?? null,
      handwritten: Boolean(page.handwritten),
    })),
    calls: (row.calls ?? []).map(toCallExchange),
    kept: (row.kept ?? []).map((item) => ({
      evidenceType: item.evidence_type ?? null,
      documentId: item.document_id ?? "",
      pageNo: item.page_no ?? null,
      excerpt: item.excerpt ?? "",
    })),
    dropped: (row.dropped ?? []).map((item) => ({
      evidenceType: item.evidence_type ?? null,
      documentId: item.document_id ?? null,
      reason: item.reason ?? "",
      evidenceId: item.evidence_id ?? null,
    })),
  }
}

async function canLoadDashboardEvidence(): Promise<boolean> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))
  return hasOpenApiPath(paths, "/api/star-dashboard/calculate")
}

export function clearEvidenceServiceCaches(): void {
  healthCache = null
  gapDetailCache.clear()
  gapDetailInFlight.clear()
}

function invalidateGapDetailCache(gapId: string): void {
  gapDetailCache.delete(gapId)
  gapDetailInFlight.delete(gapId)
}

export async function discoverEvidenceCapabilities(): Promise<EvidenceCapabilities> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))

  if (
    hasOpenApiPath(paths, "/api/measures")
    && hasOpenApiPath(paths, "/api/gaps")
    && hasOpenApiPath(paths, "/api/gaps/{gap_id}")
    && hasOpenApiPath(paths, "/api/gaps/{gap_id}/evidence")
    && hasOpenApiPath(paths, "/api/evidence/{proposal_id}/decision")
    && hasOpenApiPath(paths, "/api/supplemental/export")
  ) {
    return { mode: "integration" }
  }

  if (await canLoadDashboardEvidence()) {
    return { mode: "dashboard" }
  }

  throw new Error("Evidence review endpoints are not available in this backend profile.")
}

export async function loadEvidenceOverview(capabilities: EvidenceCapabilities): Promise<EvidenceOverview> {
  if (capabilities.mode === "integration") {
    const [measures, gaps, health] = await Promise.all([
      fetchJson<RawMeasuresResponse>(withApiBase("/measures"), timeoutMs),
      fetchJson<RawGapRow[]>(withApiBase("/gaps?limit=500"), timeoutMs),
      getHealthFast(),
    ])

    const scopeGaps = (gaps ?? [])
      .map(toScopeGap)
      .filter((row) => row.gapId.length > 0)
      .sort((left, right) => {
        if (left.pendingCount !== right.pendingCount) {
          return right.pendingCount - left.pendingCount
        }
        return tierRank(left.tier) - tierRank(right.tier)
      })

    const queueGapIds = scopeGaps
      .filter((row) => row.pendingCount > 0)
      .slice(0, MAX_QUEUE_GAPS)
      .map((row) => row.gapId)

    const details = await Promise.allSettled(
      queueGapIds.map((gapId) => fetchGapDetailCached(gapId)),
    )

    const queue = details
      .flatMap((result) => (result.status === "fulfilled" ? toQueueRows(result.value) : []))
      .sort((left, right) => {
        const tierDelta = tierRank(left.tier) - tierRank(right.tier)
        if (tierDelta !== 0) {
          return tierDelta
        }
        const confidenceLeft = left.confidence ?? -1
        const confidenceRight = right.confidence ?? -1
        return confidenceRight - confidenceLeft
      })

    const openrouter = health?.openrouter
    const modelConnected = openrouter
      ? Boolean(openrouter.reachable) && openrouter.key_valid === true && openrouter.model_listed !== false
      : null

    return {
      mode: "integration",
      modelName: openrouter?.model ?? null,
      modelConnected,
      queueSourceLabel: "evidence_proposal where status = proposed",
      queueLine: toQueueLine(queue.length),
      scopeGaps,
      queue,
      counts: {
        proposed: measures.evidence?.proposed ?? queue.length,
        confirmed: measures.evidence?.confirmed ?? 0,
        rejected: measures.evidence?.rejected ?? 0,
      },
      exportSummary: {
        total: measures.evidence?.supplemental_total ?? 0,
        readyToExport: measures.evidence?.supplemental_unexported ?? 0,
        waitingForParts: measures.evidence?.supplemental_waiting_for_parts ?? 0,
      },
      exports: toExportHistory(measures.evidence?.exports),
      acceptanceByModel: toAcceptanceByModel(measures.evidence?.by_model),
    }
  }

  const seedSnapshot = await loadDashboardSnapshot(envConfig.dashboardDefaultMeasureId ?? null)

  const scopeGaps: EvidenceScopeGap[] = seedSnapshot.measure_cards.map((row) => ({
    gapId: row.measure_id,
    key: row.measure_id,
    patientId: "-",
    measureId: row.measure_id,
    measureName: row.measure_name,
    tier: "",
    pendingCount: Math.max(0, row.total_gaps - row.baseline_closed),
    hasDocuments: false,
    missingElement: "",
    reviewStatus: "open",
    deadlineDate: null,
  }))

  const dashboardRows = await Promise.allSettled(
    scopeGaps.map((scope) => loadDashboardSnapshot(scope.measureId)),
  )

  const queue = toDashboardQueueRows(
    dashboardRows
      .flatMap((result) => (result.status === "fulfilled" ? [result.value] : []))
      .flatMap((snapshot) => {
        const measureId = snapshot.selected_measure?.measure_id ?? ""
        if (!measureId) {
          return []
        }
        return [{
          measureId,
          measureName: snapshot.selected_measure?.measure_name ?? measureId,
          gaps: snapshot.selected_measure_gaps,
        }]
      }),
  )

  const sortedScopeGaps = [...scopeGaps].sort((left, right) => {
    if (left.pendingCount !== right.pendingCount) {
      return right.pendingCount - left.pendingCount
    }
    return left.measureId.localeCompare(right.measureId)
  })

  return {
    mode: "dashboard",
    modelName: null,
    modelConnected: null,
    queueSourceLabel: "star dashboard fallback",
    queueLine: toQueueLine(queue.length),
    scopeGaps: sortedScopeGaps,
    queue,
    counts: {
      proposed: queue.length,
      confirmed: null,
      rejected: null,
    },
    exportSummary: {
      total: null,
      readyToExport: null,
      waitingForParts: null,
    },
    exports: [],
    acceptanceByModel: [],
  }
}

export async function runEvidenceDashboardProcess(): Promise<EvidenceExtractionResult> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))
  const processPath = withApiBase(envConfig.integrationProcessPath)
  if (!hasOpenApiPath(paths, processPath)) {
    throw new Error("Automation process endpoint is not available in this backend profile.")
  }

  const response = await postJson<Record<string, unknown>>(
    processPath,
    timeoutMs,
    {},
  )

  const processed = Number(response.processed ?? 0)
  const skipped = Boolean(response.skipped)
  const skipReason = typeof response.skip_reason === "string" ? response.skip_reason : ""

  const message = skipped
    ? `Processed ${processed.toLocaleString()} gaps and skipped writeback: ${skipReason}`
    : `Processed ${processed.toLocaleString()} gaps in dashboard mode.`

  return { message, run: null }
}

export async function runEvidenceDashboardSeed(): Promise<EvidenceExportResult> {
  const paths = await getOpenApiPaths(timeoutMs).catch(() => ({} as Record<string, unknown>))
  const seedPath = withApiBase(envConfig.integrationSeedPath)
  if (!hasOpenApiPath(paths, seedPath)) {
    throw new Error("Seed sample endpoint is not available in this backend profile.")
  }

  await postJson<Record<string, unknown>>(
    seedPath,
    timeoutMs,
    {},
  )

  return {
    message: "Sample dashboard data loaded. Refresh completed.",
  }
}

export async function loadEvidenceGapDetail(
  capabilities: EvidenceCapabilities,
  gapId: string,
): Promise<EvidenceGapDetail | null> {
  if (capabilities.mode !== "integration") {
    return null
  }

  const detail = await fetchGapDetailCached(gapId)
  const gap = detail.gap

  return {
    gapId: gap?.gap_id ?? gapId,
    key: gap?.key ?? gapId,
    patientId: gap?.patient_id ?? "",
    measureId: gap?.measure_id ?? "",
    measureName: gap?.measure_name ?? gap?.measure_id ?? "",
    missingElement: gap?.missing_element ?? "",
    tier: gap?.tier ?? "",
    deadlineDate: gap?.deadline_date ?? null,
    lastRelevantEventDate: gap?.last_relevant_event_date ?? null,
    lastRelevantEventDescription: gap?.last_relevant_event_description ?? null,
    reviewStatus: gap?.review_status ?? "open",
    pendingCount: gap?.pending_count ?? 0,
    breakdown: toBreakdown(detail.breakdown),
    impact: toImpact(detail.impact),
    documents: (detail.documents ?? []).map((row) => ({
      documentId: row.document_id ?? "",
      docType: row.doc_type ?? "",
      documentDate: row.document_date ?? null,
      conversionStatus: row.conversion_status ?? null,
      pageCount: row.page_count ?? null,
    })),
    evidence: toQueueRows(detail),
    aiRuns: (detail.ai_runs ?? []).map((row) => ({
      runId: row.run_id ?? 0,
      startedAt: row.started_at ?? null,
      modelName: row.model_name ?? null,
      status: row.status ?? "unknown",
      kept: row.kept ?? 0,
      droppedCount: row.dropped_count ?? 0,
      durationMs: row.duration_ms ?? null,
      error: row.error ?? null,
      promptVersion: row.prompt_version ?? null,
      pagesSent: (row.pages_sent ?? []).map((page) => ({
        documentId: page.document_id ?? "",
        pageNo: page.page_no ?? 0,
        method: page.method ?? null,
        handwritten: Boolean(page.handwritten),
      })),
      dropped: (row.dropped ?? []).map((dropped) => ({
        evidenceType: dropped.evidence_type ?? null,
        reason: dropped.reason ?? "",
        evidenceId: dropped.evidence_id ?? null,
      })),
      rawResponse: row.raw_response ?? null,
    })),
  }
}

export async function runEvidenceExtraction(
  capabilities: EvidenceCapabilities,
  gapId: string,
): Promise<EvidenceExtractionResult> {
  if (capabilities.mode !== "integration") {
    throw new Error("Evidence extraction is available only in integration profile.")
  }

  const result = await postJson<RawExtractionResponse>(
    withApiBase(`/gaps/${encodeURIComponent(gapId)}/evidence`),
    timeoutMs,
    {},
  )

  invalidateGapDetailCache(gapId)

  const run = toLatestRun(gapId, result)

  const note = result.note?.trim()
  if (note) {
    return { message: note, run }
  }

  return {
    message: `Extraction run ${result.run_id ?? "?"} completed. Saved ${result.saved ?? 0} findings and skipped ${result.skipped_already_saved ?? 0}.`,
    run,
  }
}

export async function submitEvidenceDecision(
  capabilities: EvidenceCapabilities,
  proposalId: string,
  payload: { decision: "confirm" | "reject"; reviewer: string; reason?: string },
): Promise<EvidenceDecisionResult> {
  if (capabilities.mode !== "integration") {
    throw new Error("Evidence decision is available only in integration profile.")
  }

  const result = await postJson<RawDecisionResponse>(
    withApiBase(`/evidence/${encodeURIComponent(proposalId)}/decision`),
    timeoutMs,
    payload,
  )

  if (result.gap_id) {
    invalidateGapDetailCache(result.gap_id)
  }

  const decisionText = result.status ?? payload.decision
  const reviewStatus = result.review_status ? ` Gap status: ${result.review_status}.` : ""
  const supplemental = result.supplemental_record_id
    ? ` Supplemental record ${result.supplemental_record_id} created.`
    : ""

  return {
    message: `Evidence ${proposalId} ${decisionText}.${reviewStatus}${supplemental}`.trim(),
  }
}

export async function exportSupplementalEvidence(
  capabilities: EvidenceCapabilities,
  reviewer: string,
): Promise<EvidenceExportResult> {
  if (capabilities.mode !== "integration") {
    throw new Error("Supplemental export is available only in integration profile.")
  }

  const result = await postJson<RawExportResponse>(
    withApiBase("/supplemental/export"),
    timeoutMs,
    { reviewer },
  )

  if ((result.rows ?? 0) <= 0) {
    return { message: "No supplemental records are ready for export." }
  }

  return {
    message: `Export run ${result.export_run_id ?? "?"} completed with ${result.rows ?? 0} rows.${result.file_path ? ` File: ${result.file_path}.` : ""}`,
  }
}
