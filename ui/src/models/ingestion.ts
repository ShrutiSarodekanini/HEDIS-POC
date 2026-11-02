export interface IngestionDatasetMeta {
  input_dir: string
  found_files: string[]
  missing_required_files: string[]
  row_counts: Record<string, number>
  total_rows: number
  saved_files?: string[]
}

export interface IngestionRuntimeStage {
  name: string
  status: string
  details?: string | null
  started_at?: string | null
  completed_at?: string | null
  elapsed_seconds?: number | null
}

export interface IngestionRuntime {
  status?: string
  stages?: IngestionRuntimeStage[]
  resource_progress?: Record<
    string,
    {
      total?: number
      processed?: number
      warnings?: number
      rejected?: number
    }
  >
}

export interface IngestionJob {
  id: string
  input_dir?: string
  status?: string
  started_at?: string | null
  completed_at?: string | null
  total_source_records?: number
  resources_generated?: number
  resources_validated?: number
  resources_persisted?: number
  warning_count?: number
  error_count?: number
  runtime?: IngestionRuntime
}

export interface IngestionJobsResponse {
  jobs: IngestionJob[]
}

export interface IngestionStartResponse {
  job_id: string
  status: string
  legacy_jobs_cleared?: number
}

export interface IngestionPersistResponse {
  job_id: string
  resources_persisted: number
}

export interface IngestionValidationIssue {
  id?: number
  resource_pk?: number
  severity?: string
  issue_type?: string
  issue_code?: string
  message?: string
  details?: string
  source_file?: string
  source_record_id?: string
}

export interface IngestionValidationResponse {
  job_id: string
  issues: IngestionValidationIssue[]
}

export interface IngestionPatientDetail {
  patient_id: string
  resource_count?: number
  estimated_size_bytes?: number
  saved_at?: string | null
}

export interface IngestionPatientsResponse {
  job_id: string
  patients: string[]
  patient_details: IngestionPatientDetail[]
}

export interface IngestionResource {
  resource_pk?: number
  patient_id?: string
  resource_type?: string
  persisted?: boolean
  validation_status?: string
  resource_json?: Record<string, unknown> | null
}

export interface IngestionPatientResourcesResponse {
  job_id: string
  patient_id: string
  resources: IngestionResource[]
}

export interface IngestionEndpointMode {
  prefix: "/api" | "/api/ingestion"
  flowPath: string | null
}

export interface IngestionFlowBatch {
  batch_id?: string
  ingest_job_id?: string
  status?: string
  step?: string
  updated_at?: string
  error?: string
  counts?: Record<string, unknown>
}

export interface IngestionFlowResponse {
  batch?: IngestionFlowBatch | null
  tasks?: Record<string, string>
}
