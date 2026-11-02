export interface StarCatalogCutPoint {
  star: number
  minimum_rate: number
}

export interface StarCatalogMeasure {
  measure_code: string
  measurement_year: number
  measure_name: string
  weight: number | null
  cut_points: StarCatalogCutPoint[]
}

export interface StarCatalogResponse {
  cms_rating_year: number
  cms_measure_set_url: string
  cms_technical_notes_url: string
  measures: StarCatalogMeasure[]
}

export interface StarDashboardOverall {
  rated_measure_count: number
  baseline_star: number | null
  projected_star: number | null
  star_delta: number | null
}

export interface StarDashboardMeasureCard {
  measure_id: string
  measurement_year: number
  measure_name: string
  star_eligible: boolean
  rating_note: string
  total_gaps: number
  baseline_closed: number
  projected_closed: number
  baseline_rate: number
  projected_rate: number
  baseline_star: number | null
  projected_star: number | null
  star_delta: number | null
}

export interface StarDashboardGap {
  care_gap_id: string
  member_id: string
  member_name: string
  status: string
  is_closed: boolean
  selected_for_projection: boolean
}

export interface StarDashboardPatientRow {
  member_id: string
  member_name: string
  measure_id: string
  measurement_year: number
  measure_name: string
  star_eligible: boolean
  total_gaps: number
  baseline_closed: number
  projected_closed: number
  baseline_rate: number
  projected_rate: number
  baseline_star: number | null
  projected_star: number | null
  star_delta: number | null
}

export interface StarDashboardResponse {
  cms_reference: {
    cms_rating_year: number
    technical_notes_url: string
    measure_set_url: string
    mapped_measure_ids: string[]
    note?: string
  }
  overall: StarDashboardOverall
  measure_cards: StarDashboardMeasureCard[]
  selected_measure: StarDashboardMeasureCard | null
  selected_measure_gaps: StarDashboardGap[]
  patient_rows: StarDashboardPatientRow[]
  selected_gap_ids: string[]
}

export interface DataSection<T> {
  data: T | null
  error: string | null
}

export interface DashboardSnapshot {
  catalog: DataSection<StarCatalogResponse>
  dashboard: DataSection<StarDashboardResponse>
  fetchedAt: string
}
