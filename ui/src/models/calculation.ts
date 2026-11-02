export type CalculationMode = "integration" | "star"

export interface CalculationCapabilities {
  mode: CalculationMode
  supportsSelectedMeasures: boolean
}

export interface CalculationOverviewMeasure {
  measureId: string
  measureName: string
  measureType: string
  inMeasure: number
  met: number
  excluded: number
  notMet: number
  rate: number | null
  gapsOpen: number
  closedByEngine: number
  confirmedByReviewer: number
  computedAt: string | null
}

export interface CalculationOverview {
  mode: CalculationMode
  measurementYear: number | null
  latestRunLabel: string
  measures: CalculationOverviewMeasure[]
}

export interface CalculationPatientRow {
  patientId: string
  inDenominator: boolean
  met: boolean
  excluded: boolean
  outcome: string
  gapStatus: string
  reviewStatus: string
  computedAt: string | null
  gapId: string | null
  missingElement: string | null
}

export interface CalculationRunResult {
  success: boolean
  message: string
}
