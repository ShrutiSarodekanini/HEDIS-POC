export type EvidenceMode = "integration" | "dashboard"

export interface EvidenceCapabilities {
  mode: EvidenceMode
}

export interface EvidenceModelAcceptance {
  model: string
  proposed: number
  confirmed: number
  rejected: number
  acceptanceRate: number | null
}

export interface EvidenceScopeGap {
  gapId: string
  key: string
  patientId: string
  measureId: string
  measureName: string
  tier: string
  pendingCount: number
  hasDocuments: boolean
  missingElement: string
  reviewStatus: string
  deadlineDate: string | null
}

export interface EvidenceContextQuote {
  text: string
  quoteStart: number
  quoteEnd: number
  startsMidPage: boolean
  endsMidPage: boolean
}

export interface EvidenceRuleCheckPart {
  part: string
  status: string
  metBy: number
  required: number
}

export interface EvidenceRuleCheck {
  windowFrom: string | null
  windowTo: string | null
  windowText: string | null
  serviceDateFrom: string | null
  serviceDateTo: string | null
  insideWindow: boolean | null
  parts: EvidenceRuleCheckPart[]
  partLabel: string | null
  partCount: number
}

export interface EvidenceAttempt {
  attemptId: number
  model: string | null
  startedAt: string | null
  durationMs: number | null
  kept: number
  dropped: number
}

export interface EvidenceGapImpact {
  available: boolean
  note: string | null
  rateNow: number | null
  rateAfter: number | null
  changePoints: number | null
  starsNow: number | null
  starsAfter: number | null
  starChanged: boolean | null
  closuresForNextStar: number | null
  exclusionRateAfter: number | null
  exclusionStarsAfter: number | null
  exclusionChangePoints: number | null
}

export interface EvidenceQueueItem {
  proposalId: string
  gapId: string
  gapKey: string
  patientId: string
  measureId: string
  measureName: string
  tier: string
  missingElement: string
  deadlineDate: string | null
  documentId: string
  pageNo: number | null
  evidenceKind: string
  evidenceType: string
  serviceDateRaw: string | null
  serviceDateFrom: string | null
  serviceDateTo: string | null
  value: string | null
  excerpt: string
  proposedFinding: string
  confidence: number | null
  model: string | null
  reason: string | null
  status: string
  reviewStatus: string
  decisionReason: string | null
  context: EvidenceContextQuote | null
  ruleCheck: EvidenceRuleCheck | null
  attempt: EvidenceAttempt | null
  impact: EvidenceGapImpact | null
}

export interface EvidenceOverview {
  mode: EvidenceMode
  modelName: string | null
  modelConnected: boolean | null
  queueSourceLabel: string
  queueLine: string
  scopeGaps: EvidenceScopeGap[]
  queue: EvidenceQueueItem[]
  counts: {
    proposed: number | null
    confirmed: number | null
    rejected: number | null
  }
  exportSummary: {
    total: number | null
    readyToExport: number | null
    waitingForParts: number | null
  }
  exports: EvidenceExportRun[]
  acceptanceByModel: EvidenceModelAcceptance[]
}

export interface EvidenceGapDocument {
  documentId: string
  docType: string
  documentDate: string | null
  conversionStatus: string | null
  pageCount: number | null
}

export interface EvidenceAiRun {
  runId: number
  startedAt: string | null
  modelName: string | null
  status: string
  kept: number
  droppedCount: number
  durationMs: number | null
  error: string | null
  promptVersion: string | null
  pagesSent: Array<{
    documentId: string
    pageNo: number
    method: string | null
    handwritten: boolean
  }>
  dropped: Array<{
    evidenceType: string | null
    reason: string
    evidenceId: string | null
  }>
  rawResponse: string | null
}

export interface EvidenceGapScoreStep {
  label: string
  change: number
}

export interface EvidenceGapBreakdown {
  likelihood: {
    steps: EvidenceGapScoreStep[]
    raw: number | null
    min: number | null
    max: number | null
    result: number | null
  } | null
  value: {
    weight: number | null
    numerator: number | null
    denominator: number | null
    rate: number | null
    rounded: number | null
    stars: number | null
    nextCut: number | null
    target: number | null
    needed: number | null
    divisor: number | null
    reason: string | null
    result: number | null
  } | null
  engagement: {
    steps: EvidenceGapScoreStep[]
    cap: number | null
    result: number | null
  } | null
  composite: number | null
  channel: string | null
}

export interface EvidenceGapDetail {
  gapId: string
  key: string
  patientId: string
  measureId: string
  measureName: string
  missingElement: string
  tier: string
  deadlineDate: string | null
  lastRelevantEventDate: string | null
  lastRelevantEventDescription: string | null
  reviewStatus: string
  pendingCount: number
  breakdown: EvidenceGapBreakdown | null
  impact: EvidenceGapImpact | null
  documents: EvidenceGapDocument[]
  evidence: EvidenceQueueItem[]
  aiRuns: EvidenceAiRun[]
}

export interface EvidenceExportRun {
  id: number
  runAt: string | null
  rowCount: number
  fileSha256: string
}

export interface EvidenceCallExchange {
  status: number | null
  seconds: number | null
  error: string | null
  requestMessages: Array<{
    role: string
    content: string
  }>
  answer: string | null
}

export interface EvidenceLatestRun {
  gapId: string
  runId: number | null
  pages: Array<{
    documentId: string
    pageNo: number
    method: string | null
    handwritten: boolean
  }>
  calls: EvidenceCallExchange[]
  kept: Array<{
    evidenceType: string | null
    documentId: string
    pageNo: number | null
    excerpt: string
  }>
  dropped: Array<{
    evidenceType: string | null
    documentId: string | null
    reason: string
    evidenceId: string | null
  }>
}

export interface EvidenceExtractionResult {
  message: string
  run: EvidenceLatestRun | null
}

export interface EvidenceDecisionResult {
  message: string
}

export interface EvidenceExportResult {
  message: string
}
