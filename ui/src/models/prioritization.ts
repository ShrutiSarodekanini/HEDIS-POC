export type PrioritizationMode = "integration" | "dashboard"

export interface PrioritizationCapabilities {
  mode: PrioritizationMode
}

export interface PrioritizationMeasureSummary {
  measureId: string
  measureName: string
  openGaps: number
  numerator: number
  denominator: number
  rate: number | null
  roundedRate: number | null
  stars: number | null
  projectedRate: number | null
  projectedStars: number | null
  valueScore: number | null
  valueReason: string | null
  confirmedPendingEngine: number
  excludedPendingEngine: number
}

export interface PrioritizationOverview {
  mode: PrioritizationMode
  rulesVersion: string
  cmsRatingYear: number | null
  lastScoredAt: string | null
  tiers: {
    high: number
    medium: number
    low: number
  }
  evidence: {
    proposed: number | null
    confirmed: number | null
    rejected: number | null
    supplementalUnexported: number | null
    supplementalWaitingForParts: number | null
  }
  measures: PrioritizationMeasureSummary[]
}

export interface PrioritizationWorklistFilters {
  role: "" | "reviewer" | "outreach" | "doctor"
  tier: "" | "high" | "medium" | "low"
  measure: string
  limit: number
}

export interface PrioritizationWorklistItem {
  key: string
  gapId: string
  measureId: string
  measureName: string
  tier: "high" | "medium" | "low" | string
  composite: number | null
  likelihood: number | null
  value: number | null
  engagement: number | null
  channel: string | null
  reasons: string[]
  actionReason: string | null
  patientId: string
  missingElement: string | null
  reviewStatus: string
  pendingCount: number
  nextVisit: string | null
  hasDocuments: boolean
}

export interface PrioritizationBreakdownStep {
  label: string
  change: number
}

export interface PrioritizationBreakdown {
  measureId: string
  asOf: string
  likelihood: {
    result: number
    raw: number
    min: number
    max: number
    steps: PrioritizationBreakdownStep[]
  }
  value: {
    result: number
    reason: string
    weight: number | null
    numerator: number
    denominator: number
    rate: number | null
    rounded: number | null
    stars: number | null
    nextCut: number | null
    target: number | null
    needed: number | null
  }
  engagement: {
    result: number
    cap: number
    steps: PrioritizationBreakdownStep[]
  }
  composite: number
  channel: string
}

export interface PrioritizationImpact {
  available: boolean
  note: string | null
  rateNow: number | null
  rateAfter: number | null
  changePoints: number | null
  starsNow: number | null
  starsAfter: number | null
  closuresForNextStar: number | null
  exclusion: {
    rateAfter: number | null
    starsAfter: number | null
    changePoints: number | null
  } | null
}

export interface PrioritizationGapDetail {
  gap: {
    gapId: string
    key: string
    patientId: string
    measureId: string
    measureName: string
    measurementYear: number
    status: string
    reviewStatus: string
    pendingCount: number
    missingElement: string | null
    deadlineDate: string | null
    lastRelevantEventDate: string | null
    lastRelevantEventDescription: string | null
    tier: string | null
    compositeScore: number | null
    recommendedChannel: string | null
    reasons: string[]
    actionReason: string | null
  }
  breakdown: PrioritizationBreakdown | null
  impact: PrioritizationImpact | null
  documentsCount: number
  evidenceSummary: {
    proposed: number
    confirmed: number
    rejected: number
  }
  aiRunsCount: number
}
