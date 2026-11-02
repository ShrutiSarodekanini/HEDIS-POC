import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
  IngestionDatasetMeta,
  IngestionEndpointMode,
  IngestionFlowBatch,
  IngestionJob,
  IngestionPatientDetail,
  IngestionResource,
  IngestionValidationIssue,
} from "../models/ingestion"
import {
  discoverIngestionMode,
  getIngestionJob,
  inspectIngestionDataset,
  listIngestionJobs,
  loadIngestionFlow,
  loadIngestionPatientResources,
  loadIngestionPatients,
  loadIngestionValidation,
  persistIngestionJob,
  startIngestionJob,
  uploadIngestionFiles,
} from "../services/ingestion.service"
import { toRequestErrorMessage } from "../services/http"

const ACTIVE_JOB_STATUSES = new Set(["created", "starting", "running", "pending", "queued"])

type SectionKey = "setup" | "upload" | "job" | "validation" | "patients" | "history" | "ourSteps"

export interface SectionMessage {
  text: string
  error: boolean
}

export type SectionMessageMap = Record<SectionKey, SectionMessage>

const defaultMessages = (): SectionMessageMap => ({
  setup: { text: "", error: false },
  upload: { text: "", error: false },
  job: { text: "", error: false },
  validation: { text: "", error: false },
  patients: { text: "", error: false },
  history: { text: "", error: false },
  ourSteps: { text: "", error: false },
})

const sortJobs = (jobs: IngestionJob[]): IngestionJob[] =>
  jobs
    .slice()
    .sort((left, right) => String(right.started_at ?? "").localeCompare(String(left.started_at ?? "")))

const effectiveStatus = (job: IngestionJob | null): string => {
  if (!job) {
    return ""
  }

  const runtimeStatus = job.runtime?.status
  if (typeof runtimeStatus === "string" && runtimeStatus.trim().length > 0) {
    return runtimeStatus
  }

  return job.status ?? ""
}

const asBundle = (
  jobId: string,
  patientId: string,
  resources: IngestionResource[],
): Record<string, unknown> => ({
  job_id: jobId,
  patient_id: patientId,
  bundle: {
    resourceType: "Bundle",
    type: "collection",
    entry: resources
      .map((resource) => resource.resource_json)
      .filter((resource): resource is Record<string, unknown> => Boolean(resource))
      .map((resource) => ({ resource })),
  },
})

const saveJsonFile = (fileName: string, payload: unknown): void => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = fileName
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function useIngestionWorkflow() {
  const [mode, setMode] = useState<IngestionEndpointMode | null>(null)
  const [setupLoading, setSetupLoading] = useState<boolean>(true)
  const [setupError, setSetupError] = useState<string | null>(null)

  const [inputDir, setInputDir] = useState<string>("")
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])

  const [datasetMeta, setDatasetMeta] = useState<IngestionDatasetMeta | null>(null)
  const [history, setHistory] = useState<IngestionJob[]>([])
  const [jobId, setJobId] = useState<string>("")
  const [job, setJob] = useState<IngestionJob | null>(null)

  const [validationIssues, setValidationIssues] = useState<IngestionValidationIssue[]>([])
  const [patients, setPatients] = useState<IngestionPatientDetail[]>([])
  const [selectedPatientId, setSelectedPatientId] = useState<string>("")
  const [patientResources, setPatientResources] = useState<IngestionResource[]>([])
  const [ourStepsBatch, setOurStepsBatch] = useState<IngestionFlowBatch | null>(null)
  const [ourStepsTasks, setOurStepsTasks] = useState<Record<string, string>>({})

  const [messages, setMessages] = useState<SectionMessageMap>(defaultMessages)

  const [busy, setBusy] = useState<Record<string, boolean>>({
    upload: false,
    inspect: false,
    start: false,
    refresh: false,
    persist: false,
    validation: false,
    patients: false,
    patient: false,
    history: false,
    ourSteps: false,
    exportPatient: false,
    exportAll: false,
  })

  const pollFailRef = useRef<number>(0)
  const lastPolledStatusRef = useRef<string>("")

  const setMessage = useCallback((section: SectionKey, text: string, error = false): void => {
    setMessages((previous) => ({
      ...previous,
      [section]: { text, error },
    }))
  }, [])

  const setBusyFlag = useCallback((key: string, value: boolean): void => {
    setBusy((previous) => ({
      ...previous,
      [key]: value,
    }))
  }, [])

  const reloadHistory = useCallback(
    async (activeMode: IngestionEndpointMode): Promise<IngestionJob[]> => {
      const jobs = sortJobs(await listIngestionJobs(activeMode))
      setHistory(jobs)
      return jobs
    },
    [],
  )

  const reloadOurSteps = useCallback(
    async (activeMode: IngestionEndpointMode): Promise<void> => {
      if (!activeMode.flowPath) {
        setOurStepsBatch(null)
        setOurStepsTasks({})
        return
      }

      const flow = await loadIngestionFlow(activeMode)
      setOurStepsBatch(flow.batch ?? null)
      setOurStepsTasks(flow.tasks ?? {})
    },
    [],
  )

  const loadJobById = useCallback(
    async (activeMode: IngestionEndpointMode, activeJobId: string): Promise<IngestionJob> => {
      const nextJob = await getIngestionJob(activeMode, activeJobId)
      setJob(nextJob)
      setInputDir((previous) => (previous.length > 0 ? previous : nextJob.input_dir ?? ""))
      return nextJob
    },
    [],
  )

  const withGuard = useCallback(
    async (
      section: SectionKey,
      loadingKey: string,
      action: () => Promise<void>,
      loadingMessage?: string,
    ): Promise<void> => {
      setBusyFlag(loadingKey, true)
      if (loadingMessage) {
        setMessage(section, loadingMessage)
      } else {
        setMessage(section, "")
      }

      try {
        await action()
      } catch (error) {
        setMessage(section, toRequestErrorMessage(error), true)
      } finally {
        setBusyFlag(loadingKey, false)
      }
    },
    [setBusyFlag, setMessage],
  )

  const initialize = useCallback(async (): Promise<void> => {
    setSetupLoading(true)
    setSetupError(null)

    try {
      const discovered = await discoverIngestionMode()
      setMode(discovered)

      const [jobs] = await Promise.all([
        reloadHistory(discovered),
        reloadOurSteps(discovered),
      ])
      if (jobs.length > 0) {
        const latest = jobs[0]
        setJobId(latest.id)
        await loadJobById(discovered, latest.id)
      }
    } catch (error) {
      setSetupError(toRequestErrorMessage(error))
      setMode(null)
    } finally {
      setSetupLoading(false)
    }
  }, [loadJobById, reloadHistory, reloadOurSteps])

  useEffect(() => {
    void initialize()
  }, [initialize])

  const inspectDataset = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("upload", "Ingestion endpoints are unavailable.", true)
      return
    }

    const trimmed = inputDir.trim()
    if (!trimmed) {
      setMessage("upload", "Enter an input folder path or upload files first.", true)
      return
    }

    await withGuard("upload", "inspect", async () => {
      const meta = await inspectIngestionDataset(mode, trimmed)
      setDatasetMeta(meta)
      setInputDir(meta.input_dir)
      setMessage("upload", `Inspected ${meta.total_rows.toLocaleString()} rows.`)
    }, "Inspecting dataset…")
  }, [inputDir, mode, setMessage, withGuard])

  const uploadFiles = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("upload", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (selectedFiles.length === 0) {
      setMessage("upload", "Choose one .zip file or several .csv files first.", true)
      return
    }

    await withGuard("upload", "upload", async () => {
      const meta = await uploadIngestionFiles(mode, selectedFiles)
      setDatasetMeta(meta)
      setInputDir(meta.input_dir)
      setMessage(
        "upload",
        `Uploaded ${(meta.saved_files ?? []).length.toLocaleString()} supported files and found ${meta.total_rows.toLocaleString()} rows.`,
      )
    }, "Uploading files…")
  }, [mode, selectedFiles, setMessage, withGuard])

  const refreshJob = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("job", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId) {
      setMessage("job", "Start a run or select a job from history first.", true)
      return
    }

    await withGuard("job", "refresh", async () => {
      await Promise.all([
        loadJobById(mode, jobId),
        reloadHistory(mode),
      ])
      setMessage("job", "Job refreshed.")
    }, "Refreshing job status…")
  }, [jobId, loadJobById, mode, reloadHistory, setMessage, withGuard])

  const selectJob = useCallback(
    async (nextJobId: string): Promise<void> => {
      if (!mode) {
        setMessage("job", "Ingestion endpoints are unavailable.", true)
        return
      }

      if (!nextJobId) {
        setJobId("")
        setJob(null)
        return
      }

      setJobId(nextJobId)
      await withGuard("job", "refresh", async () => {
        await Promise.all([
          loadJobById(mode, nextJobId),
          reloadOurSteps(mode),
        ])
      }, "Loading selected job…")
    },
    [loadJobById, mode, reloadOurSteps, setMessage, withGuard],
  )

  const startJob = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("job", "Ingestion endpoints are unavailable.", true)
      return
    }

    const trimmed = inputDir.trim()
    if (!trimmed) {
      setMessage("job", "Enter an input folder path or upload files first.", true)
      return
    }

    await withGuard("job", "start", async () => {
      const inspected = await inspectIngestionDataset(mode, trimmed)
      setDatasetMeta(inspected)
      if ((inspected.missing_required_files ?? []).length > 0) {
        throw new Error(`Cannot start: missing required files ${inspected.missing_required_files.join(", ")}.`)
      }

      const started = await startIngestionJob(mode, trimmed)
      setJobId(started.job_id)
      setValidationIssues([])
      setPatients([])
      setSelectedPatientId("")
      setPatientResources([])
      await Promise.all([
        loadJobById(mode, started.job_id),
        reloadHistory(mode),
        reloadOurSteps(mode),
      ])

      const clearedJobs = started.legacy_jobs_cleared ?? 0
      if (clearedJobs > 0) {
        setMessage("job", `Started. Cleared ${clearedJobs.toLocaleString()} older jobs for this dataset.`)
      } else {
        setMessage("job", "Ingestion started.")
      }
    }, "Starting ingestion…")
  }, [inputDir, loadJobById, mode, reloadHistory, reloadOurSteps, setMessage, withGuard])

  const persistJob = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("job", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId) {
      setMessage("job", "Start a run or select a job from history first.", true)
      return
    }

    await withGuard("job", "persist", async () => {
      const result = await persistIngestionJob(mode, jobId)
      await Promise.all([
        loadJobById(mode, jobId),
        reloadHistory(mode),
        reloadOurSteps(mode),
      ])
      setMessage("job", `Persisted ${result.resources_persisted.toLocaleString()} resources.`)
    }, "Persisting resources…")
  }, [jobId, loadJobById, mode, reloadHistory, reloadOurSteps, setMessage, withGuard])

  const loadValidation = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("validation", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId) {
      setMessage("validation", "Start a run or select a job first.", true)
      return
    }

    await withGuard("validation", "validation", async () => {
      const response = await loadIngestionValidation(mode, jobId)
      setValidationIssues(response.issues ?? [])
      setMessage("validation", `Loaded ${(response.issues ?? []).length.toLocaleString()} validation issues.`)
    }, "Loading validation issues…")
  }, [jobId, mode, setMessage, withGuard])

  const loadPatients = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("patients", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId) {
      setMessage("patients", "Start a run or select a job first.", true)
      return
    }

    await withGuard("patients", "patients", async () => {
      const response = await loadIngestionPatients(mode, jobId)
      const details = response.patient_details ?? []
      setPatients(details)
      if (!details.some((patient) => patient.patient_id === selectedPatientId)) {
        setSelectedPatientId("")
        setPatientResources([])
      }
      setMessage("patients", `Loaded ${details.length.toLocaleString()} patients.`)
    }, "Loading patient list…")
  }, [jobId, mode, selectedPatientId, setMessage, withGuard])

  const selectPatient = useCallback(
    async (patientId: string): Promise<void> => {
      setSelectedPatientId(patientId)
      setPatientResources([])

      if (!mode) {
        setMessage("patients", "Ingestion endpoints are unavailable.", true)
        return
      }

      if (!jobId || !patientId) {
        return
      }

      await withGuard("patients", "patient", async () => {
        const response = await loadIngestionPatientResources(mode, jobId, patientId)
        setPatientResources(response.resources ?? [])
        setMessage("patients", `Loaded ${(response.resources ?? []).length.toLocaleString()} resources for ${patientId}.`)
      }, "Loading patient resources…")
    },
    [jobId, mode, setMessage, withGuard],
  )

  const downloadSelectedBundle = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("patients", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId || !selectedPatientId) {
      setMessage("patients", "Choose a patient first.", true)
      return
    }

    await withGuard("patients", "exportPatient", async () => {
      let resources = patientResources
      if (resources.length === 0) {
        const response = await loadIngestionPatientResources(mode, jobId, selectedPatientId)
        resources = response.resources ?? []
        setPatientResources(resources)
      }

      const payload = asBundle(jobId, selectedPatientId, resources)
      saveJsonFile(`fhir-${selectedPatientId}.bundle.json`, payload)
      setMessage("patients", "Saved selected patient bundle.")
    }, "Preparing selected bundle…")
  }, [jobId, mode, patientResources, selectedPatientId, setMessage, withGuard])

  const downloadAllBundles = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("patients", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!jobId) {
      setMessage("patients", "Start a run or select a job first.", true)
      return
    }

    await withGuard("patients", "exportAll", async () => {
      let details = patients
      if (details.length === 0) {
        const response = await loadIngestionPatients(mode, jobId)
        details = response.patient_details ?? []
        setPatients(details)
      }

      if (details.length === 0) {
        throw new Error("There are no patients to export for this job yet.")
      }

      const bundles: Array<Record<string, unknown>> = []
      for (const patient of details) {
        const response = await loadIngestionPatientResources(mode, jobId, patient.patient_id)
        bundles.push(asBundle(jobId, patient.patient_id, response.resources ?? []))
      }

      saveJsonFile(`fhir-job-${jobId}-patients.json`, {
        job_id: jobId,
        patient_count: bundles.length,
        exported_at: new Date().toISOString(),
        bundles,
      })

      setMessage("patients", `Saved ${bundles.length.toLocaleString()} patient bundles.`)
    }, "Preparing all bundles…")
  }, [jobId, mode, patients, setMessage, withGuard])

  const reloadHistoryList = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("history", "Ingestion endpoints are unavailable.", true)
      return
    }

    await withGuard("history", "history", async () => {
      await reloadHistory(mode)
      setMessage("history", "History refreshed.")
    }, "Loading history…")
  }, [mode, reloadHistory, setMessage, withGuard])

  const loadOurSteps = useCallback(async (): Promise<void> => {
    if (!mode) {
      setMessage("ourSteps", "Ingestion endpoints are unavailable.", true)
      return
    }

    if (!mode.flowPath) {
      setMessage("ourSteps", "Our-steps flow endpoint is not available in this backend profile.", true)
      return
    }

    await withGuard("ourSteps", "ourSteps", async () => {
      await reloadOurSteps(mode)
      setMessage("ourSteps", "Loaded post-persist workflow status.")
    }, "Loading our steps…")
  }, [mode, reloadOurSteps, setMessage, withGuard])

  useEffect(() => {
    if (!mode || !jobId) {
      return
    }

    const status = effectiveStatus(job)
    lastPolledStatusRef.current = status
    if (!ACTIVE_JOB_STATUSES.has(status.toLowerCase())) {
      return
    }

    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const refreshed = await loadJobById(mode, jobId)
          pollFailRef.current = 0

          const nextStatus = effectiveStatus(refreshed)
          const previousStatus = lastPolledStatusRef.current
          lastPolledStatusRef.current = nextStatus

          if (
            ACTIVE_JOB_STATUSES.has(previousStatus.toLowerCase())
            && !ACTIVE_JOB_STATUSES.has(nextStatus.toLowerCase())
          ) {
            await reloadHistory(mode)
            await reloadOurSteps(mode)
          }
        } catch (error) {
          pollFailRef.current += 1
          if (pollFailRef.current <= 2) {
            setMessage("job", `Auto-refresh issue: ${toRequestErrorMessage(error)}`, true)
          }
        }
      })()
    }, 3000)

    return () => {
      window.clearInterval(timer)
    }
  }, [job, jobId, loadJobById, mode, reloadHistory, reloadOurSteps, setMessage])

  const canPersist = useMemo(() => {
    const status = effectiveStatus(job).toLowerCase()
    return jobId.length > 0 && ["completed", "done", "succeeded", "success"].includes(status)
  }, [job, jobId])

  return {
    mode,
    setupLoading,
    setupError,
    retrySetup: initialize,
    inputDir,
    setInputDir,
    selectedFiles,
    setSelectedFiles,
    datasetMeta,
    history,
    jobId,
    setJobId,
    selectJob,
    job,
    validationIssues,
    patients,
    selectedPatientId,
    patientResources,
    ourStepsBatch,
    ourStepsTasks,
    messages,
    busy,
    canPersist,
    inspectDataset,
    uploadFiles,
    startJob,
    refreshJob,
    persistJob,
    loadValidation,
    loadPatients,
    selectPatient,
    downloadSelectedBundle,
    downloadAllBundles,
    reloadHistoryList,
    loadOurSteps,
  }
}
