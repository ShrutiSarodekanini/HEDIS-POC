import { envConfig } from "../config/env"
import type {
  IngestionDatasetMeta,
  IngestionEndpointMode,
  IngestionFlowResponse,
  IngestionJob,
  IngestionJobsResponse,
  IngestionPatientResourcesResponse,
  IngestionPatientsResponse,
  IngestionPersistResponse,
  IngestionStartResponse,
  IngestionValidationResponse,
} from "../models/ingestion"
import { fetchJson, postForm, postJson } from "./http"
import { getOpenApiPaths, hasOpenApiPath } from "./openapi"

const timeoutMs = envConfig.apiTimeoutMs

const listJobsInFlight = new Map<string, Promise<IngestionJobsResponse>>()
const jobInFlight = new Map<string, Promise<IngestionJob>>()
const flowInFlight = new Map<string, Promise<IngestionFlowResponse>>()

const withPrefix = (mode: IngestionEndpointMode, suffix: string): string => {
  const normalized = suffix.startsWith("/") ? suffix : `/${suffix}`
  return `${mode.prefix}${normalized}`
}

export async function discoverIngestionMode(): Promise<IngestionEndpointMode> {
  const paths = await getOpenApiPaths(timeoutMs)
  const flowPath = hasOpenApiPath(paths, "/api/flow") ? "/api/flow" : null

  if (hasOpenApiPath(paths, "/api/ingestion/jobs") && hasOpenApiPath(paths, "/api/ingestion/jobs/{job_id}")) {
    return { prefix: "/api/ingestion", flowPath }
  }

  if (hasOpenApiPath(paths, "/api/jobs") && hasOpenApiPath(paths, "/api/jobs/{job_id}")) {
    return { prefix: "/api", flowPath }
  }

  throw new Error("Ingestion endpoints are not available in this backend profile.")
}

export async function listIngestionJobs(mode: IngestionEndpointMode): Promise<IngestionJob[]> {
  const key = mode.prefix
  const existing = listJobsInFlight.get(key)
  const request = existing
    ?? fetchJson<IngestionJobsResponse>(withPrefix(mode, "/jobs"), timeoutMs)
      .finally(() => {
        listJobsInFlight.delete(key)
      })

  if (!existing) {
    listJobsInFlight.set(key, request)
  }

  const response = await request
  return response.jobs ?? []
}

export async function getIngestionJob(mode: IngestionEndpointMode, jobId: string): Promise<IngestionJob> {
  const key = `${mode.prefix}:${jobId}`
  const existing = jobInFlight.get(key)
  const request = existing
    ?? fetchJson<IngestionJob>(withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}`), timeoutMs)
      .finally(() => {
        jobInFlight.delete(key)
      })

  if (!existing) {
    jobInFlight.set(key, request)
  }

  return request
}

export async function inspectIngestionDataset(
  mode: IngestionEndpointMode,
  inputDir: string,
): Promise<IngestionDatasetMeta> {
  return fetchJson<IngestionDatasetMeta>(
    withPrefix(mode, `/datasets/inspect?input_dir=${encodeURIComponent(inputDir)}`),
    timeoutMs,
  )
}

export async function uploadIngestionFiles(
  mode: IngestionEndpointMode,
  files: File[],
): Promise<IngestionDatasetMeta> {
  const formData = new FormData()
  const zipFiles = files.filter((file) => /\.zip$/i.test(file.name))
  const csvFiles = files.filter((file) => /\.csv$/i.test(file.name))
  const supportedCount = zipFiles.length + csvFiles.length

  if (supportedCount !== files.length) {
    throw new Error("Only .zip and .csv files can be uploaded.")
  }

  if (zipFiles.length > 0 && csvFiles.length > 0) {
    throw new Error("Choose either one .zip file or several .csv files, not both.")
  }

  if (zipFiles.length > 1) {
    throw new Error("Choose only one .zip file.")
  }

  let endpoint: string
  if (zipFiles.length === 1) {
    endpoint = "/datasets/upload-zip"
    formData.append("file", zipFiles[0])
  } else {
    endpoint = "/datasets/upload"
    for (const file of files) {
      formData.append("files", file)
    }
  }

  return postForm<IngestionDatasetMeta>(withPrefix(mode, endpoint), timeoutMs, formData)
}

export async function startIngestionJob(
  mode: IngestionEndpointMode,
  inputDir: string,
): Promise<IngestionStartResponse> {
  const url = withPrefix(mode, `/jobs/start?input_dir=${encodeURIComponent(inputDir)}`)
  return postJson<IngestionStartResponse>(url, timeoutMs, {})
}

export async function persistIngestionJob(
  mode: IngestionEndpointMode,
  jobId: string,
): Promise<IngestionPersistResponse> {
  return postJson<IngestionPersistResponse>(
    withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}/persist`),
    timeoutMs,
    {},
  )
}

export async function loadIngestionValidation(
  mode: IngestionEndpointMode,
  jobId: string,
): Promise<IngestionValidationResponse> {
  return fetchJson<IngestionValidationResponse>(
    withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}/validation`),
    timeoutMs,
  )
}

export async function loadIngestionPatients(
  mode: IngestionEndpointMode,
  jobId: string,
): Promise<IngestionPatientsResponse> {
  return fetchJson<IngestionPatientsResponse>(
    withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}/patients?persisted_only=false&bundleable_only=true`),
    timeoutMs,
  )
}

export async function loadIngestionPatientResources(
  mode: IngestionEndpointMode,
  jobId: string,
  patientId: string,
): Promise<IngestionPatientResourcesResponse> {
  return fetchJson<IngestionPatientResourcesResponse>(
    withPrefix(mode, `/jobs/${encodeURIComponent(jobId)}/patients/${encodeURIComponent(patientId)}/resources`),
    timeoutMs,
  )
}

export async function loadIngestionFlow(mode: IngestionEndpointMode): Promise<IngestionFlowResponse> {
  if (!mode.flowPath) {
    throw new Error("Our-steps flow endpoint is not available in this backend profile.")
  }

  const key = mode.flowPath
  const existing = flowInFlight.get(key)
  const request = existing
    ?? fetchJson<IngestionFlowResponse>(mode.flowPath, timeoutMs)
      .finally(() => {
        flowInFlight.delete(key)
      })

  if (!existing) {
    flowInFlight.set(key, request)
  }

  return request
}
