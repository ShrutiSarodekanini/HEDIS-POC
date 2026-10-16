import { ApiClient } from './apiClient';
import type { FqmApiService } from './fqmApiService';
import type {
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

const ALL_MEASURES_OPTION_ID = '__ALL_10_MEASURES__';
const AUTO_DETECTED_MEASURES_MODE_ID = '__AUTO_DETECTED_RELATED_MEASURES__';
const AUTO_DETECTED_MEASURES_LABEL = 'AUTO_DETECTED_RELATED_MEASURES';
const MEASURE_BUNDLE_BASE_PATH =
  import.meta.env.VITE_MEASURE_BUNDLE_BASE_PATH?.trim() || 'examples';
const MEASUREMENT_YEAR = import.meta.env.VITE_MEASUREMENT_YEAR?.trim() || 'MY2026';
const MEASUREMENT_YEAR_NUMBER = (() => {
  const matchedYear = MEASUREMENT_YEAR.match(/(\d{4})/);
  return matchedYear ? Number(matchedYear[1]) : 2026;
})();
const SUPPORTED_MEASURE_IDS = [
  'AAB',
  'BCSE',
  'BPD',
  'CBP',
  'CCSE',
  'COLE',
  'EED',
  'GSD',
  'KED',
  'SPD',
] as const;

type SupportedMeasureId = (typeof SUPPORTED_MEASURE_IDS)[number];

function isSupportedMeasureId(value: string): value is SupportedMeasureId {
  return (SUPPORTED_MEASURE_IDS as readonly string[]).includes(value);
}

interface PatientDemographics {
  gender?: string;
  birthDate?: string;
}

function calculateAgeOnYearStart(birthDate: string, year: number): number | null {
  const birth = new Date(birthDate);
  if (Number.isNaN(birth.getTime())) {
    return null;
  }

  const reference = new Date(Date.UTC(year, 0, 1));
  let age = reference.getUTCFullYear() - birth.getUTCFullYear();
  const monthDelta = reference.getUTCMonth() - birth.getUTCMonth();
  const dayDelta = reference.getUTCDate() - birth.getUTCDate();

  if (monthDelta < 0 || (monthDelta === 0 && dayDelta < 0)) {
    age -= 1;
  }

  return age;
}

function extractPatientDemographics(payload: unknown): PatientDemographics {
  if (!payload || typeof payload !== 'object') {
    return {};
  }

  const asRecord = payload as Record<string, unknown>;
  if (asRecord.resourceType === 'Patient') {
    return {
      gender: typeof asRecord.gender === 'string' ? asRecord.gender : undefined,
      birthDate: typeof asRecord.birthDate === 'string' ? asRecord.birthDate : undefined,
    };
  }

  if (asRecord.resourceType !== 'Bundle' || !Array.isArray(asRecord.entry)) {
    return {};
  }

  for (const entry of asRecord.entry as Array<Record<string, unknown>>) {
    const resource = entry?.resource as Record<string, unknown> | undefined;
    if (!resource || resource.resourceType !== 'Patient') {
      continue;
    }

    return {
      gender: typeof resource.gender === 'string' ? resource.gender : undefined,
      birthDate: typeof resource.birthDate === 'string' ? resource.birthDate : undefined,
    };
  }

  return {};
}

const MEASURE_BUNDLE_PATHS: Record<(typeof SUPPORTED_MEASURE_IDS)[number], string> = {
  AAB: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_aab/PublicAabAntibioticAvoidanceTechnicalDemo-bundle.json`,
  BCSE: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_bcse/PublicBreastCancerScreeningTechnicalDemo-bundle.json`,
  BPD: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_bpd/CMS165FHIRControllingHighBP-bundle.json`,
  CBP: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_cbp/CMS165FHIRControllingHighBP-bundle.json`,
  CCSE: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_ccse/CMS124FHIRCervicalCancerScreen-bundle.json`,
  COLE: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_cole/PublicColorectalCancerScreeningTechnicalDemo-bundle.json`,
  EED: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_eed/PublicEedDiabetesEyeExamTechnicalDemo-bundle.json`,
  GSD: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_gsd/PublicGsdGlycemicStatusTechnicalDemo-bundle.json`,
  KED: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_ked/PublicKidneyHealthTechnicalDemo-bundle.json`,
  SPD: `${MEASURE_BUNDLE_BASE_PATH}/independent_public_spd/PublicSpdStatinTherapyTechnicalDemo-bundle.json`,
};

interface HedisUiMemberResult {
  member_id?: string;
  measure_id?: string;
  status?: string;
  outcome?: string;
  populations?: Record<string, boolean | null>;
  data_quality_flags?: string[];
  warnings?: string[];
  errors?: Array<{ message?: string }>;
}

interface HedisUiBatchResult {
  results?: HedisUiMemberResult[];
  warnings?: string[];
  errors?: Array<{ message?: string }>;
}

interface MeasureRunResponseRecord {
  selectedMeasureId: SupportedMeasureId;
  response: HedisUiMemberResult | HedisUiBatchResult;
}

function isActionableMemberResult(result: HedisUiMemberResult): boolean {
  return result.populations?.denominator === true;
}

function normalizePage(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) && value !== undefined ? Math.max(1, value) : fallback;
}

function normalizePageSize(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) && value !== undefined ? Math.max(1, value) : fallback;
}

export class RealFqmApiService implements FqmApiService {
  readonly mode = 'real' as const;

  private readonly client: ApiClient;
  private readonly evaluationResults = new Map<string, EvaluationResult>();
  private readonly evaluationHistory: EvaluationHistoryItem[] = [];

  constructor(baseUrl: string) {
    this.client = new ApiClient(baseUrl);
  }

  async healthCheck(): Promise<HealthResponse> {
    const response = await this.client.get<{ status?: string; message?: string; version?: string }>(
      '/health',
    );
    return {
      status: response.status === 'ok' ? 'ok' : 'degraded',
      message: response.message,
      version: response.version,
      checkedAt: new Date().toISOString(),
    };
  }

  async listMeasures(
    _query: MeasureQuery,
  ): Promise<PaginatedResponse<MeasureSummary>> {
    const items: MeasureSummary[] = SUPPORTED_MEASURE_IDS.map((id) => ({
      id,
      name: id,
      description: 'Supported HEDIS measure',
      measurementPeriod: MEASUREMENT_YEAR,
      executionStatus: 'ready',
      metadataAvailable: true,
    }));

    return {
      items,
      page: 1,
      pageSize: items.length,
      total: items.length,
    };
  }

  async getMeasureDetails(measureId: string): Promise<MeasureDetail> {
    const normalized = measureId.toUpperCase();
    return {
      id: normalized,
      name: normalized,
      description: 'Supported HEDIS measure',
      measurementPeriod: MEASUREMENT_YEAR,
      executionStatus: 'ready',
      metadataAvailable: true,
      lastUpdated: new Date().toISOString(),
      source: 'hedis_ui_app',
    };
  }

  async submitEvaluation(
    request: EvaluationSubmissionRequest,
  ): Promise<EvaluationSubmissionResponse> {
    const submittedAt = new Date().toISOString();
    const evaluationId =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `eval-${Date.now()}`;

    const normalizedRequestedMeasureId = request.measureId?.trim();
    const isAutomaticDetectionMode =
      !normalizedRequestedMeasureId ||
      normalizedRequestedMeasureId === ALL_MEASURES_OPTION_ID ||
      normalizedRequestedMeasureId === AUTO_DETECTED_MEASURES_MODE_ID;

    const selectedMeasureIds =
      isAutomaticDetectionMode
        ? [...SUPPORTED_MEASURE_IDS]
        : (() => {
            const normalizedMeasureId = normalizedRequestedMeasureId.toUpperCase();
            if (!isSupportedMeasureId(normalizedMeasureId)) {
              throw new Error(`Unsupported measure id '${request.measureId}'.`);
            }
            return [normalizedMeasureId];
          })();

    const runResponseGroups = await Promise.all(
      request.patientBundleFiles.map(async (file) => {
        const measureCandidates = isAutomaticDetectionMode
          ? await this.preselectMeasuresForFile(file, selectedMeasureIds)
          : selectedMeasureIds;

        const effectiveCandidates = measureCandidates.length > 0
          ? measureCandidates
          : selectedMeasureIds;

        // Probe runs are intentionally lightweight and reused as final execution output.
        const probeResponses = await Promise.all(
          effectiveCandidates.map((selectedMeasureId) =>
            this.runMeasure(file, selectedMeasureId, false),
          ),
        );

        if (!isAutomaticDetectionMode) {
          return probeResponses;
        }

        const probeRows = probeResponses.flatMap(({ selectedMeasureId, response }) =>
          this.toMemberResults(response).map((memberResult) => ({
            ...memberResult,
            measure_id: memberResult.measure_id ?? selectedMeasureId,
          })),
        );

        const relatedProbeRows = this.filterRelatedMeasureResults(probeRows);
        const relatedMeasureSet = new Set(
          relatedProbeRows
            .map((row) => row.measure_id)
            .filter(
              (value): value is SupportedMeasureId =>
                typeof value === 'string' && isSupportedMeasureId(value),
            ),
        );

        if (relatedMeasureSet.size === 0) {
          return probeResponses;
        }

        return probeResponses.filter(({ selectedMeasureId }) => relatedMeasureSet.has(selectedMeasureId));
      }),
    );

    const rawResponses = runResponseGroups.flat();

    const rawMemberResults = rawResponses.flatMap(({ selectedMeasureId, response }) =>
      this.toMemberResults(response).map((memberResult) => ({
        ...memberResult,
        measure_id: memberResult.measure_id ?? selectedMeasureId,
      })),
    );
    const memberResults =
      isAutomaticDetectionMode
        ? this.filterRelatedMeasureResults(rawMemberResults)
        : rawMemberResults;

    const patientResults = memberResults.map((member, index) => {
      const populations = this.toPopulationBooleans(member.populations ?? {});
      return {
        patientId: member.member_id || `patient-${index + 1}`,
        status: this.toEvaluationStatus(member.status),
        populations,
      } satisfies PatientResult;
    });

    const populationCounts = this.toPopulationCounts(patientResults);
    const warnings = [
      ...rawResponses.flatMap(({ response }) => this.toBatchWarnings(response)),
      ...memberResults.flatMap((result) => result.warnings ?? []),
    ];
    const errors = memberResults.flatMap((result) =>
      (result.errors ?? [])
        .map((error) => error.message)
        .filter((message): message is string => Boolean(message)),
    );
    const batchErrors = rawResponses.flatMap(({ response }) => this.toBatchErrors(response));
    const combinedErrors = [...batchErrors, ...errors];
    const unresolvedReasons = memberResults
      .filter(
        (result) =>
          isActionableMemberResult(result) &&
          this.toEvaluationStatus(result.status) === 'unresolved',
      )
      .map((result) => {
        const reasonSource = [
          ...(result.warnings ?? []),
          ...((result.errors ?? [])
            .map((error) => error.message)
            .filter((message): message is string => Boolean(message))),
        ];
        const reason = reasonSource[0] ?? 'Unresolved policy or missing required evidence.';
        return `${result.measure_id ?? 'UNKNOWN'}: ${reason}`;
      });

    const hasFailedStatus =
      memberResults.length === 0 ||
      memberResults.some((result) => this.toEvaluationStatus(result.status) === 'failed');

    const hasUnresolvedStatus = memberResults.some(
      (result) =>
        isActionableMemberResult(result) &&
        this.toEvaluationStatus(result.status) === 'unresolved',
    );

    const evaluationStatus: EvaluationResult['status'] = hasFailedStatus
      ? 'failed'
      : hasUnresolvedStatus
        ? 'unresolved'
        : 'completed';

    const latestResult: EvaluationResult = {
      evaluationId,
      status: evaluationStatus,
      measureId: isAutomaticDetectionMode
        ? AUTO_DETECTED_MEASURES_LABEL
        : normalizedRequestedMeasureId,
      submittedAt,
      completedAt: new Date().toISOString(),
      populationCounts,
      unresolvedReasons,
      warnings,
      errors: combinedErrors,
      patientResults,
      // Persist filtered rows so downstream UI tables only render patient-related measures.
      resultPayload: {
        results: memberResults,
        warnings,
        errors: combinedErrors.map((message) => ({ message })),
      },
      modeLabel: 'production',
    };

    this.evaluationResults.set(evaluationId, latestResult);
    this.evaluationHistory.unshift({
      evaluationId,
      measureId: isAutomaticDetectionMode
        ? AUTO_DETECTED_MEASURES_LABEL
        : normalizedRequestedMeasureId,
      status: latestResult.status,
      submittedAt,
      completedAt: latestResult.completedAt,
    });

    return {
      evaluationId,
      status: evaluationStatus,
      submittedAt,
    };
  }

  async getEvaluationResult(evaluationId: string): Promise<EvaluationResult> {
    const found = this.evaluationResults.get(evaluationId);
    if (!found) {
      return {
        evaluationId,
        status: 'failed',
        measureId: 'UNKNOWN',
        submittedAt: new Date().toISOString(),
        errors: [
          `Evaluation '${evaluationId}' was not found in local frontend runtime cache.`,
        ],
      };
    }

    return found;
  }

  async listEvaluationHistory(
    query: EvaluationHistoryQuery,
  ): Promise<PaginatedResponse<EvaluationHistoryItem>> {
    const page = normalizePage(query.page, 1);
    const pageSize = normalizePageSize(query.pageSize, 10);
    const filtered = this.evaluationHistory.filter((item) => {
      if (query.status && query.status !== 'all' && item.status !== query.status) {
        return false;
      }

      if (query.search && query.search.trim()) {
        const search = query.search.trim().toLowerCase();
        return (
          item.evaluationId.toLowerCase().includes(search) ||
          item.measureId.toLowerCase().includes(search)
        );
      }

      return true;
    });

    const start = (page - 1) * pageSize;
    const items = filtered.slice(start, start + pageSize);

    return {
      items,
      page,
      pageSize,
      total: filtered.length,
    };
  }

  async listPatientResults(
    query: PatientResultsQuery,
  ): Promise<PaginatedResponse<PatientResult>> {
    const page = normalizePage(query.page, 1);
    const pageSize = normalizePageSize(query.pageSize, 10);
    const source = query.evaluationId
      ? this.evaluationResults.get(query.evaluationId)?.patientResults ?? []
      : Array.from(this.evaluationResults.values()).flatMap(
          (entry) => entry.patientResults ?? [],
        );

    const filtered = source.filter((item) => {
      if (query.search && query.search.trim()) {
        return item.patientId.toLowerCase().includes(query.search.trim().toLowerCase());
      }
      return true;
    });

    const start = (page - 1) * pageSize;
    const items = filtered.slice(start, start + pageSize);

    return {
      items,
      page,
      pageSize,
      total: filtered.length,
    };
  }

  private toMemberResults(
    response: HedisUiMemberResult | HedisUiBatchResult,
  ): HedisUiMemberResult[] {
    if (Array.isArray((response as HedisUiBatchResult).results)) {
      return (response as HedisUiBatchResult).results ?? [];
    }

    return [response as HedisUiMemberResult];
  }

  private toBatchWarnings(response: HedisUiMemberResult | HedisUiBatchResult): string[] {
    return Array.isArray((response as HedisUiBatchResult).warnings)
      ? ((response as HedisUiBatchResult).warnings ?? [])
      : [];
  }

  private toBatchErrors(response: HedisUiMemberResult | HedisUiBatchResult): string[] {
    const errors = (response as HedisUiBatchResult).errors;
    if (!Array.isArray(errors)) {
      return [];
    }

    return errors
      .map((error) => error.message)
      .filter((message): message is string => Boolean(message));
  }

  private toEvaluationStatus(raw: string | undefined): EvaluationResult['status'] {
    const normalized = (raw ?? '').toLowerCase();
    if (normalized === 'ok' || normalized === 'completed') {
      return 'completed';
    }
    if (normalized === 'pending') {
      return 'pending';
    }
    if (normalized === 'running') {
      return 'running';
    }
    if (normalized === 'unresolved') {
      return 'unresolved';
    }
    if (normalized === 'blocked') {
      return 'unresolved';
    }
    return 'failed';
  }

  private toPopulationBooleans(
    populations: Record<string, boolean | null>,
  ): Record<string, boolean | null> {
    return {
      initialPopulation: populations.initial_population ?? null,
      denominator: populations.denominator ?? null,
      denominatorExclusion: populations.denominator_exclusion ?? null,
      denominatorException: populations.denominator_exception ?? null,
      numerator: populations.numerator ?? null,
    };
  }

  private filterRelatedMeasureResults(results: HedisUiMemberResult[]): HedisUiMemberResult[] {
    const related = results.filter((result) => {
      const populations = result.populations ?? {};
      return (
        populations.initial_population === true ||
        populations.denominator === true ||
        populations.numerator === true ||
        populations.denominator_exclusion === true ||
        populations.denominator_exception === true
      );
    });

    // If every row appears non-related, return original rows so users still get diagnostics.
    return related.length > 0 ? related : results;
  }

  private async runMeasure(
    file: File,
    selectedMeasureId: SupportedMeasureId,
    includeMeasureReport: boolean,
  ): Promise<MeasureRunResponseRecord> {
    const measureBundlePath = MEASURE_BUNDLE_PATHS[selectedMeasureId];
    if (!measureBundlePath) {
      throw new Error(`No measure bundle path configured for measure '${selectedMeasureId}'.`);
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('measure_id', selectedMeasureId);
    formData.append('measurement_year', MEASUREMENT_YEAR);
    formData.append('adapter', 'fqm-execution');
    formData.append('measure_bundle_path', measureBundlePath);
    formData.append('include_measure_report', includeMeasureReport ? 'true' : 'false');
    formData.append('runtime_enabled', 'false');

    const response = await this.client.postForm<HedisUiMemberResult | HedisUiBatchResult>(
      '/run-request-file',
      formData,
    );

    return {
      selectedMeasureId,
      response,
    };
  }

  private async preselectMeasuresForFile(
    file: File,
    candidates: SupportedMeasureId[],
  ): Promise<SupportedMeasureId[]> {
    try {
      const text = await file.text();
      const payload = JSON.parse(text) as unknown;
      const demographics = extractPatientDemographics(payload);
      let filtered = [...candidates];

      const gender = demographics.gender?.toLowerCase();
      if (gender && gender !== 'female') {
        filtered = filtered.filter((id) => id !== 'BCSE' && id !== 'CCSE');
      }

      if (demographics.birthDate) {
        const age = calculateAgeOnYearStart(demographics.birthDate, MEASUREMENT_YEAR_NUMBER);
        if (age !== null && age >= 18) {
          filtered = filtered.filter((id) => id !== 'AAB');
        }
      }

      return filtered;
    } catch {
      return candidates;
    }
  }

  private toPopulationCounts(
    patientResults: PatientResult[],
  ): EvaluationResult['populationCounts'] {
    const counts = {
      initialPopulation: 0,
      denominator: 0,
      denominatorExclusion: 0,
      denominatorException: 0,
      numerator: 0,
    };

    for (const patient of patientResults) {
      if (!patient.populations) {
        continue;
      }

      if (patient.populations.initialPopulation === true) {
        counts.initialPopulation += 1;
      }
      if (patient.populations.denominator === true) {
        counts.denominator += 1;
      }
      if (patient.populations.denominatorExclusion === true) {
        counts.denominatorExclusion += 1;
      }
      if (patient.populations.denominatorException === true) {
        counts.denominatorException += 1;
      }
      if (patient.populations.numerator === true) {
        counts.numerator += 1;
      }
    }

    return counts;
  }

}
