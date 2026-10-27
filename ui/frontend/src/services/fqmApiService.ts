import type {
  ConnectionMode,
  EvaluationHistoryItem,
  EvaluationHistoryQuery,
  EvaluationResult,
  EvaluationSubmissionRequest,
  EvaluationSubmissionResponse,
  HealthResponse,
  MeasureDetail,
  MeasureQuery,
  MeasureSummary,
  PaginatedResponse,
  PatientResult,
  PatientResultsQuery,
} from '../types/api';

export interface FqmApiService {
  readonly mode: ConnectionMode;

  healthCheck(): Promise<HealthResponse>;
  listMeasures(query: MeasureQuery): Promise<PaginatedResponse<MeasureSummary>>;
  getMeasureDetails(measureId: string): Promise<MeasureDetail>;
  submitEvaluation(
    request: EvaluationSubmissionRequest,
  ): Promise<EvaluationSubmissionResponse>;
  getEvaluationResult(evaluationId: string): Promise<EvaluationResult>;
  listEvaluationHistory(
    query: EvaluationHistoryQuery,
  ): Promise<PaginatedResponse<EvaluationHistoryItem>>;
  listPatientResults(
    query: PatientResultsQuery,
  ): Promise<PaginatedResponse<PatientResult>>;
}
