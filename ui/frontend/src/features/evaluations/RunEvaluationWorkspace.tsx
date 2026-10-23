import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import JSZip from 'jszip';
import {
  Activity,
  ArrowDownToLine,
  CheckCircle2,
  CircleDot,
  CirclePlay,
  Circle,
  FileSearch,
  LayoutGrid,
  LoaderCircle,
  List,
  Play,
  RotateCcw,
  UploadCloud,
} from 'lucide-react';
import { FileInputField } from '../../components/FileInputField';
import { StatusBadge } from '../../components/StatusBadge';
import { useAppConfig, useFqmApiService } from '../../app/hooks';
import { ApiError } from '../../services/apiClient';

const AUTO_DETECTED_MEASURES_MODE_ID = '__AUTO_DETECTED_RELATED_MEASURES__';
const DB_PATIENT_PAGE_SIZE = 20;
const DB_PATIENT_SCAN_LIMIT = 8;

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

interface IngestionDatasetResponse {
  input_dir?: string;
  found_files?: string[];
  missing_required_files?: string[];
  row_counts?: Record<string, number>;
  total_rows?: number;
}

interface IngestionJobResponse {
  id?: string;
  job_id?: string;
  status?: string;
  resources_generated?: number;
  resources_persisted?: number;
  warning_count?: number;
  error_count?: number;
  runtime?: {
    status?: string;
    started_at?: string;
    completed_at?: string;
    stages?: Array<{
      name: string;
      status: string;
      details?: string;
      elapsed_seconds?: number | null;
    }>;
  };
}

interface IngestionValidationIssue {
  id: number;
  severity: string;
  issue_category: string;
  source_file?: string | null;
  source_record_id?: string | null;
  diagnostic: string;
}

interface IngestionLogEntry {
  at: string;
  message: string;
}

interface IngestionPatientResourcesResponse {
  job_id: string;
  patient_id: string;
  resources: Array<{
    resource_json?: Record<string, unknown>;
  }>;
}

interface DbPatientDetail {
  patient_id: string;
  resource_count?: number;
  estimated_size_bytes?: number;
  saved_at?: string | null;
}

type WorkflowStepState = 'complete' | 'current' | 'upcoming';

function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details && typeof error.details === 'object') {
      const detailsRecord = error.details as Record<string, unknown>;
      const topDetail = detailsRecord.detail;

      if (typeof topDetail === 'string' && topDetail.length > 0) {
        return `Backend request failed (${error.status}): ${topDetail}`;
      }

      if (topDetail && typeof topDetail === 'object') {
        const inner = topDetail as Record<string, unknown>;
        const missing = inner.missing_required_files;
        if (Array.isArray(missing) && missing.length > 0) {
          return `Cannot start ingestion. Missing required files: ${missing.join(', ')}`;
        }
      }

      const missingTop = detailsRecord.missing_required_files;
      if (Array.isArray(missingTop) && missingTop.length > 0) {
        return `Cannot start ingestion. Missing required files: ${missingTop.join(', ')}`;
      }
    }

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

function formatBytes(value?: number): string {
  if (!Number.isFinite(value) || value === undefined || value < 0) {
    return 'n/a';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const kb = value / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  const mb = kb / 1024;
  return `${mb.toFixed(2)} MB`;
}

function formatSavedAt(value?: string | null): string {
  if (!value) {
    return 'n/a';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return 'n/a';
  }
  return parsed.toLocaleString();
}

export function RunEvaluationWorkspace() {
  const config = useAppConfig();
  const service = useFqmApiService();

  const [patientBundleFiles, setPatientBundleFiles] = useState<File[]>([]);
  const [ingestionInputDir, setIngestionInputDir] = useState('');
  const [ingestionFiles, setIngestionFiles] = useState<File[]>([]);
  const [ingestionJobId, setIngestionJobId] = useState('');
  const [ingestionPatients, setIngestionPatients] = useState<string[]>([]);
  const [selectedIngestionPatient, setSelectedIngestionPatient] = useState('');
  const [selectedPatientFhir, setSelectedPatientFhir] = useState('');
  const [allPatientsFhir, setAllPatientsFhir] = useState<{
    jobId: string;
    patientCount: number;
    exportedAt: string;
  } | null>(null);
  const [dbSourceJobId, setDbSourceJobId] = useState('');
  const [dbSourcePatients, setDbSourcePatients] = useState<string[]>([]);
  const [dbPatientDetailsById, setDbPatientDetailsById] = useState<Record<string, DbPatientDetail>>({});
  const [dbSourceSearch, setDbSourceSearch] = useState('');
  const [dbSelectedPatients, setDbSelectedPatients] = useState<string[]>([]);
  const [dbPatientJobMap, setDbPatientJobMap] = useState<Record<string, string>>({});
  const [dbPatientJobCandidates, setDbPatientJobCandidates] = useState<Record<string, string[]>>({});
  const [dbSourceBusy, setDbSourceBusy] = useState(false);
  const [dbSourceMessage, setDbSourceMessage] = useState('');
  const [dbSourceError, setDbSourceError] = useState('');
  const [dbVisiblePatientCount, setDbVisiblePatientCount] = useState(DB_PATIENT_PAGE_SIZE);
  const [ingestionBusy, setIngestionBusy] = useState(false);
  const [ingestionStatusMessage, setIngestionStatusMessage] = useState('');
  const [ingestionErrorMessage, setIngestionErrorMessage] = useState('');
  const [ingestionDatasetMeta, setIngestionDatasetMeta] = useState<IngestionDatasetResponse | null>(null);
  const [ingestionJobSummary, setIngestionJobSummary] = useState<IngestionJobResponse | null>(null);
  const [ingestionValidationIssues, setIngestionValidationIssues] = useState<IngestionValidationIssue[]>([]);
  const [ingestionLog, setIngestionLog] = useState<IngestionLogEntry[]>([]);
  const stageStatusRef = useRef<Record<string, string>>({});
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
    mutationFn: (files: File[]) =>
      service.submitEvaluation({
        measureId: AUTO_DETECTED_MEASURES_MODE_ID,
        patientBundleFiles: files,
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

  const ingestionJobQuery = useQuery({
    queryKey: ['ingestion-job-live', ingestionJobId],
    queryFn: () => ingestionApiRequest(`/jobs/${encodeURIComponent(ingestionJobId.trim())}`) as Promise<IngestionJobResponse>,
    enabled: Boolean(ingestionJobId),
    refetchInterval: (queryState) => {
      const runtimeStatus = queryState.state.data?.runtime?.status?.toLowerCase();
      const topLevelStatus = queryState.state.data?.status?.toLowerCase();
      const current = runtimeStatus || topLevelStatus;
      return current === 'running' || current === 'pending' || current === 'started' ? 1500 : false;
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

  useEffect(() => {
    if (!ingestionJobQuery.data) {
      return;
    }
    setIngestionJobSummary(ingestionJobQuery.data);
  }, [ingestionJobQuery.data]);

  useEffect(() => {
    const stages = ingestionJobSummary?.runtime?.stages ?? [];
    if (stages.length === 0) {
      return;
    }

    const now = new Date().toLocaleTimeString();
    const nextEntries: IngestionLogEntry[] = [];

    for (const stage of stages) {
      const previous = stageStatusRef.current[stage.name];
      if (previous !== stage.status) {
        stageStatusRef.current[stage.name] = stage.status;
        nextEntries.push({
          at: now,
          message: `${stage.name}: ${stage.status}${stage.details ? ` - ${stage.details}` : ''}`,
        });
      }
    }

    if (nextEntries.length > 0) {
      setIngestionLog((current) => [...nextEntries, ...current].slice(0, 20));
    }
  }, [ingestionJobSummary?.runtime?.stages]);

  async function ingestionApiRequest(path: string, init?: RequestInit): Promise<unknown> {
    const response = await fetch(`${config.apiBaseUrl}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.headers ?? {}),
      },
    });

    const payload = await response
      .text()
      .then((raw) => {
        try {
          return raw ? JSON.parse(raw) : null;
        } catch {
          return raw;
        }
      });

    if (!response.ok) {
      const detailText =
        typeof payload === 'string'
          ? payload
          : typeof payload === 'object' && payload !== null && 'detail' in payload
            ? String((payload as Record<string, unknown>).detail)
            : `Ingestion request failed with status ${response.status}`;
      throw new ApiError(detailText, response.status, payload);
    }

    return payload;
  }

  async function inspectIngestionDataset() {
    if (!ingestionInputDir.trim()) {
      setIngestionErrorMessage('Upload CSV files first to create a dataset.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(
        `/datasets/inspect?input_dir=${encodeURIComponent(ingestionInputDir.trim())}`,
      );
      const dataset = payload as IngestionDatasetResponse;
      setIngestionDatasetMeta(dataset);
      setIngestionStatusMessage(`Dataset inspected successfully. ${dataset.total_rows ?? 0} rows discovered.`);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function uploadIngestionCsvFiles() {
    if (ingestionFiles.length === 0) {
      setIngestionErrorMessage('Select one or more Synthea CSV files first.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const formData = new FormData();
      ingestionFiles.forEach((file) => formData.append('files', file, file.name));
      const payload = await ingestionApiRequest('/datasets/upload', {
        method: 'POST',
        body: formData,
      });
      const asRecord = payload as IngestionDatasetResponse;
      if (asRecord.input_dir) {
        setIngestionInputDir(asRecord.input_dir);
      }
      setIngestionDatasetMeta(asRecord);
      setIngestionJobSummary(null);
      setIngestionValidationIssues([]);
      setIngestionJobId('');
      setIngestionPatients([]);
      setSelectedIngestionPatient('');
      setSelectedPatientFhir('');
      setAllPatientsFhir(null);
      setIngestionStatusMessage(`Uploaded dataset successfully. ${asRecord.total_rows ?? 0} rows discovered.`);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function startIngestionJob() {
    if (!ingestionInputDir.trim()) {
      setIngestionErrorMessage('Upload CSV files first to start ingestion.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const missingRequiredFiles = ingestionDatasetMeta?.missing_required_files ?? [];
      if (missingRequiredFiles.length > 0) {
        throw new ApiError(
          `Missing required files: ${missingRequiredFiles.join(', ')}`,
          400,
          { missing_required_files: missingRequiredFiles },
        );
      }

      const payload = await ingestionApiRequest(
        `/jobs/start?input_dir=${encodeURIComponent(ingestionInputDir.trim())}`,
        { method: 'POST' },
      );
      const asRecord = payload as IngestionJobResponse;
      if (asRecord.job_id) {
        setIngestionJobId(asRecord.job_id);
      }
      stageStatusRef.current = {};
      setIngestionLog([
        {
          at: new Date().toLocaleTimeString(),
          message: `Job started: ${asRecord.job_id ?? 'unknown'}`,
        },
      ]);
      setIngestionJobSummary(asRecord);
      setIngestionPatients([]);
      setSelectedIngestionPatient('');
      setSelectedPatientFhir('');
      setAllPatientsFhir(null);
      setIngestionValidationIssues([]);
      setIngestionStatusMessage('Ingestion job started successfully.');
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function refreshIngestionJob() {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to refresh status.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(`/jobs/${encodeURIComponent(ingestionJobId.trim())}`);
      setIngestionJobSummary(payload as IngestionJobResponse);
      setIngestionStatusMessage('Ingestion status refreshed.');
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function persistIngestionResources() {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to persist resources.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(
        `/jobs/${encodeURIComponent(ingestionJobId.trim())}/persist`,
        { method: 'POST' },
      );
      const asRecord = payload as { resources_persisted?: number };
      const latestJob = await ingestionApiRequest(
        `/jobs/${encodeURIComponent(ingestionJobId.trim())}`,
      ) as IngestionJobResponse;
      setIngestionJobSummary(latestJob);
      setIngestionStatusMessage(
        `Resources persisted successfully. ${asRecord.resources_persisted ?? 0} resources marked persisted.`,
      );
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function loadIngestionValidation() {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to load validation issues.');
      return;
    }
    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(
        `/jobs/${encodeURIComponent(ingestionJobId.trim())}/validation`,
      );
      const asRecord = payload as { issues?: IngestionValidationIssue[] };
      const issues = Array.isArray(asRecord.issues) ? asRecord.issues : [];
      setIngestionValidationIssues(issues);
      setIngestionStatusMessage(
        issues.length > 0
          ? `Validation loaded. ${issues.length} issues found.`
          : 'Validation loaded. No issues found.',
      );
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  function toPatientBundle(
    jobId: string,
    patientId: string,
    resourcesPayload: IngestionPatientResourcesResponse,
  ): Record<string, unknown> {
    const entries = (resourcesPayload.resources ?? [])
      .map((item) => item.resource_json)
      .filter((resource): resource is Record<string, unknown> => Boolean(resource))
      .map((resource) => ({ resource }));

    return {
      resourceType: 'Bundle',
      type: 'collection',
      id: `${jobId}-${patientId}`,
      total: entries.length,
      entry: entries,
    };
  }

  function downloadJsonFile(fileName: string, payload: unknown) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    downloadBlobFile(fileName, blob);
  }

  function downloadBlobFile(fileName: string, blob: Blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function loadIngestionPatients() {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to load patients.');
      return;
    }

    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(`/jobs/${encodeURIComponent(ingestionJobId.trim())}/patients`);
      const asRecord = payload as { patients?: string[] };
      const patients = Array.isArray(asRecord.patients) ? asRecord.patients : [];
      setIngestionPatients(patients);

      if (patients.length === 0) {
        setSelectedIngestionPatient('');
        setSelectedPatientFhir('');
        setIngestionStatusMessage('No patient resources found yet. Run ingestion and persist first.');
        return;
      }

      const firstPatient = patients[0];
      setSelectedIngestionPatient(firstPatient);
      setIngestionStatusMessage(`Loaded ${patients.length} patients.`);
      await loadPatientFhir(firstPatient);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function loadPatientFhir(patientId: string) {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to load patient FHIR data.');
      return;
    }

    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(
        `/jobs/${encodeURIComponent(ingestionJobId.trim())}/patients/${encodeURIComponent(patientId)}/resources`,
      ) as IngestionPatientResourcesResponse;
      const bundle = toPatientBundle(ingestionJobId.trim(), patientId, payload);
      setSelectedIngestionPatient(patientId);
      setSelectedPatientFhir(JSON.stringify(bundle, null, 2));
      setIngestionStatusMessage(`Loaded FHIR bundle preview for patient ${patientId}.`);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function downloadSelectedPatientFhir() {
    if (!selectedIngestionPatient) {
      setIngestionErrorMessage('Select a patient first.');
      return;
    }

    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to download patient FHIR data.');
      return;
    }

    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      const payload = await ingestionApiRequest(
        `/jobs/${encodeURIComponent(ingestionJobId.trim())}/patients/${encodeURIComponent(selectedIngestionPatient)}/resources`,
      ) as IngestionPatientResourcesResponse;
      const bundle = toPatientBundle(ingestionJobId.trim(), selectedIngestionPatient, payload);
      downloadJsonFile(`fhir-${selectedIngestionPatient}.bundle.json`, bundle);
      setIngestionStatusMessage(`Downloaded FHIR bundle for patient ${selectedIngestionPatient}.`);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  async function downloadAllPatientsFhir() {
    if (!ingestionJobId.trim()) {
      setIngestionErrorMessage('Start ingestion first to download all patient FHIR data.');
      return;
    }

    setIngestionBusy(true);
    setIngestionErrorMessage('');
    try {
      let patientIds = ingestionPatients;
      if (patientIds.length === 0) {
        const patientsPayload = await ingestionApiRequest(
          `/jobs/${encodeURIComponent(ingestionJobId.trim())}/patients`,
        ) as { patients?: string[] };
        patientIds = Array.isArray(patientsPayload.patients) ? patientsPayload.patients : [];
        setIngestionPatients(patientIds);
      }

      if (patientIds.length === 0) {
        setIngestionStatusMessage('No patients available for download.');
        return;
      }

      const patientBundles = await Promise.all(
        patientIds.map(async (patientId) => {
          const payload = await ingestionApiRequest(
            `/jobs/${encodeURIComponent(ingestionJobId.trim())}/patients/${encodeURIComponent(patientId)}/resources`,
          ) as IngestionPatientResourcesResponse;
          return {
            patientId,
            bundle: toPatientBundle(ingestionJobId.trim(), patientId, payload),
          };
        }),
      );

      const bundles = patientBundles.map((item) => item.bundle);

      const exportPayload = {
        job_id: ingestionJobId.trim(),
        patient_count: bundles.length,
        exported_at: new Date().toISOString(),
        bundles,
      };

      const zip = new JSZip();
      for (const item of patientBundles) {
        const patientId = item.patientId || 'patient';
        const safePatientId = patientId.replace(/[^a-zA-Z0-9._-]/g, '_');
        zip.file(`patients/fhir-${safePatientId}.bundle.json`, JSON.stringify(item.bundle, null, 2));
      }
      zip.file(
        'manifest.json',
        JSON.stringify(
          {
            job_id: exportPayload.job_id,
            patient_count: exportPayload.patient_count,
            exported_at: exportPayload.exported_at,
          },
          null,
          2,
        ),
      );

      const zipBlob = await zip.generateAsync({ type: 'blob' });
      downloadBlobFile(`fhir-job-${ingestionJobId.trim()}-patients.zip`, zipBlob);

      setAllPatientsFhir({
        jobId: exportPayload.job_id,
        patientCount: exportPayload.patient_count,
        exportedAt: exportPayload.exported_at,
      });
      setIngestionStatusMessage(`Downloaded ZIP export with ${bundles.length} individual patient files.`);
    } catch (error) {
      setIngestionErrorMessage(toErrorMessage(error));
    } finally {
      setIngestionBusy(false);
    }
  }

  function validateForm(): boolean {
    const nextErrors: FormErrors = {};

    if (patientBundleFiles.length === 0 && dbSelectedPatients.length === 0) {
      nextErrors.patientBundleFiles =
        'Select at least one patient bundle file or one database patient.';
    } else if (fileValidation.patientFileErrors.length > 0) {
      nextErrors.patientBundleFiles = 'One or more patient files are invalid.';
    }

    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  }

  async function buildEvaluationPatientFiles(): Promise<File[]> {
    const fileMap = new Map(patientBundleFiles.map((file) => [file.name, file] as const));

    if (dbSelectedPatients.length === 0) {
      return Array.from(fileMap.values());
    }

    async function fetchPatientBundleWithFallback(patientId: string): Promise<Record<string, unknown>> {
      const preferred = dbPatientJobMap[patientId];
      const candidates = dbPatientJobCandidates[patientId] ?? [];
      const orderedCandidates = Array.from(
        new Set([
          ...(preferred ? [preferred] : []),
          ...candidates,
          ...(dbSourceJobId.trim() ? [dbSourceJobId.trim()] : []),
        ].filter((jobId) => Boolean(jobId))),
      );

      let lastError: unknown = null;

      for (const candidateJobId of orderedCandidates) {
        try {
          const payload = await ingestionApiRequest(
            `/jobs/${encodeURIComponent(candidateJobId)}/patients/${encodeURIComponent(patientId)}/resources`,
          ) as IngestionPatientResourcesResponse;

          const hasBundleableResources = (payload.resources ?? []).some(
            (item) => Boolean(item.resource_json && typeof item.resource_json === 'object'),
          );

          if (!hasBundleableResources) {
            continue;
          }

          return toPatientBundle(candidateJobId, patientId, payload);
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            lastError = error;
            continue;
          }
          throw error;
        }
      }

      throw lastError ?? new Error(`No bundle found for patient ${patientId}`);
    }

    setDbSourceMessage('Preparing selected database patients for evaluation...');
    const settled = await Promise.allSettled(
      dbSelectedPatients.map(async (patientId) => {
        const bundle = await fetchPatientBundleWithFallback(patientId);
        const fileName = `fhir-${patientId}.bundle.json`;
        return new File([JSON.stringify(bundle, null, 2)], fileName, {
          type: 'application/json',
        });
      }),
    );

    const failedPatients: string[] = [];
    settled.forEach((entry, index) => {
      if (entry.status === 'fulfilled') {
        fileMap.set(entry.value.name, entry.value);
      } else {
        failedPatients.push(dbSelectedPatients[index] ?? 'unknown');
      }
    });

    if (failedPatients.length > 0) {
      setDbSourceMessage(
        `Prepared ${fileMap.size} total bundle(s). Skipped ${failedPatients.length} selected database patient(s) with no available bundle.`,
      );
    } else {
      setDbSourceMessage(`Prepared ${fileMap.size} total bundle(s) for evaluation.`);
    }

    return Array.from(fileMap.values());
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!validateForm()) {
      return;
    }

    try {
      const files = await buildEvaluationPatientFiles();
      if (files.length === 0) {
        setErrors((current) => ({
          ...current,
          request: 'No valid patient bundles were available to run evaluation.',
        }));
        return;
      }
      submitMutation.mutate(files);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        request: toErrorMessage(error),
      }));
    }
  }

  function resetAll() {
    setPatientBundleFiles([]);
    setDbSelectedPatients([]);
    setErrors({});
    setSubmittedEvaluationId(null);
    setResetToken((current) => current + 1);
  }

  function removePatientFile(index: number) {
    setPatientBundleFiles((current) => current.filter((_, i) => i !== index));
  }

  function removeIngestionFile(index: number) {
    setIngestionFiles((current) => current.filter((_, i) => i !== index));
  }

  const latestResult = progressQuery.data;
  const isEvaluationRunning =
    latestResult?.status === 'pending' || latestResult?.status === 'running';
  const hasEvaluationCompleted = latestResult?.status === 'completed';
  const hasMeasureDetectionReady = true;
  const hasPatientUpload =
    (patientBundleFiles.length > 0 || dbSelectedPatients.length > 0) &&
    fileValidation.patientFileErrors.length === 0;
  const hasRunStarted = Boolean(submittedEvaluationId);

  const workflowSteps = useMemo(
    () => [
      {
        key: 'measure',
        title: 'Standard measure execution',
        detail: 'Checks all 8 active measures and derives relatedness from engine output',
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

  const filteredDbPatients = useMemo(() => {
    const query = dbSourceSearch.trim().toLowerCase();
    if (!query) {
      return dbSourcePatients;
    }
    return dbSourcePatients.filter((patientId) => patientId.toLowerCase().includes(query));
  }, [dbSourcePatients, dbSourceSearch]);

  const visibleDbPatients = useMemo(
    () => filteredDbPatients.slice(0, dbVisiblePatientCount),
    [filteredDbPatients, dbVisiblePatientCount],
  );
  const hasDbSearchQuery = dbSourceSearch.trim().length > 0;

  useEffect(() => {
    setDbVisiblePatientCount(DB_PATIENT_PAGE_SIZE);
  }, [dbSourcePatients, dbSourceSearch]);

  function loadMoreVisibleDbPatients() {
    setDbVisiblePatientCount((current) =>
      Math.min(current + DB_PATIENT_PAGE_SIZE, filteredDbPatients.length),
    );
  }

  function onDbPatientsScroll(event: React.UIEvent<HTMLDivElement>) {
    const element = event.currentTarget;
    const nearBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 16;
    if (nearBottom && dbVisiblePatientCount < filteredDbPatients.length) {
      loadMoreVisibleDbPatients();
    }
  }

  function toggleDbPatientSelection(patientId: string) {
    setDbSelectedPatients((current) =>
      current.includes(patientId)
        ? current.filter((item) => item !== patientId)
        : [...current, patientId],
    );
  }

  function toggleSelectAllFilteredDbPatients() {
    setDbSelectedPatients((current) => {
      const allSelected =
        filteredDbPatients.length > 0 &&
        filteredDbPatients.every((patientId) => current.includes(patientId));

      if (allSelected) {
        return current.filter((patientId) => !filteredDbPatients.includes(patientId));
      }

      const merged = new Set([...current, ...filteredDbPatients]);
      return Array.from(merged);
    });
  }

  async function loadDbPatientsForEvaluation(jobIdOverride?: string) {
    setDbSourceBusy(true);
    setDbSourceError('');
    setDbSourceMessage('');
    try {
      // Refresh should rescan across jobs unless a specific job id is explicitly provided.
      const explicitJobId = jobIdOverride?.trim() ?? '';

      const nextPatientJobMap: Record<string, string> = {};
      const nextPatientJobCandidates: Record<string, string[]> = {};
      const nextPatientDetailsById: Record<string, DbPatientDetail> = {};
      let resolvedJobId = explicitJobId;
      let patients: string[] = [];

      const normalizePatientId = (value: unknown): string =>
        typeof value === 'string' ? value.trim() : '';

      if (resolvedJobId) {
        const payload = await ingestionApiRequest(
          `/jobs/${encodeURIComponent(resolvedJobId)}/patients?persisted_only=true&bundleable_only=true`,
        ) as { patients?: string[]; patient_details?: DbPatientDetail[] };
        patients = Array.isArray(payload.patients)
          ? payload.patients.map((item) => normalizePatientId(item)).filter((item) => item.length > 0)
          : [];
        const details = Array.isArray(payload.patient_details) ? payload.patient_details : [];
        details.forEach((item) => {
          const normalized = normalizePatientId(item.patient_id);
          if (normalized) {
            nextPatientDetailsById[normalized] = { ...item, patient_id: normalized };
          }
        });
        patients.forEach((patientId) => {
          nextPatientJobMap[patientId] = resolvedJobId;
          nextPatientJobCandidates[patientId] = [resolvedJobId];
        });
      } else {
        setDbSourceMessage('Looking up available patients from database...');
        const jobsPayload = await ingestionApiRequest('/jobs') as { jobs?: Array<{ id?: string }> };
        const jobs = Array.isArray(jobsPayload.jobs) ? jobsPayload.jobs : [];
        const jobIds = jobs
          .map((job) => (typeof job.id === 'string' ? job.id.trim() : ''))
          .filter((jobId) => jobId.length > 0)
          .slice(0, DB_PATIENT_SCAN_LIMIT);

        setDbSourceMessage(
          `Scanning recent database jobs for patients (${jobIds.length} job${jobIds.length === 1 ? '' : 's'})...`,
        );

        const patientSet = new Set<string>();
        const orderedPatients: string[] = [];
        let jobsWithPatients = 0;

        const jobPatientPayloads = await Promise.all(
          jobIds.map(async (candidateJobId) => {
            const payload = await ingestionApiRequest(
              `/jobs/${encodeURIComponent(candidateJobId)}/patients?persisted_only=true&bundleable_only=true`,
            ) as { patients?: string[]; patient_details?: DbPatientDetail[] };
            return { candidateJobId, payload };
          }),
        );

        for (const { candidateJobId, payload } of jobPatientPayloads) {
          const candidatePatients = Array.isArray(payload.patients)
            ? payload.patients.map((item) => normalizePatientId(item)).filter((item) => item.length > 0)
            : [];
          const details = Array.isArray(payload.patient_details) ? payload.patient_details : [];

          details.forEach((item) => {
            const normalized = normalizePatientId(item.patient_id);
            if (!normalized || nextPatientDetailsById[normalized]) {
              return;
            }
            nextPatientDetailsById[normalized] = { ...item, patient_id: normalized };
          });

          if (candidatePatients.length === 0) {
            continue;
          }

          jobsWithPatients += 1;
          if (!resolvedJobId) {
            resolvedJobId = candidateJobId;
          }

          candidatePatients.forEach((patientId) => {
            if (!patientSet.has(patientId)) {
              patientSet.add(patientId);
              orderedPatients.push(patientId);
            }
            if (!nextPatientJobMap[patientId]) {
              nextPatientJobMap[patientId] = candidateJobId;
            }

            if (!nextPatientJobCandidates[patientId]) {
              nextPatientJobCandidates[patientId] = [];
            }
            if (!nextPatientJobCandidates[patientId].includes(candidateJobId)) {
              nextPatientJobCandidates[patientId].push(candidateJobId);
            }
          });
        }

        patients = orderedPatients;

        if (patients.length > 0) {
          setDbSourceMessage(`Loaded ${patients.length} unique patients across ${jobsWithPatients} job(s).`);
        } else if (jobIds.length > 0) {
          setDbSourceMessage(`No patients found in the most recent ${jobIds.length} job(s).`);
        }
      }

      if (!resolvedJobId) {
        setDbSourcePatients([]);
        setDbSelectedPatients([]);
        setDbPatientJobMap({});
        setDbPatientJobCandidates({});
        setDbPatientDetailsById({});
        setDbSourceMessage('No ingestion jobs found in database.');
        return;
      }

      setDbSourceJobId(resolvedJobId);
      setDbSourcePatients(patients);
      setDbPatientJobMap(nextPatientJobMap);
      setDbPatientJobCandidates(nextPatientJobCandidates);
      setDbPatientDetailsById(nextPatientDetailsById);
      setDbSelectedPatients([]);
      setDbVisiblePatientCount(DB_PATIENT_PAGE_SIZE);

      if (patients.length === 0) {
        setDbSourceMessage(`No patients found for job ${resolvedJobId}.`);
      } else if (explicitJobId) {
        setDbSourceMessage(`Loaded ${patients.length} patients from database (job ${resolvedJobId}).`);
      }
    } catch (error) {
      setDbSourceError(toErrorMessage(error));
    } finally {
      setDbSourceBusy(false);
    }
  }

  useEffect(() => {
    void loadDbPatientsForEvaluation();
  }, []);

  const ingestionRuntimeStatus =
    ingestionJobSummary?.runtime?.status ?? ingestionJobSummary?.status ?? 'not_started';

  const ingestionFlowSteps = [
    {
      key: 'upload',
      title: 'Upload CSV',
      completed: Boolean(ingestionInputDir),
    },
    {
      key: 'inspect',
      title: 'Inspect Dataset',
      completed: Boolean(ingestionDatasetMeta?.row_counts),
    },
    {
      key: 'normalize',
      title: 'Normalize to FHIR',
      completed:
        ingestionRuntimeStatus === 'completed' ||
        ingestionRuntimeStatus === 'partially_completed',
      active:
        ingestionRuntimeStatus === 'running' ||
        ingestionRuntimeStatus === 'pending' ||
        ingestionRuntimeStatus === 'started',
    },
    {
      key: 'persist',
      title: 'Persist Resources',
      completed: (ingestionJobSummary?.resources_persisted ?? 0) > 0,
    },
    {
      key: 'patients',
      title: 'Load Patients',
      completed: ingestionPatients.length > 0,
    },
    {
      key: 'download',
      title: 'Download FHIR',
      completed: Boolean(allPatientsFhir),
    },
  ];

  const ingestionCompletionPercent = Math.round(
    (ingestionFlowSteps.filter((step) => step.completed).length / ingestionFlowSteps.length) * 100,
  );

  const canRunInMode = config.mode === 'real';
  const isRunDisabled =
    !canRunInMode ||
    (patientBundleFiles.length === 0 && dbSelectedPatients.length === 0) ||
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
    }).filter((row) => row.outcome !== 'not_in_population');
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
      <header className="app-header-bar" aria-label="Application header">
        <div className="app-header-inner">
          <div className="app-header-brand">
            <span className="app-header-dot" aria-hidden="true" />
            <strong>HEDIS Ops Console</strong>
          </div>
          <div className="app-header-right">
            <span className="app-header-chip">POC - SYNTHETIC DATA ONLY</span>
          </div>
        </div>
      </header>
      <section className="engine-shell">
        <aside className="engine-sidebar" aria-label="Workspace navigation">
          <nav className="sidebar-nav" aria-label="Engine sections">
            <button type="button" className="sidebar-tab">
              <LayoutGrid size={16} aria-hidden="true" />
              <span>Dashboard</span>
            </button>
            <button type="button" className="sidebar-tab">
              <ArrowDownToLine size={16} aria-hidden="true" />
              <span>Run Ingestion</span>
            </button>
            <button type="button" className="sidebar-tab sidebar-tab-active" aria-current="page">
              <CirclePlay size={16} aria-hidden="true" />
              <span>Run Gap Calculation</span>
            </button>
            <button type="button" className="sidebar-tab">
              <List size={16} aria-hidden="true" />
              <span>Prioritization</span>
            </button>
            <button type="button" className="sidebar-tab">
              <FileSearch size={16} aria-hidden="true" />
              <span>Evidence Review</span>
            </button>
          </nav>
        </aside>

        <section className="engine-content">
          <header className="engine-header">
            <div>
              <h1>Run Gap Calculation</h1>
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
                  ) : step.state === 'current' && step.key === 'run' && isEvaluationRunning ? (
                    <LoaderCircle size={16} className="rotating" />
                  ) : step.state === 'current' ? (
                    <CircleDot size={16} />
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
              <span>Execution policy:</span>
              <strong>Standard engine-verified policy</strong>
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

          <section className="summary-block" aria-label="Database patient source">
            <h3>Use Existing Database Patients</h3>
            <p className="field-helper">
              Patient list is loaded automatically from the latest available database job. Select patients here, then run using Run FQM Evaluation.
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  void loadDbPatientsForEvaluation();
                }}
                disabled={dbSourceBusy}
              >
                Refresh Patients
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={toggleSelectAllFilteredDbPatients}
                disabled={dbSourceBusy || filteredDbPatients.length === 0}
              >
                Select / Unselect Filtered
              </button>
            </div>
            <div className="form-field">
              <label htmlFor="dbPatientSearch">Search patient id</label>
              <input
                id="dbPatientSearch"
                type="text"
                value={dbSourceSearch}
                onChange={(event) => setDbSourceSearch(event.target.value)}
                placeholder="Type patient id text"
              />
            </div>
            <div className="summary-block" aria-label="Database patient picker">
              <div className="workflow-panel-header">
                <h3>Patient dropdown table</h3>
                <p>
                  {dbSelectedPatients.length} selected / {dbSourcePatients.length} total loaded
                </p>
              </div>
              {hasDbSearchQuery ? (
                <p className="field-helper">Search matched {filteredDbPatients.length} patient(s).</p>
              ) : null}

              <div
                style={{ maxHeight: '320px', overflowY: 'auto', border: '1px solid #d1d5db', borderRadius: '10px' }}
                onScroll={onDbPatientsScroll}
              >
                {filteredDbPatients.length > 0 ? (
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: 'left', padding: '8px', borderBottom: '1px solid #e2e8f0', width: '72px' }}>Select</th>
                        <th style={{ textAlign: 'left', padding: '8px', borderBottom: '1px solid #e2e8f0' }}>Patient ID</th>
                        <th style={{ textAlign: 'left', padding: '8px', borderBottom: '1px solid #e2e8f0', width: '140px' }}>File Size</th>
                        <th style={{ textAlign: 'left', padding: '8px', borderBottom: '1px solid #e2e8f0', width: '220px' }}>Saved In DB</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleDbPatients.map((patientId) => {
                        const detail = dbPatientDetailsById[patientId];
                        return (
                        <tr key={patientId}>
                          <td style={{ padding: '8px', borderBottom: '1px solid #f1f5f9' }}>
                            <input
                              type="checkbox"
                              checked={dbSelectedPatients.includes(patientId)}
                              onChange={() => toggleDbPatientSelection(patientId)}
                              aria-label={`Select patient ${patientId}`}
                            />
                          </td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #f1f5f9' }}>
                            <span className="mono-inline">{patientId}</span>
                          </td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #f1f5f9', fontSize: '12px' }}>
                            {formatBytes(detail?.estimated_size_bytes)}
                          </td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #f1f5f9', fontSize: '12px' }}>
                            {formatSavedAt(detail?.saved_at)}
                          </td>
                        </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <p className="field-helper" style={{ margin: '12px' }}>
                    {dbSourceBusy
                      ? 'Loading patients from database...'
                      : 'No patients available in the database yet.'}
                  </p>
                )}
              </div>

            </div>
            {dbSourceMessage ? <p className="field-helper">{dbSourceMessage}</p> : null}
            {dbSourceError ? <p className="field-error">{dbSourceError}</p> : null}
          </section>

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
              <span>Measure strategy:</span>
              <strong>All 8 active measures evaluated, related measures inferred from results</strong>
            </p>
            <p>
              <span>Patient bundles:</span>
              <strong>{patientBundleFiles.length + dbSelectedPatients.length}</strong>
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

          <section className="panel form-layout" aria-label="Data ingestion workflow">
          <h2>Data Ingestion (Synthea CSV -&gt; FHIR)</h2>
          <p className="field-helper">
            Upload CSV files, normalize to FHIR, review patient bundles, and download normalized output.
          </p>

          <section className="selection-summary" aria-label="Dataset details">
            <p>
              <span>Dataset source:</span>
              <strong>{ingestionInputDir ? 'Uploaded CSV files' : 'Not uploaded yet'}</strong>
            </p>
            <p>
              <span>Current job ID:</span>
              <strong>{ingestionJobId || 'Not started'}</strong>
            </p>
            <p>
              <span>Rows discovered:</span>
              <strong>{ingestionDatasetMeta?.total_rows ?? 0}</strong>
            </p>
            <p>
              <span>Patients normalized:</span>
              <strong>{ingestionPatients.length}</strong>
            </p>
          </section>

          <FileInputField
            key={`ingestion-${resetToken}`}
            id="ingestionFiles"
            label="Or upload Synthea CSV files"
            title="Upload Synthea CSV files"
            description="Upload all required Synthea CSV files. The app uses the returned temporary dataset automatically."
            accept=".csv,text/csv"
            files={ingestionFiles}
            browseLabel="Browse CSV files"
            multiple
            onChange={(files) => setIngestionFiles(Array.from(files ?? []))}
            onRemoveFile={removeIngestionFile}
          />

          <div className="form-actions">
            <button type="button" className="secondary-button" onClick={uploadIngestionCsvFiles} disabled={ingestionBusy}>
              Upload CSV Files
            </button>
            <button type="button" className="secondary-button" onClick={inspectIngestionDataset} disabled={ingestionBusy}>
              Inspect Dataset
            </button>
            <button type="button" className="primary-button" onClick={startIngestionJob} disabled={ingestionBusy}>
              Start Ingestion
            </button>
            <button type="button" className="secondary-button" onClick={refreshIngestionJob} disabled={ingestionBusy}>
              Refresh Status
            </button>
            <button type="button" className="secondary-button" onClick={persistIngestionResources} disabled={ingestionBusy}>
              Persist Resources
            </button>
            <button type="button" className="secondary-button" onClick={loadIngestionValidation} disabled={ingestionBusy}>
              Load Validation
            </button>
            <button type="button" className="secondary-button" onClick={loadIngestionPatients} disabled={ingestionBusy}>
              Load Patients
            </button>
            <button type="button" className="secondary-button" onClick={downloadAllPatientsFhir} disabled={ingestionBusy || !ingestionJobId.trim()}>
              Download All FHIR
            </button>
          </div>

          {ingestionStatusMessage ? <p className="field-helper">{ingestionStatusMessage}</p> : null}
          {ingestionErrorMessage ? <p className="field-error">{ingestionErrorMessage}</p> : null}

          <section className="summary-block" aria-label="Ingestion flow progress">
            <div className="workflow-panel-header">
              <h3>Ingestion flow progress</h3>
              <p>{ingestionCompletionPercent}% complete</p>
            </div>
            <div className="workflow-progress-track" aria-hidden="true">
              <span style={{ width: `${ingestionCompletionPercent}%` }} />
            </div>
            <div className="upload-process-steps">
              {ingestionFlowSteps.map((step) => (
                <span
                  key={step.key}
                  className={`upload-step-chip ${step.completed || step.active ? 'active' : ''}`}
                >
                  {step.title}
                </span>
              ))}
            </div>
            <p className="field-helper">
              Live status: <strong>{ingestionRuntimeStatus}</strong>
              {ingestionJobQuery.isFetching ? ' (auto-refreshing...)' : ''}
            </p>
          </section>

          {ingestionDatasetMeta?.row_counts ? (
            <section>
              <h3>Dataset file summary</h3>
              <div className="table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>CSV File</th>
                      <th>Rows</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(ingestionDatasetMeta.row_counts).map(([fileName, rowCount]) => (
                      <tr key={fileName}>
                        <td>{fileName}</td>
                        <td>{rowCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {Array.isArray(ingestionDatasetMeta.missing_required_files) && ingestionDatasetMeta.missing_required_files.length > 0 ? (
                <p className="field-error">
                  Missing required files: {ingestionDatasetMeta.missing_required_files.join(', ')}
                </p>
              ) : null}
            </section>
          ) : null}

          {ingestionJobSummary ? (
            <section>
              <h3>Ingestion job status</h3>
              <div className="summary-grid">
                <article className="summary-card">
                  <p>Job Status</p>
                  <strong>{ingestionJobSummary.runtime?.status ?? ingestionJobSummary.status ?? 'unknown'}</strong>
                </article>
                <article className="summary-card">
                  <p>Generated Resources</p>
                  <strong>{ingestionJobSummary.resources_generated ?? 0}</strong>
                </article>
                <article className="summary-card">
                  <p>Persisted Resources</p>
                  <strong>{ingestionJobSummary.resources_persisted ?? 0}</strong>
                </article>
                <article className="summary-card">
                  <p>Warnings / Errors</p>
                  <strong>{`${ingestionJobSummary.warning_count ?? 0} / ${ingestionJobSummary.error_count ?? 0}`}</strong>
                </article>
              </div>
            </section>
          ) : null}

          {Array.isArray(ingestionJobSummary?.runtime?.stages) && ingestionJobSummary.runtime.stages.length > 0 ? (
            <section>
              <h3>Pipeline stage progress</h3>
              <div className="table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Stage</th>
                      <th>Status</th>
                      <th>Details</th>
                      <th>Elapsed (s)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ingestionJobSummary.runtime.stages.map((stage) => (
                      <tr key={stage.name}>
                        <td>{stage.name}</td>
                        <td>
                          <span className={`outcome-pill tone-${toBadgeTone(stage.status)}`}>
                            {stage.status}
                          </span>
                        </td>
                        <td>{stage.details ?? 'n/a'}</td>
                        <td>{typeof stage.elapsed_seconds === 'number' ? stage.elapsed_seconds.toFixed(2) : 'n/a'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          <section>
            <h3>Validation issues</h3>
            {ingestionValidationIssues.length === 0 ? (
              <p className="field-helper">No validation issues loaded yet.</p>
            ) : (
              <div className="table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Severity</th>
                      <th>Category</th>
                      <th>Source File</th>
                      <th>Source Record</th>
                      <th>Diagnostic</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ingestionValidationIssues.map((issue) => (
                      <tr key={issue.id}>
                        <td>{issue.severity}</td>
                        <td>{issue.issue_category}</td>
                        <td>{issue.source_file ?? 'n/a'}</td>
                        <td>{issue.source_record_id ?? 'n/a'}</td>
                        <td>{issue.diagnostic}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section>
            <h3>Live activity feed</h3>
            {ingestionLog.length === 0 ? (
              <p className="field-helper">No live events yet. Start ingestion to see streamed updates.</p>
            ) : (
              <div className="table-wrap">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Event</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ingestionLog.map((entry, index) => (
                      <tr key={`${entry.at}-${index}`}>
                        <td>{entry.at}</td>
                        <td>{entry.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {ingestionPatients.length > 0 ? (
            <section className="form-field" aria-label="Patient FHIR output">
              <label htmlFor="ingestionPatientSelect">Patient FHIR preview</label>
              <select
                id="ingestionPatientSelect"
                value={selectedIngestionPatient}
                onChange={(event) => {
                  const patientId = event.target.value;
                  setSelectedIngestionPatient(patientId);
                  void loadPatientFhir(patientId);
                }}
              >
                {ingestionPatients.map((patientId) => (
                  <option key={patientId} value={patientId}>
                    {patientId}
                  </option>
                ))}
              </select>
              <div className="form-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void loadPatientFhir(selectedIngestionPatient)}
                  disabled={ingestionBusy || !selectedIngestionPatient}
                >
                  Load Selected Patient FHIR
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={downloadSelectedPatientFhir}
                  disabled={ingestionBusy || !selectedIngestionPatient}
                >
                  Download Selected FHIR
                </button>
              </div>
              <details>
                <summary className="field-helper">View selected patient bundle JSON</summary>
                <pre>{selectedPatientFhir || 'No patient bundle loaded yet.'}</pre>
              </details>
            </section>
          ) : null}

          {allPatientsFhir ? (
            <section className="selection-summary" aria-label="All patient export summary">
              <p>
                <span>All-patient export job:</span>
                <strong>{allPatientsFhir.jobId}</strong>
              </p>
              <p>
                <span>Patients exported:</span>
                <strong>{allPatientsFhir.patientCount}</strong>
              </p>
              <p>
                <span>Exported at:</span>
                <strong>{new Date(allPatientsFhir.exportedAt).toLocaleString()}</strong>
              </p>
            </section>
          ) : null}
          </section>

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
                    In All 8 mode, each row represents one measure evaluation for the same uploaded patient.
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
      </section>
    </main>
  );
}
