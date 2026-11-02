export type StarMode = "integration" | "dashboard"

export interface StarCapabilities {
  mode: StarMode
}

export interface StarCutPoint {
  stars: number
  low: number
  high: number | null
}

export interface StarCatalogMeasure {
  measureId: string
  measureName: string
  measurementYear: number
  weight: number | null
  cutPoints: StarCutPoint[]
}

export interface StarMeasureCard {
  measureId: string
  measureName: string
  measurementYear: number
  starEligible: boolean
  total: number
  baselineNumerator: number
  projectedNumerator: number
  baselineRate: number | null
  projectedRate: number | null
  baselineStar: number | null
  projectedStar: number | null
  starDelta: number | null
}

export interface StarOverview {
  mode: StarMode
  measurementYear: number | null
  cmsRatingYear: number | null
  catalogUrl: string | null
  notesUrl: string | null
  measures: StarCatalogMeasure[]
  measureCards: StarMeasureCard[]
  overall: {
    ratedMeasures: number
    baselineStar: number | null
    projectedStar: number | null
    delta: number | null
  } | null
}

export interface StarGap {
  gapId: string
  memberId: string
  memberName: string
  evidenceStatus: string
  isClosed: boolean
  eligibleForClosure: boolean
}

export interface StarWhatIfResult {
  measureId: string
  measureName: string
  measurementYear: number
  cmsRatingYear: number | null
  baselineRate: number | null
  projectedRate: number | null
  baselineNumerator: number
  baselineDenominator: number
  projectedNumerator: number
  projectedDenominator: number
  baselineStar: number | null
  projectedStar: number | null
  rateDelta: number | null
  relativeImprovementPercent: number | null
  starChanged: boolean
  selectedGapIds: string[]
  warnings: string[]
  disclaimer: string | null
  gaps: StarGap[]
}

export interface StarCalculationResult {
  measureId: string
  measurementYear: number
  cmsRatingYear: number
  inputScore: number | null
  appliedScore: number | null
  assignedStar: number | null
  ratingStatus: string
  explanation: string
  cutPoints: StarCutPoint[]
}

export interface StarComparisonResult {
  before: StarCalculationResult
  after: StarCalculationResult
  starChanged: boolean
}
