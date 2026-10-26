export type ConnectionMode = 'real' | 'mock';

export type EvaluationStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'unresolved';

export type MeasureExecutionStatus =
  | 'ready'
  | 'retired'
  | 'unknown'
  | 'unavailable';

export interface PaginatedResponse<TItem> {
  items: TItem[];
  page: number;
  pageSize: number;
  total: number;
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'unavailable';
  message?: string;
  version?: string;
  checkedAt: string;
}

export interface MeasureSummary {
  id: string;
  name: string | null;
  description: string | null;
  measurementPeriod: string | null;
  executionStatus: MeasureExecutionStatus;
  metadataAvailable: boolean;
}

export interface MeasureDetail extends MeasureSummary {
  lastUpdated: string | null;
  source: string | null;
}

export interface MeasureQuery {
  search?: string;
  status?: MeasureExecutionStatus | 'all';
  page?: number;
  pageSize?: number;
}

export interface EvaluationSubmissionRequest {
  measureId: string;
  measurementPeriodStart?: string;
  measurementPeriodEnd?: string;
  measureBundleFile?: File;
  patientBundleFiles: File[];
}

export interface EvaluationSubmissionResponse {
  evaluationId: string;
  status: EvaluationStatus;
  submittedAt: string;
}

export interface PopulationCounts {
  initialPopulation?: number;
  denominator?: number;
  numerator?: number;
  denominatorExclusion?: number;
  denominatorException?: number;
  measurePopulation?: number;
  measureObservation?: number;
}

export interface PatientResult {
  patientId: string;
  status: EvaluationStatus;
  populations?: Record<string, boolean | null>;
  warnings?: string[];
}

export interface EvaluationResult {
  evaluationId: string;
  status: EvaluationStatus;
  measureId: string;
  measurementPeriodStart?: string;
  measurementPeriodEnd?: string;
  submittedAt: string;
  completedAt?: string;
  populationCounts?: PopulationCounts;
  unresolvedReasons?: string[];
  warnings?: string[];
  errors?: string[];
  patientResults?: PatientResult[];
  resultPayload?: unknown;
  modeLabel?: 'demo' | 'production';
}

export interface EvaluationHistoryItem {
  evaluationId: string;
  measureId: string;
  status: EvaluationStatus;
  submittedAt: string;
  completedAt?: string;
}

export interface EvaluationHistoryQuery {
  search?: string;
  status?: EvaluationStatus | 'all';
  page?: number;
  pageSize?: number;
}

export interface PatientResultsQuery {
  search?: string;
  evaluationId?: string;
  page?: number;
  pageSize?: number;
}

export interface AppRuntimeConfig {
  apiBaseUrl: string;
  mode: ConnectionMode;
  version: string;
}
