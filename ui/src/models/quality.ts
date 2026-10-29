export type QualityMode = "integration" | "automation" | "unavailable"

export interface QualityCapabilities {
  mode: QualityMode
}

export interface QualityMeasure {
  measure_id: string
  phase?: string
  description?: string | null
  open_gaps?: number | null
  total_gaps?: number | null
  baseline_closed?: number | null
  projected_closed?: number | null
  baseline_rate?: number | null
  projected_rate?: number | null
  baseline_star?: number | null
  projected_star?: number | null
}

export interface QualityBatch {
  batch_id: string
  started_at?: string | null
  status?: string
  step?: string
  loaded?: boolean
  runs?: number
  latest_run_id?: number | null
}

export interface QualityOverview {
  mode: QualityMode
  measures: QualityMeasure[]
  batches: QualityBatch[]
  not_available: Array<{ name?: string; reason?: string }>
}

export interface QualityRunSummary {
  run_id: number
  batch_id: string
  measure_id?: string | null
  status: string
  actor?: string | null
  summary?: Record<string, unknown> | null
  error?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface QualityCheck {
  check_name: string
  resource_type?: string | null
  measure_id?: string | null
  status: string
  metric_name?: string | null
  metric_value?: number | null
  threshold_value?: number | null
  details?: Record<string, unknown> | null
}

export interface QualityAnomaly {
  anomaly_id: string
  anomaly_type?: string | null
  check_type?: string | null
  severity?: string | null
  resource_type?: string | null
  measure_id?: string | null
  patient_id?: string | null
  details?: Record<string, unknown> | null
  detected_at?: string | null
}

export interface QualityRunDetail {
  run: QualityRunSummary
  checks: QualityCheck[]
  anomalies: QualityAnomaly[]
}

export interface QualityCoverageRecord {
  patient_id: string
  coverage_id: string
  payer?: string | null
  period_start?: string | null
  period_end?: string | null
  gap_detected?: boolean
  gap_days?: number | null
  continuous_enrollment_compliant?: boolean
}

export interface QualityCoverageSummary {
  total_covered: number
  continuous_enrollment: number
  hedis_violations: number
  compliance_rate: number
  gap_limit_days: number
}

export interface QualityCoverageResponse {
  coverage_records: QualityCoverageRecord[]
  payers: string[]
  summary: QualityCoverageSummary
}

export interface QualityRunActionResult {
  message: string
  success: boolean
}
