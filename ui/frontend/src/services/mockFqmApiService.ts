import type { FqmApiService } from './fqmApiService';
import type {
  EvaluationHistoryItem,
  EvaluationHistoryQuery,
  EvaluationResult,
  EvaluationSubmissionRequest,
  EvaluationSubmissionResponse,
  HealthResponse,
  MeasureDetail,
  MeasureExecutionStatus,
  MeasureQuery,
  MeasureSummary,
  PaginatedResponse,
  PatientResult,
  PatientResultsQuery,
} from '../types/api';
import { paginate } from '../utils/pagination';

const TARGET_MEASURE_IDS = [
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

const DEMO_DELAY_MS = 250;
const FINALIZATION_DELAY_MS = 2200;

function nowIso(): string {
  return new Date().toISOString();
}

function formatPeriodLabel(year: number): string {
  return `${year}-01-01 to ${year}-12-31`;
}

function createMeasure(status: MeasureExecutionStatus, id: string): MeasureDetail {
  return {
    id,
    name: null,
    description: null,
    measurementPeriod: null,
    executionStatus: status,
    metadataAvailable: false,
    lastUpdated: null,
    source: null,
  };
}

const mockMeasures: MeasureDetail[] = TARGET_MEASURE_IDS.map((id) =>
  createMeasure('ready', id),
);

const evaluations = new Map<string, EvaluationResult>();
const histories: EvaluationHistoryItem[] = [
  {
    evaluationId: 'demo-run-0001',
    measureId: 'AAB',
    status: 'completed',
    submittedAt: '2026-09-30T14:06:00.000Z',
    completedAt: '2026-09-30T14:06:06.000Z',
  },
  {
    evaluationId: 'demo-run-0002',
    measureId: 'SPD',
    status: 'unresolved',
    submittedAt: '2026-10-01T11:12:00.000Z',
    completedAt: '2026-10-01T11:12:05.000Z',
  },
  {
    evaluationId: 'demo-run-0003',
    measureId: 'BCSE',
    status: 'failed',
    submittedAt: '2026-10-04T09:00:00.000Z',
    completedAt: '2026-10-04T09:00:01.000Z',
  },
];

for (const history of histories) {
  evaluations.set(history.evaluationId, {
    evaluationId: history.evaluationId,
    status: history.status,
    measureId: history.measureId,
    submittedAt: history.submittedAt,
    completedAt: history.completedAt,
    measurementPeriodStart: '2026-01-01',
    measurementPeriodEnd: '2026-12-31',
    modeLabel: 'demo',
    populationCounts:
      history.status === 'completed'
        ? {
            initialPopulation: 9,
            denominator: 7,
            numerator: 5,
          }
        : undefined,
    unresolvedReasons:
      history.status === 'unresolved'
        ? ['Insufficient terminology resources in supplied bundles.']
        : undefined,
    errors:
      history.status === 'failed'
        ? ['Malformed bundle payload in submitted measure package.']
        : undefined,
    patientResults:
      history.status === 'completed'
        ? [
            {
              patientId: 'demo-patient-001',
              status: 'completed',
              populations: {
                initialPopulation: true,
                denominator: true,
                numerator: true,
              },
            },
            {
              patientId: 'demo-patient-002',
              status: 'completed',
              populations: {
                initialPopulation: true,
                denominator: true,
                numerator: false,
              },
            },
          ]
        : undefined,
    warnings:
      history.status === 'unresolved'
        ? ['Some expected population outputs were not returned.']
        : undefined,
  });
}

let serial = 4;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function toPatientId(filename: string): string {
  return filename.toLowerCase().replace(/[^a-z0-9]/g, '-').replace(/-+/g, '-');
}

function matchStatus(status: string, filter: string | undefined): boolean {
  if (!filter || filter === 'all') {
    return true;
  }

  return status === filter;
}

function matchSearch(value: string, search: string | undefined): boolean {
  if (!search) {
    return true;
  }

  return value.toLowerCase().includes(search.toLowerCase().trim());
}

function finalizeIfReady(result: EvaluationResult): EvaluationResult {
  if (result.status !== 'pending' && result.status !== 'running') {
    return result;
  }

  const startedAt = new Date(result.submittedAt).valueOf();
  const shouldFinalize = Date.now() - startedAt > FINALIZATION_DELAY_MS;

  if (!shouldFinalize) {
    return {
      ...result,
      status: 'running',
      warnings: ['Evaluation is still in progress.'],
    };
  }

  if (result.measureId === 'SPD') {
    return {
      ...result,
      status: 'unresolved',
      completedAt: nowIso(),
      unresolvedReasons: [
        'Patient bundles did not provide enough evidence to resolve all target populations.',
      ],
      warnings: [
        'Outcome is unresolved. This is not equivalent to pass or fail.',
      ],
      populationCounts: {
        initialPopulation: 5,
        denominator: 4,
      },
      patientResults: (result.patientResults ?? []).map((entry) => ({
        ...entry,
        status: 'unresolved',
      })),
    };
  }

  return {
    ...result,
    status: 'completed',
    completedAt: nowIso(),
    warnings: undefined,
    populationCounts: {
      initialPopulation: 12,
      denominator: 9,
      numerator: 6,
      denominatorException: 1,
    },
    patientResults: (result.patientResults ?? []).map((entry, index) => ({
      ...entry,
      status: 'completed',
      populations: {
        initialPopulation: true,
        denominator: true,
        numerator: index % 2 === 0,
      },
    })),
    resultPayload: {
      generatedAt: nowIso(),
      note: 'Demo payload from explicit mock mode.',
    },
  };
}

export class MockFqmApiService implements FqmApiService {
  readonly mode = 'mock' as const;

  async healthCheck(): Promise<HealthResponse> {
    await wait(DEMO_DELAY_MS);
    return {
      status: 'ok',
      message: 'Mock adapter active. Responses are demonstration data.',
      version: 'mock-1.0',
      checkedAt: nowIso(),
    };
  }

  async listMeasures(query: MeasureQuery): Promise<PaginatedResponse<MeasureSummary>> {
    await wait(DEMO_DELAY_MS);

    const filtered = mockMeasures.filter((measure) => {
      const searchMatch =
        matchSearch(measure.id, query.search) ||
        matchSearch(measure.name ?? '', query.search);
      const statusMatch =
        query.status === undefined ||
        query.status === 'all' ||
        query.status === measure.executionStatus;

      return searchMatch && statusMatch;
    });

    return paginate(filtered, query.page, query.pageSize);
  }

  async getMeasureDetails(measureId: string): Promise<MeasureDetail> {
    await wait(DEMO_DELAY_MS);

    const found = mockMeasures.find((measure) => measure.id === measureId);
    if (!found) {
      throw new Error(`Measure '${measureId}' was not found in mock mode.`);
    }

    return {
      ...found,
      measurementPeriod: formatPeriodLabel(new Date().getFullYear()),
    };
  }

  async submitEvaluation(
    request: EvaluationSubmissionRequest,
  ): Promise<EvaluationSubmissionResponse> {
    await wait(DEMO_DELAY_MS);

    const evaluationId = `demo-run-${String(serial).padStart(4, '0')}`;
    serial += 1;

    const submittedAt = nowIso();

    const pendingResult: EvaluationResult = {
      evaluationId,
      status: 'pending',
      measureId: request.measureId,
      measurementPeriodStart: request.measurementPeriodStart,
      measurementPeriodEnd: request.measurementPeriodEnd,
      submittedAt,
      modeLabel: 'demo',
      warnings: ['Evaluation has been queued in mock mode.'],
      patientResults: request.patientBundleFiles.map((file) => ({
        patientId: toPatientId(file.name),
        status: 'pending',
      })),
    };

    evaluations.set(evaluationId, pendingResult);
    histories.unshift({
      evaluationId,
      measureId: request.measureId,
      status: 'pending',
      submittedAt,
    });

    return {
      evaluationId,
      status: 'pending',
      submittedAt,
    };
  }

  async getEvaluationResult(evaluationId: string): Promise<EvaluationResult> {
    await wait(DEMO_DELAY_MS);

    const existing = evaluations.get(evaluationId);
    if (!existing) {
      throw new Error(`Evaluation '${evaluationId}' was not found in mock mode.`);
    }

    const finalized = finalizeIfReady(existing);
    evaluations.set(evaluationId, finalized);

    const historyIndex = histories.findIndex(
      (item) => item.evaluationId === evaluationId,
    );
    if (historyIndex >= 0) {
      histories[historyIndex] = {
        ...histories[historyIndex],
        status: finalized.status,
        completedAt: finalized.completedAt,
      };
    }

    return finalized;
  }

  async listEvaluationHistory(
    query: EvaluationHistoryQuery,
  ): Promise<PaginatedResponse<EvaluationHistoryItem>> {
    await wait(DEMO_DELAY_MS);

    const filtered = histories.filter((entry) => {
      const searchTarget = `${entry.evaluationId} ${entry.measureId}`;
      return (
        matchSearch(searchTarget, query.search) &&
        matchStatus(entry.status, query.status)
      );
    });

    return paginate(filtered, query.page, query.pageSize);
  }

  async listPatientResults(
    query: PatientResultsQuery,
  ): Promise<PaginatedResponse<PatientResult>> {
    await wait(DEMO_DELAY_MS);

    const allResults = Array.from(evaluations.values())
      .filter((evaluation) =>
        query.evaluationId ? evaluation.evaluationId === query.evaluationId : true,
      )
      .flatMap((evaluation) => evaluation.patientResults ?? []);

    const filtered = allResults.filter((result) =>
      matchSearch(result.patientId, query.search),
    );

    return paginate(filtered, query.page, query.pageSize);
  }
}
