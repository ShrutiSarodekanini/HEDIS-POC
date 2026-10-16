import { useMutation, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  Circle,
  LoaderCircle,
  Play,
  RotateCcw,
  UploadCloud,
} from 'lucide-react';
import { FileInputField } from '../../components/FileInputField';
import { StatusBadge } from '../../components/StatusBadge';
import { useAppConfig, useFqmApiService } from '../../app/hooks';
import { ApiError } from '../../services/apiClient';

const AUTO_DETECTED_MEASURES_MODE_ID = '__AUTO_DETECTED_RELATED_MEASURES__';

function validateJsonFile(file: File): string | null {
  const isJsonType = file.type.includes('json') || file.name.endsWith('.json');
  if (!isJsonType) {
    return 'File must be JSON.';
  }

  return null;
}

interface FormErrors {
  patientBundleFiles?: string;
  request?: string;
}

interface RawGapRow {
  gap_id?: string;
  missing_element?: string | null;
  recommended_action?: string | null;
  due_date?: string | null;
  source_evidence_refs?: string[];
  decision_state?: string;
  decision_actor?: string | null;
  decision_time?: string | null;
}

interface RawResultRow {
  member_id?: string;
  measure_id?: string;
  status?: string;
  outcome?: string;
  populations?: {
    initial_population?: boolean | null;
    denominator?: boolean | null;
    numerator?: boolean | null;
    denominator_exclusion?: boolean | null;
  };
  care_gaps?: RawGapRow[];
  data_quality_flags?: string[];
  warnings?: string[];
  errors?: Array<{ message?: string }>;
}

interface OutcomeTableRow {
  rowKey: string;
  memberId: string;
  measureId: string;
  status: string;
  outcome: string;
  initialPopulation: boolean | null;
  denominator: boolean | null;
  numerator: boolean | null;
  exclusion: boolean | null;
  isActionable: boolean;
  gapState: string;
  recommendedAction: string;
  gapRows: RawGapRow[];
}

interface PatientOutcomeSummaryRow {
  patientId: string;
  measureChecks: number;
  status: 'failed' | 'unresolved' | 'running' | 'pending' | 'completed';
  summary: string;
}

interface WorklistRow {
  memberId: string;
  measureId: string;
  gapId: string;
  issueType: 'Policy Blocker' | 'Care Gap';
  decisionState: string;
  missingElement: string;
  recommendedAction: string;
  dueDate: string | null;
  evidenceRefs: string | null;
  decisionActor: string | null;
  decisionTime: string | null;
}

type WorkflowStepState = 'complete' | 'current' | 'upcoming';

function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (typeof error.details === 'string' && error.details.length > 0) {
      return `Backend request failed (${error.status}): ${error.details}`;
    }

    return `Backend request failed with status ${error.status}.`;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'Unknown error occurred while running evaluation.';
}

function toPopulationLabel(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/^./, (value) => value.toUpperCase());
}

function toDisplayFlag(value: boolean | null | undefined): string {
  if (value === true) {
    return 'Yes';
  }
  if (value === false) {
    return 'No';
  }
  return 'n/a';
}

function toBadgeTone(value: string): 'positive' | 'warning' | 'neutral' {
  const normalized = value.toLowerCase();
  if (normalized === 'ok' || normalized === 'completed' || normalized === 'numerator_met') {
    return 'positive';
  }
  if (
    normalized.includes('blocked') ||
    normalized.includes('unresolved') ||
    normalized.includes('not_met')
  ) {
    return 'warning';
  }
  return 'neutral';
}

function resolveRecommendedAction(
  primaryGap: RawGapRow | undefined,
  context: {
    isActionable: boolean;
    measureId: string;
    status: string;
    outcome: string;
    numerator: boolean | null;
    exclusion: boolean | null;
  },
): string {
  if (!context.isActionable) {
    return 'n/a';
  }

  if (primaryGap?.recommended_action) {
    return primaryGap.recommended_action;
  }

  if (context.exclusion === true) {
    return 'Excluded from scoring; no action required';
  }

  const normalizedStatus = context.status.toLowerCase();
  const normalizedOutcome = context.outcome.toLowerCase();

  if (normalizedStatus === 'unresolved' || normalizedOutcome === 'unresolved_policy') {
    return 'Review patient evidence for unresolved policy criteria';
  }

  if (context.numerator === true || normalizedOutcome === 'numerator_met') {
    return 'Gap closed; continue current care plan';
  }

  if (context.numerator === false || normalizedOutcome === 'numerator_not_met') {
    const measureActionMap: Record<string, string> = {
      AAB: 'Avoid unnecessary antibiotic use for acute bronchitis',
      BCSE: 'Schedule breast cancer screening',
      BPD: 'Improve blood pressure follow-up and control',
      CBP: 'Improve blood pressure follow-up and control',
      CCSE: 'Schedule cervical cancer screening',
      COLE: 'Schedule colorectal cancer screening',
      EED: 'Schedule diabetic retinal eye exam',
      GSD: 'Obtain glycemic status lab result',
      KED: 'Complete kidney health evaluation',
      SPD: 'Start or optimize statin therapy',
    };

    return measureActionMap[context.measureId] ?? 'Close numerator criteria using patient evidence';
  }

  return 'Review patient evidence for numerator criteria';
}

function toGapDisplayValue(row: OutcomeTableRow): 'Open' | 'Closed' {
  const normalizedGapState = row.gapState.toLowerCase();
  const normalizedOutcome = row.outcome.toLowerCase();
  const normalizedStatus = row.status.toLowerCase();
  const isDerivedOpenFromPopulation = row.isActionable && row.numerator === false;

  const isOpen =
    isDerivedOpenFromPopulation ||
    normalizedGapState === 'open' ||
    normalizedGapState === 'numerator_not_met' ||
    normalizedGapState === 'unresolved_policy' ||
    normalizedOutcome === 'numerator_not_met' ||
    normalizedOutcome === 'unresolved_policy' ||
    normalizedStatus === 'unresolved';

  return isOpen ? 'Open' : 'Closed';
}

function hasWorklistValue(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.trim().length > 0;
}

export function RunEvaluationWorkspace() {
  const config = useAppConfig();
  const service = useFqmApiService();

  const [patientBundleFiles, setPatientBundleFiles] = useState<File[]>([]);
  const [errors, setErrors] = useState<FormErrors>({});
  const [submittedEvaluationId, setSubmittedEvaluationId] = useState<string | null>(
    null,
  );
  const [resetToken, setResetToken] = useState(0);

  const healthQuery = useQuery({
    queryKey: ['health-check', config.apiBaseUrl],
    queryFn: () => service.healthCheck(),
    enabled: config.mode === 'real',
    retry: 0,
  });

  const submitMutation = useMutation({
    mutationFn: () =>
      service.submitEvaluation({
        measureId: AUTO_DETECTED_MEASURES_MODE_ID,
        patientBundleFiles,
      }),
    onSuccess: (response) => {
      setErrors({});
      setSubmittedEvaluationId(response.evaluationId);
    },
    onError: (error) => {
      setErrors((current) => ({
        ...current,
        request: toErrorMessage(error),
      }));
    },
  });

  const progressQuery = useQuery({
    queryKey: ['evaluation-progress', submittedEvaluationId],
    queryFn: () => service.getEvaluationResult(submittedEvaluationId ?? ''),
    enabled: Boolean(submittedEvaluationId),
    refetchInterval: (queryState) => {
      const status = queryState.state.data?.status;
      return status === 'pending' || status === 'running' ? 2_000 : false;
    },
  });

  const fileValidation = useMemo(() => {
    const patientFileErrors = patientBundleFiles
      .map((file) => ({
        file,
        error: validateJsonFile(file),
      }))
      .filter((entry) => entry.error);

    return {
      patientFileErrors,
    };
  }, [patientBundleFiles]);

  function validateForm(): boolean {
    const nextErrors: FormErrors = {};

    if (patientBundleFiles.length === 0) {
      nextErrors.patientBundleFiles =
        'At least one patient bundle file is required.';
    } else if (fileValidation.patientFileErrors.length > 0) {
      nextErrors.patientBundleFiles = 'One or more patient files are invalid.';
    }

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!validateForm()) {
      return;
    }

    submitMutation.mutate();
  }

  function resetAll() {
    setPatientBundleFiles([]);
    setErrors({});
    setSubmittedEvaluationId(null);
    setResetToken((current) => current + 1);
  }

  function removePatientFile(index: number) {
    setPatientBundleFiles((current) => current.filter((_, i) => i !== index));
  }

  const latestResult = progressQuery.data;
  const isEvaluationRunning =
    latestResult?.status === 'pending' || latestResult?.status === 'running';
  const hasEvaluationCompleted = latestResult?.status === 'completed';
  const hasMeasureDetectionReady = true;
  const hasPatientUpload =
    patientBundleFiles.length > 0 && fileValidation.patientFileErrors.length === 0;
  const hasRunStarted = Boolean(submittedEvaluationId);

  const workflowSteps = useMemo(
    () => [
      {
        key: 'measure',
        title: 'Auto-detect measures',
        detail: 'Engine detects patient-related measures automatically',
        state: 'complete' as WorkflowStepState,
      },
      {
        key: 'upload',
        title: 'Upload patients',
        detail: hasPatientUpload
          ? `${patientBundleFiles.length} patient bundle${patientBundleFiles.length === 1 ? '' : 's'} validated`
          : 'Upload one or more patient bundle files',
        state: (
          hasPatientUpload
            ? 'complete'
            : hasMeasureDetectionReady
              ? 'current'
              : 'upcoming'
        ) as WorkflowStepState,
      },
      {
        key: 'run',
        title: 'Process evaluation',
        detail: isEvaluationRunning
          ? 'Engine is processing your request'
          : hasRunStarted
            ? 'Evaluation submitted'
            : 'Run the evaluation',
        state: (
          hasRunStarted
            ? 'complete'
            : hasPatientUpload
              ? 'current'
              : 'upcoming'
        ) as WorkflowStepState,
      },
      {
        key: 'results',
        title: 'Review results',
        detail: hasEvaluationCompleted
          ? 'Results ready to inspect'
          : 'Results will appear after processing',
        state: (
          hasEvaluationCompleted
            ? 'complete'
            : hasRunStarted
              ? 'current'
              : 'upcoming'
        ) as WorkflowStepState,
      },
    ],
    [
      hasEvaluationCompleted,
      hasMeasureDetectionReady,
      hasPatientUpload,
      hasRunStarted,
      isEvaluationRunning,
      patientBundleFiles.length,
    ],
  );

  const completedStepCount = workflowSteps.filter((step) => step.state === 'complete').length;
  const workflowCompletionPercent = Math.round(
    (completedStepCount / workflowSteps.length) * 100,
  );

  const uploadProgressStages = [
    {
      id: 'selected',
      label: 'Selected',
      active: patientBundleFiles.length > 0,
    },
    {
      id: 'validated',
      label: 'Validated',
      active: hasPatientUpload,
    },
    {
      id: 'queued',
      label: 'Queued',
      active: hasRunStarted,
    },
    {
      id: 'running',
      label: 'Running',
      active: isEvaluationRunning,
    },
    {
      id: 'ready',
      label: 'Results ready',
      active: hasEvaluationCompleted,
    },
  ];

  const canRunInMode = config.mode === 'real';
  const isRunDisabled =
    !canRunInMode ||
    patientBundleFiles.length === 0 ||
    submitMutation.isPending ||
    isEvaluationRunning;

  const statusTone =
    config.mode === 'real' && !healthQuery.isError && healthQuery.data?.status === 'ok'
      ? 'ok'
      : 'degraded';

  const connectionStateLabel =
    statusTone === 'ok' ? 'Connected' : 'Unavailable';

  const measurementPeriodLabel =
    latestResult?.measurementPeriodStart && latestResult?.measurementPeriodEnd
      ? `${latestResult.measurementPeriodStart} to ${latestResult.measurementPeriodEnd}`
      : null;

  const populationEntries = latestResult?.populationCounts
    ? Object.entries(latestResult.populationCounts)
    : [];

  const rawResultRows = useMemo<RawResultRow[]>(() => {
    const payload = latestResult?.resultPayload;
    if (!payload) {
      return [];
    }

    const entries = Array.isArray(payload) ? payload : [payload];
    const rows: RawResultRow[] = [];

    for (const entry of entries) {
      if (entry && typeof entry === 'object' && Array.isArray((entry as { results?: unknown[] }).results)) {
        for (const child of (entry as { results: unknown[] }).results) {
          if (child && typeof child === 'object') {
            rows.push(child as RawResultRow);
          }
        }
        continue;
      }

      if (entry && typeof entry === 'object') {
        rows.push(entry as RawResultRow);
      }
    }

    return rows;
  }, [latestResult?.resultPayload]);

  const outcomeTableRows = useMemo<OutcomeTableRow[]>(() => {
    return rawResultRows.map((row, index) => {
      const gaps = Array.isArray(row.care_gaps) ? row.care_gaps : [];
      const primaryGap = gaps[0];
      const denominator = row.populations?.denominator ?? null;
      const initialPopulation = row.populations?.initial_population ?? null;
      const numerator = row.populations?.numerator ?? null;
      const isActionable = denominator === true;
      const resolvedMeasureId = row.measure_id ?? latestResult?.measureId ?? 'UNKNOWN';
      const rawOutcome = row.outcome ?? 'unknown';
      const normalizedRawOutcome = rawOutcome.toLowerCase();
      const resolvedOutcome =
        isActionable && numerator === false && !['numerator_not_met', 'unresolved_policy', 'blocked', 'unresolved'].includes(normalizedRawOutcome)
          ? 'numerator_not_met'
          : rawOutcome;
      const gapState = !isActionable
        ? 'not_applicable'
        : primaryGap?.decision_state ??
          (resolvedOutcome === 'numerator_not_met' || numerator === false
            ? 'numerator_not_met'
            : gaps.length > 0
              ? 'open'
              : 'closed');
      const resolvedStatus = row.status ?? 'unknown';
      const recommendedAction = resolveRecommendedAction(
        primaryGap,
        {
          isActionable,
          measureId: resolvedMeasureId,
          status: resolvedStatus,
          outcome: resolvedOutcome,
          numerator,
          exclusion: row.populations?.denominator_exclusion ?? null,
        },
      );

      return {
        rowKey: `${row.member_id ?? 'member'}:${resolvedMeasureId}:${index}`,
        memberId: row.member_id ?? `member-${index + 1}`,
        measureId: resolvedMeasureId,
        status: resolvedStatus,
        outcome: resolvedOutcome,
        initialPopulation,
        denominator,
        numerator,
        exclusion: row.populations?.denominator_exclusion ?? null,
        isActionable,
        gapState,
        recommendedAction,
        gapRows: gaps,
      };
    });
  }, [
    latestResult?.measureId,
    rawResultRows,
  ]);

  const worklistRows = useMemo<WorklistRow[]>(
    () =>
      outcomeTableRows.flatMap((resultRow) => {
        if (!resultRow.isActionable) {
          return [];
        }

        if (resultRow.gapRows.length > 0) {
          return resultRow.gapRows.map((gap, index) => ({
            memberId: resultRow.memberId,
            measureId: resultRow.measureId,
            gapId: gap.gap_id ?? `${resultRow.measureId}:${resultRow.memberId}:${index + 1}`,
            issueType:
              gap.decision_state === 'unresolved_policy' ? 'Policy Blocker' : 'Care Gap',
            decisionState: gap.decision_state ?? 'unresolved',
            missingElement: gap.missing_element ?? 'n/a',
            recommendedAction: gap.recommended_action ?? resultRow.recommendedAction,
            dueDate: gap.due_date ?? null,
            evidenceRefs: (gap.source_evidence_refs ?? []).join(', ') || null,
            decisionActor: gap.decision_actor ?? null,
            decisionTime: gap.decision_time ?? null,
          }));
        }

        if (resultRow.outcome === 'numerator_not_met' || resultRow.status === 'unresolved' || resultRow.numerator === false) {
          return [{
            memberId: resultRow.memberId,
            measureId: resultRow.measureId,
            gapId: `${resultRow.measureId}:${resultRow.memberId}:derived`,
            issueType:
              resultRow.outcome === 'unresolved_policy' || resultRow.status === 'unresolved'
                ? 'Policy Blocker'
                : 'Care Gap',
            decisionState:
              resultRow.outcome === 'unresolved_policy' || resultRow.status === 'unresolved'
                ? 'unresolved_policy'
                : 'numerator_not_met',
            missingElement: 'numerator',
            recommendedAction: resultRow.recommendedAction,
            dueDate: null,
            evidenceRefs: null,
            decisionActor: null,
            decisionTime: null,
          }];
        }

        return [];
      }),
    [outcomeTableRows],
  );

  const worklistColumnVisibility = useMemo(
    () => ({
      dueDate: worklistRows.some((row) => hasWorklistValue(row.dueDate)),
      evidenceRefs: worklistRows.some((row) => hasWorklistValue(row.evidenceRefs)),
      decisionActor: worklistRows.some((row) => hasWorklistValue(row.decisionActor)),
      decisionTime: worklistRows.some((row) => hasWorklistValue(row.decisionTime)),
    }),
    [worklistRows],
  );

  const calculationSummary = useMemo(() => {
    const elapsedMs =
      latestResult?.submittedAt && latestResult?.completedAt
        ? Math.max(
            0,
            new Date(latestResult.completedAt).getTime() -
              new Date(latestResult.submittedAt).getTime(),
          )
        : null;

    const uniquePatientCount = new Set(outcomeTableRows.map((row) => row.memberId)).size;
    const measureEvaluations = outcomeTableRows.length;
    const actionableRows = outcomeTableRows.filter((row) => row.isActionable);
    const eligible = actionableRows.length;
    const numeratorMet = actionableRows.filter((row) => row.numerator === true).length;
    const exclusions = outcomeTableRows.filter((row) => row.exclusion === true).length;
    const policyBlockers = actionableRows.filter(
      (row) =>
        row.outcome === 'unresolved_policy' ||
        row.status === 'unresolved' ||
        row.gapRows.some((gap) => gap.decision_state === 'unresolved_policy'),
    ).length;
    const openGaps = actionableRows.filter((row) => row.outcome === 'numerator_not_met' || row.numerator === false).length;
    const currentRate = eligible > 0 ? (numeratorMet / eligible) * 100 : 0;

    const status = latestResult?.status ?? 'completed';
    const outcome =
      policyBlockers > 0
        ? 'unresolved_policy'
        : openGaps > 0
          ? 'numerator_not_met'
          : eligible > 0 && numeratorMet === eligible
            ? 'numerator_met'
            : numeratorMet > 0
              ? 'partial_met'
              : 'unknown';

    return {
      status,
      type: measureEvaluations > 1 ? 'batch' : 'member',
      outcome,
      elapsedMs,
      patients: uniquePatientCount,
      measureEvaluations,
      eligible,
      numeratorMet,
      exclusions,
      openGaps,
      policyBlockers,
      currentRate,
    };
  }, [latestResult?.completedAt, latestResult?.status, latestResult?.submittedAt, outcomeTableRows]);

  const patientOutcomeSummaryRows = useMemo<PatientOutcomeSummaryRow[]>(() => {
    if (outcomeTableRows.length === 0) {
      return (latestResult?.patientResults ?? []).map((patient) => {
        const populations = patient.populations ?? {};
        const denominator = populations.denominator === true;
        const numerator = populations.numerator === true;
        const status = patient.status;
        const summary = denominator
          ? `${numerator ? 1 : 0}/1 met, ${numerator ? 0 : 1} open gaps, 0 blockers, 0 not-applicable`
          : '0/0 met, 0 open gaps, 0 blockers, 1 not-applicable';

        return {
          patientId: patient.patientId,
          measureChecks: 1,
          status,
          summary,
        };
      });
    }

    const grouped = new Map<string, OutcomeTableRow[]>();

    for (const row of outcomeTableRows) {
      if (!grouped.has(row.memberId)) {
        grouped.set(row.memberId, []);
      }
      grouped.get(row.memberId)?.push(row);
    }

    return Array.from(grouped.entries()).map(([patientId, rows]) => {
      const actionableRows = rows.filter((row) => row.isActionable);
      const notApplicable = rows.length - actionableRows.length;
      const policyBlockers = actionableRows.filter(
        (row) =>
          row.status === 'unresolved' ||
          row.outcome === 'unresolved_policy' ||
          row.gapRows.some((gap) => gap.decision_state === 'unresolved_policy'),
      ).length;
      const openGaps = actionableRows.filter((row) => row.outcome === 'numerator_not_met' || row.numerator === false).length;
      const numeratorMet = actionableRows.filter((row) => row.numerator === true).length;
      const failed = actionableRows.some((row) => row.status === 'failed');
      const unresolved = actionableRows.some((row) => row.status === 'unresolved');
      const running = actionableRows.some((row) => row.status === 'running');
      const pending = actionableRows.some((row) => row.status === 'pending');

      let status: PatientOutcomeSummaryRow['status'] = 'completed';
      if (failed) {
        status = 'failed';
      } else if (unresolved) {
        status = 'unresolved';
      } else if (running) {
        status = 'running';
      } else if (pending) {
        status = 'pending';
      }

      const summary = `${numeratorMet}/${actionableRows.length} met, ${openGaps} open gaps, ${policyBlockers} blockers, ${notApplicable} not-applicable`;

      return {
        patientId,
        measureChecks: rows.length,
        status,
        summary,
      };
    });
  }, [latestResult?.patientResults, outcomeTableRows]);

  return (
    <main className="engine-page" id="main-content">
      <section className="engine-shell">
        <header className="engine-header">
          <div>
            <p className="eyebrow">FQM EXECUTION</p>
            <h1>FQM Engine</h1>
            <p>Evaluate FHIR measure bundles and inspect execution results.</p>
          </div>
          <div className="header-status-pill" role="status">
            <Activity size={14} aria-hidden="true" />
            <span>{connectionStateLabel}</span>
          </div>
        </header>

        <section className="panel workflow-panel" aria-label="Evaluation workflow">
          <div className="workflow-panel-header">
            <h2>Step-by-step workflow</h2>
            <p>{workflowCompletionPercent}% complete</p>
          </div>
          <div className="workflow-progress-track" aria-hidden="true">
            <span style={{ width: `${workflowCompletionPercent}%` }} />
          </div>
          <div className="workflow-steps">
            {workflowSteps.map((step) => (
              <article
                key={step.key}
                className={`workflow-step workflow-step-${step.state}`}
                aria-current={step.state === 'current' ? 'step' : undefined}
              >
                <div className="workflow-step-icon" aria-hidden="true">
                  {step.state === 'complete' ? (
                    <CheckCircle2 size={16} />
                  ) : step.state === 'current' ? (
                    <LoaderCircle size={16} className="rotating" />
                  ) : (
                    <Circle size={16} />
                  )}
                </div>
                <div>
                  <p className="workflow-step-title">{step.title}</p>
                  <p className="workflow-step-detail">{step.detail}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <form className="panel form-layout" onSubmit={onSubmit} noValidate>
          <h2>Evaluation inputs</h2>

          <section className="selection-summary" aria-label="Automatic measure detection">
            <p>
              <span>Measure mode:</span>
              <strong>Auto-detect related measures</strong>
            </p>
          </section>

          <FileInputField
            key={`patient-${resetToken}`}
            id="patientBundleFiles"
            label="Patient bundle upload"
            title="Upload patient bundles"
            description="Select one or more FHIR JSON patient bundles."
            accept="application/json,.json"
            files={patientBundleFiles}
            browseLabel="Browse files"
            multiple
            required
            error={errors.patientBundleFiles}
            onChange={(files) => setPatientBundleFiles(Array.from(files ?? []))}
            onRemoveFile={removePatientFile}
          />

          <section className="upload-process" aria-label="Upload processing status">
            <p className="upload-process-title">Upload processing</p>
            <div className="upload-process-steps">
              {uploadProgressStages.map((stage) => (
                <span
                  key={stage.id}
                  className={`upload-step-chip ${stage.active ? 'active' : ''}`}
                >
                  {stage.label}
                </span>
              ))}
            </div>
          </section>

          <section className="selection-summary" aria-label="Input summary">
            <p>
              <span>Measure detection:</span>
              <strong>Automatic</strong>
            </p>
            <p>
              <span>Patient bundles:</span>
              <strong>{patientBundleFiles.length}</strong>
            </p>
          </section>

          {errors.request ? <p className="field-error">{errors.request}</p> : null}

          <div className="form-actions">
            <button
              type="submit"
              className="primary-button"
              disabled={isRunDisabled}
            >
              {submitMutation.isPending || isEvaluationRunning ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  Running evaluation...
                </>
              ) : (
                <>
                  <Play size={16} aria-hidden="true" />
                  Run FQM Evaluation
                </>
              )}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={resetAll}
              disabled={submitMutation.isPending}
            >
              <RotateCcw size={16} aria-hidden="true" />
              Clear inputs
            </button>
          </div>
        </form>

        <section className="panel">
          <h2>Results</h2>
          {!submittedEvaluationId ? (
            <div className="empty-results" role="status">
              <UploadCloud size={18} aria-hidden="true" />
              <div>
                <p className="empty-title">No evaluation results yet</p>
                <p className="field-helper">
                  Upload patient bundles and run an evaluation to view results.
                </p>
              </div>
            </div>
          ) : null}

          {progressQuery.isLoading && submittedEvaluationId ? (
            <p className="field-helper">Evaluation in progress...</p>
          ) : null}

          {progressQuery.isError ? (
            <p className="field-error">
              Unable to retrieve current run status. {toErrorMessage(progressQuery.error)}
            </p>
          ) : null}

          {latestResult ? (
            <div className="progress-state">
              <div className="result-meta-grid">
                <div>
                  <p className="result-meta-label">Execution status</p>
                  <StatusBadge status={latestResult.status} />
                </div>
                <div>
                  <p className="result-meta-label">Measure identifier</p>
                  <p>{latestResult.measureId}</p>
                </div>
                {measurementPeriodLabel ? (
                  <div>
                    <p className="result-meta-label">Measurement period</p>
                    <p>{measurementPeriodLabel}</p>
                  </div>
                ) : null}
                <div>
                  <p className="result-meta-label">Evaluation ID</p>
                  <p className="mono-inline">{latestResult.evaluationId}</p>
                </div>
              </div>

              {outcomeTableRows.length > 0 ? (
                <section className="summary-block">
                  <h3>Calculation Summary</h3>
                  <div className="summary-grid">
                    <article className="summary-card"><p>Status</p><strong>{calculationSummary.status}</strong></article>
                    <article className="summary-card"><p>Type</p><strong>{calculationSummary.type}</strong></article>
                    <article className="summary-card"><p>Outcome</p><strong>{calculationSummary.outcome}</strong></article>
                    <article className="summary-card"><p>Elapsed</p><strong>{calculationSummary.elapsedMs != null ? `${calculationSummary.elapsedMs} ms` : 'n/a'}</strong></article>
                    <article className="summary-card"><p>Patients</p><strong>{calculationSummary.patients}</strong></article>
                    <article className="summary-card"><p>Measure Checks</p><strong>{calculationSummary.measureEvaluations}</strong></article>
                    <article className="summary-card"><p>Eligible</p><strong>{calculationSummary.eligible}</strong></article>
                    <article className="summary-card"><p>Numerator Met</p><strong>{calculationSummary.numeratorMet}</strong></article>
                    <article className="summary-card"><p>Exclusions</p><strong>{calculationSummary.exclusions}</strong></article>
                    <article className="summary-card"><p>Open Gaps</p><strong>{calculationSummary.openGaps}</strong></article>
                    <article className="summary-card"><p>Policy Blockers</p><strong>{calculationSummary.policyBlockers}</strong></article>
                    <article className="summary-card"><p>Current Rate</p><strong>{calculationSummary.currentRate.toFixed(1)}%</strong></article>
                  </div>
                  <p className="field-helper">
                    In All 10 mode, each row represents one measure evaluation for the same uploaded patient.
                  </p>
                </section>
              ) : null}

              {outcomeTableRows.length > 0 ? (
                <section>
                  <h3>Measure Outcome Table</h3>
                  <div className="table-wrap">
                    <table className="results-table">
                      <thead>
                        <tr>
                          <th>Member</th>
                          <th>Measure</th>
                          <th>Status</th>
                          <th>Outcome</th>
                          <th>Denominator</th>
                          <th>Numerator</th>
                          <th>Exclusion</th>
                          <th>Gap</th>
                          <th>Recommended Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {outcomeTableRows.map((row) => (
                          (() => {
                            const gapDisplayValue = toGapDisplayValue(row);
                            const gapTone = gapDisplayValue === 'Open' ? 'warning' : 'positive';

                            return (
                          <tr key={row.rowKey}>
                            <td className="mono-inline">{row.memberId}</td>
                            <td>{row.measureId}</td>
                            <td>
                              <span className={`outcome-pill tone-${toBadgeTone(row.status)}`}>
                                {row.status}
                              </span>
                            </td>
                            <td>
                              <span className={`outcome-pill tone-${toBadgeTone(row.outcome)}`}>
                                {row.outcome}
                              </span>
                            </td>
                            <td>{toDisplayFlag(row.denominator)}</td>
                            <td>{toDisplayFlag(row.numerator)}</td>
                            <td>{toDisplayFlag(row.exclusion)}</td>
                            <td>
                              <span className={`outcome-pill tone-${gapTone}`}>
                                {gapDisplayValue}
                              </span>
                            </td>
                            <td>{row.recommendedAction}</td>
                          </tr>
                            );
                          })()
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}

              {worklistRows.length > 0 ? (
                <section>
                  <h3>Gap and Blocker Worklist</h3>
                  <div className="table-wrap">
                    <table className="results-table">
                      <thead>
                        <tr>
                          <th>Member</th>
                          <th>Measure</th>
                          <th>Gap ID</th>
                          <th>Issue Type</th>
                          <th>Decision State</th>
                          <th>Missing Element</th>
                          <th>Recommended Action</th>
                          {worklistColumnVisibility.dueDate ? <th>Due Date</th> : null}
                          {worklistColumnVisibility.evidenceRefs ? <th>Evidence Refs</th> : null}
                          {worklistColumnVisibility.decisionActor ? <th>Decision Actor</th> : null}
                          {worklistColumnVisibility.decisionTime ? <th>Decision Time</th> : null}
                        </tr>
                      </thead>
                      <tbody>
                        {worklistRows.map((row) => (
                          <tr key={`${row.gapId}-${row.memberId}`}>
                            <td className="mono-inline">{row.memberId}</td>
                            <td>{row.measureId}</td>
                            <td className="mono-inline">{row.gapId}</td>
                            <td>{row.issueType}</td>
                            <td>{row.decisionState}</td>
                            <td>{row.missingElement}</td>
                            <td>{row.recommendedAction}</td>
                            {worklistColumnVisibility.dueDate ? <td>{row.dueDate ?? 'n/a'}</td> : null}
                            {worklistColumnVisibility.evidenceRefs ? <td>{row.evidenceRefs ?? 'n/a'}</td> : null}
                            {worklistColumnVisibility.decisionActor ? <td>{row.decisionActor ?? 'n/a'}</td> : null}
                            {worklistColumnVisibility.decisionTime ? <td>{row.decisionTime ?? 'n/a'}</td> : null}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : null}

              {populationEntries.length > 0 ? (
                <div>
                  <h3>Population results</h3>
                  <div className="population-grid">
                    {populationEntries.map(([key, value]) => (
                      <article key={key} className="population-card">
                        <p>{toPopulationLabel(key)}</p>
                        <strong>{value ?? 'Unavailable'}</strong>
                      </article>
                    ))}
                  </div>
                </div>
              ) : null}

              {patientOutcomeSummaryRows.length > 0 ? (
                <div>
                  <h3>Patient-level outcomes</h3>
                  <div className="table-wrap">
                    <table className="results-table">
                      <thead>
                        <tr>
                          <th>Patient ID</th>
                          <th>Measure Checks</th>
                          <th>Status</th>
                          <th>Outcome Summary</th>
                        </tr>
                      </thead>
                      <tbody>
                        {patientOutcomeSummaryRows.map((patient) => (
                          <tr key={patient.patientId}>
                            <td className="mono-inline">{patient.patientId}</td>
                            <td>{patient.measureChecks}</td>
                            <td>
                              <StatusBadge status={patient.status} />
                            </td>
                            <td>{patient.summary}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {latestResult.warnings?.length ? (
                <section className="result-alert warning-alert">
                  <h3>Warnings</h3>
                  <ul>
                    {latestResult.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {latestResult.unresolvedReasons?.length ? (
                <section className="result-alert unresolved-alert">
                  <h3>Unresolved reasons</h3>
                  <ul>
                    {latestResult.unresolvedReasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </section>
              ) : null}

              {latestResult.errors?.length ? (
                <section className="result-alert error-alert">
                  <h3>Execution errors</h3>
                  <ul>
                    {latestResult.errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </div>
          ) : null}
        </section>
      </section>
    </main>
  );
}
