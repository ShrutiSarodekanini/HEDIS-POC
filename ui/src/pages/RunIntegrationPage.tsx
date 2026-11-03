import { useMemo } from "react"

import { useIngestionWorkflow } from "../hooks/useIngestionWorkflow"
import { formatDateTime, formatInteger, formatRate, toMeterWidth } from "../utils/format"

const OUR_STEPS = [
  { key: "loading", label: "Load FHIR records" },
  { key: "calculating", label: "Run HEDIS calculation" },
  { key: "prioritizing", label: "Run prioritization" },
]

type StepState = "done" | "running" | "failed" | "pending"

function CardMessage({ text, error }: { text: string; error: boolean }) {
  if (!text) {
    return null
  }

  return <p className={`card-message ${error ? "error" : ""}`}>{text}</p>
}

const asStatusLabel = (value: string | undefined): string => {
  if (!value || value.trim().length === 0) {
    return "-"
  }

  return value.replace(/_/g, " ").replace(/\b\w/g, (part) => part.toUpperCase())
}

const asPercent = (numerator: number | undefined, denominator: number | undefined): number | null => {
  if (
    typeof numerator !== "number"
    || Number.isNaN(numerator)
    || typeof denominator !== "number"
    || Number.isNaN(denominator)
    || denominator <= 0
  ) {
    return null
  }

  return (numerator / denominator) * 100
}

const asKb = (value: number | undefined): string => {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-"
  }

  return `${(value / 1024).toFixed(1)} KB`
}

const normalizeStatusToken = (value: string | undefined): string =>
  String(value ?? "").trim().toLowerCase().replace(/[-\s]/g, "_")

const isPositiveCount = (value: unknown): boolean => Number(value) > 0

const toStepState = (value: string | undefined): StepState => {
  const normalized = normalizeStatusToken(value)
  if (["failed", "error", "errored", "cancelled"].includes(normalized)) {
    return "failed"
  }
  if (["completed", "done", "succeeded", "success", "prioritized"].includes(normalized)) {
    return "done"
  }
  if (["running", "pending", "queued", "started", "in_progress", "processing"].includes(normalized)) {
    return "running"
  }
  return "pending"
}

const stepStateLabel = (state: StepState): string => {
  if (state === "done") {
    return "Completed"
  }
  if (state === "running") {
    return "Running"
  }
  if (state === "failed") {
    return "Failed"
  }
  return "Pending"
}

export function RunIntegrationPage() {
  const {
    mode,
    setupLoading,
    setupError,
    retrySetup,
    setSelectedFiles,
    datasetMeta,
    history,
    jobId,
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
    uploadFiles,
    startJob,
    refreshJob,
    loadValidation,
    loadPatients,
    selectPatient,
    downloadSelectedBundle,
    downloadAllBundles,
    reloadHistoryList,
  } = useIngestionWorkflow()

  const effectiveJobStatus = job?.runtime?.status ?? job?.status

  const resourceProgressRows = useMemo(() => {
    const progress = job?.runtime?.resource_progress ?? {}
    return Object.entries(progress)
      .map(([resourceType, row]) => ({
        resourceType,
        total: row.total ?? 0,
        processed: row.processed ?? 0,
        warnings: row.warnings ?? 0,
        rejected: row.rejected ?? 0,
      }))
      .sort((left, right) => right.total - left.total)
  }, [job?.runtime?.resource_progress])

  const selectedResourceTypeRows = useMemo(() => {
    const grouped: Record<string, { total: number; persisted: number }> = {}
    for (const resource of patientResources) {
      const type = resource.resource_type ?? "Unknown"
      if (!grouped[type]) {
        grouped[type] = { total: 0, persisted: 0 }
      }
      grouped[type].total += 1
      if (resource.persisted) {
        grouped[type].persisted += 1
      }
    }

    return Object.entries(grouped)
      .map(([resourceType, stats]) => ({ resourceType, ...stats }))
      .sort((left, right) => right.total - left.total)
  }, [patientResources])

  const stageRows = job?.runtime?.stages ?? []
  const overallProgress = asPercent(job?.resources_persisted, job?.resources_generated)

  const taskRows = useMemo(
    () => Object.entries(ourStepsTasks)
      .map(([task, status]) => ({
        task,
        status,
        state: toStepState(status),
      }))
      .sort((left, right) => left.task.localeCompare(right.task)),
    [ourStepsTasks],
  )

  const topLevelCounts = useMemo(() => {
    const counts = ourStepsBatch?.counts ?? {}
    return Object.entries(counts).filter(([, value]) => typeof value === "number")
  }, [ourStepsBatch?.counts])

  const selectedBatchMatchesJob = !jobId || !ourStepsBatch?.ingest_job_id || ourStepsBatch.ingest_job_id === jobId
  const datasetRowCounts = useMemo(
    () => Object.entries(datasetMeta?.row_counts ?? {}).sort(([left], [right]) => left.localeCompare(right)),
    [datasetMeta?.row_counts],
  )

  const stepRows = useMemo(() => {
    const statusToken = normalizeStatusToken(ourStepsBatch?.status)
    const stepToken = normalizeStatusToken(ourStepsBatch?.step)
    const counts = (ourStepsBatch?.counts ?? {}) as Record<string, unknown>

    const statusIsFailed = statusToken === "failed"
    const statusIsDone = ["prioritized", "completed", "done", "success", "succeeded"].includes(statusToken)

    const doneByCounts = {
      loading: Boolean(counts.load) || isPositiveCount(counts.persisted),
      calculating: Boolean(counts.measures),
      prioritizing: Boolean(counts.prioritization),
    }

    const activeIndexFromStep = (() => {
      if (["loading", "checking", "waiting", "received", "persisting", "converting"].includes(stepToken)) {
        return 0
      }
      if (["measuring", "calculating", "ready", "recalculating"].includes(stepToken)) {
        return 1
      }
      if (["prioritizing", "done"].includes(stepToken)) {
        return 2
      }
      return -1
    })()

    const countBasedStates: StepState[] = [
      doneByCounts.loading ? "done" : "pending",
      doneByCounts.calculating ? "done" : "pending",
      doneByCounts.prioritizing || statusIsDone ? "done" : "pending",
    ]

    if (!statusIsDone) {
      if (statusIsFailed && activeIndexFromStep >= 0) {
        countBasedStates[activeIndexFromStep] = "failed"
      } else {
        const baseIndex = activeIndexFromStep >= 0
          ? activeIndexFromStep
          : countBasedStates.findIndex((state) => state === "pending")
        if (baseIndex >= 0) {
          const runIndex = countBasedStates[baseIndex] === "done"
            ? countBasedStates.findIndex((state, idx) => idx > baseIndex && state === "pending")
            : baseIndex
          if (runIndex >= 0) {
            countBasedStates[runIndex] = "running"
          }
          for (let idx = 0; idx < baseIndex; idx += 1) {
            if (countBasedStates[idx] === "pending") {
              countBasedStates[idx] = "done"
            }
          }
        }
      }
    }

    return OUR_STEPS.map((step, index) => ({ ...step, state: countBasedStates[index] }))
  }, [ourStepsBatch])

  const completedStepCount = stepRows.filter((step) => step.state === "done").length
  const batchStatusState = toStepState(ourStepsBatch?.status)
  const runningTaskCount = taskRows.length > 0
    ? taskRows.filter((task) => task.state === "running").length
    : (batchStatusState === "running" ? 1 : 0)
  const failedTaskCount = taskRows.length > 0
    ? taskRows.filter((task) => task.state === "failed").length
    : (batchStatusState === "failed" ? 1 : 0)
  const stepsProgress = stepRows.length > 0 ? (completedStepCount / stepRows.length) * 100 : null
  const showCurrentBatchSteps = selectedBatchMatchesJob
  const displayedExecutionModel = showCurrentBatchSteps
    ? taskRows.length > 0
      ? (runningTaskCount > 1 ? "Parallel subtasks" : "Sequential steps")
      : "Sequential steps (batch-level telemetry)"
    : "Waiting for matching batch"
  const displayedProgress = showCurrentBatchSteps ? stepsProgress : 0
  const displayedRunningTasks = showCurrentBatchSteps ? runningTaskCount : 0
  const displayedFailedTasks = showCurrentBatchSteps ? failedTaskCount : 0
  const displayedSteps = showCurrentBatchSteps
    ? stepRows
    : OUR_STEPS.map((step) => ({ ...step, state: "pending" as StepState }))

  if (setupLoading) {
    return (
      <section id="view-ingestion" className="view">
        <article className="card state-card">
          <h2>Run Ingestion</h2>
          <p className="card-message">Loading ingestion capabilities from backend…</p>
        </article>
      </section>
    )
  }

  return (
    <section id="view-ingestion" className="view">
      <div className="page-head">
        <div>
          <h1>Run Ingestion</h1>
          <p>
            Upload, inspect, run, and export ingestion output using the backend ingestion workflow.
          </p>
        </div>
        <div className="actions">
          <button
            type="button"
            className="btn outline"
            disabled={busy.history || busy.refresh}
            onClick={() => {
              void reloadHistoryList()
              if (jobId) {
                void refreshJob()
              }
            }}
          >
            {busy.history || busy.refresh ? "Refreshing..." : "Refresh status"}
          </button>
        </div>
      </div>

      {setupError ? (
        <article className="card danger-box">
          <p>
            <strong>Ingestion API unavailable:</strong> {setupError}
          </p>
          <p>
            This page supports backend profiles exposing either /api/jobs or /api/ingestion/jobs in OpenAPI.
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn outline"
              onClick={() => {
                void retrySetup()
              }}
            >
              Re-check ingestion endpoints
            </button>
          </div>
        </article>
      ) : null}

      <div className="grid two">
        <article className="card integration-action-card">
          <div className="card-head row-split">
            <h2>New ingestion run</h2>
            <span className="chip info">{mode?.prefix === "/api/ingestion" ? "Adapter mode" : "Direct mode"}</span>
          </div>

          <div className="integration-capabilities">
            <span>
              Endpoint profile: <strong>{mode?.prefix ?? "Unavailable"}</strong>
            </span>
            <span>
              Current job id: <strong>{jobId || "Not selected"}</strong>
            </span>
          </div>

          <label className="field-label" htmlFor="ingestion-source-system">
            Source system
          </label>
          <select id="ingestion-source-system" className="text-input" value="synthea" disabled>
            <option value="synthea">synthea</option>
          </select>

          <label className="dropzone" htmlFor="ingestion-files">
            Drop Synthea CSV/ZIP files here, or browse
            <small>.csv files or one .zip only</small>
            <input
              id="ingestion-files"
              className="file-input"
              type="file"
              multiple
              accept=".csv,.zip"
              onChange={(event) => {
                setSelectedFiles(Array.from(event.target.files ?? []))
              }}
            />
          </label>

          <div className="actions">
            <button
              type="button"
              className="btn primary"
              disabled={!mode || busy.upload}
              onClick={() => {
                void uploadFiles()
              }}
            >
              {busy.upload ? "Uploading..." : "Upload files"}
            </button>
            <button
              type="button"
              className="btn success"
              disabled={!mode || busy.start}
              onClick={() => {
                void startJob()
              }}
            >
              {busy.start ? "Starting..." : "Start ingestion"}
            </button>
          </div>

          <CardMessage text={messages.upload.text} error={messages.upload.error} />
          <CardMessage text={messages.job.text} error={messages.job.error} />

          {datasetMeta ? (
            <div className="ingestion-meta">
              <p className="note">
                Found {formatInteger(datasetMeta.found_files.length)} files and {formatInteger(datasetMeta.total_rows)}
                {" "}
                rows.
              </p>
              <p className="note">Inspected input: {datasetMeta.input_dir || "-"}</p>

              {datasetMeta.found_files.length > 0 ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>File</th>
                        <th>Rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datasetMeta.found_files.map((name) => {
                        const count = datasetMeta.row_counts?.[name]
                        return (
                          <tr key={name}>
                            <td>{name}</td>
                            <td>{formatInteger(typeof count === "number" ? count : 0)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {datasetMeta.found_files.length === 0 && datasetRowCounts.length > 0 ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>File</th>
                        <th>Rows</th>
                      </tr>
                    </thead>
                    <tbody>
                      {datasetRowCounts.map(([name, count]) => (
                        <tr key={name}>
                          <td>{name}</td>
                          <td>{formatInteger(typeof count === "number" ? count : 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {(datasetMeta.missing_required_files ?? []).length > 0 ? (
                <div className="chips">
                  {datasetMeta.missing_required_files.map((name) => (
                    <span key={name} className="chip warn">
                      Missing: {name}
                    </span>
                  ))}
                </div>
              ) : (
                <span className="chip good">All required files present</span>
              )}
            </div>
          ) : (
            <p className="card-message">No dataset inspected yet.</p>
          )}
        </article>

        <article className="card integration-status-card">
          <div className="card-head row-split">
            <h2>Live run status</h2>
            <span className="chip info">{asStatusLabel(effectiveJobStatus)}</span>
          </div>

          <div className="actions">
            <button
              type="button"
              className="btn outline"
              disabled={!mode || !jobId || busy.refresh}
              onClick={() => {
                void refreshJob()
              }}
            >
              {busy.refresh ? "Refreshing..." : "Refresh job"}
            </button>
          </div>

          <div className="kpi-grid three">
            <article className="kpi-card">
              <span>Generated</span>
              <strong>{formatInteger(job?.resources_generated)}</strong>
            </article>
            <article className="kpi-card">
              <span>Validated</span>
              <strong>{formatInteger(job?.resources_validated)}</strong>
            </article>
            <article className="kpi-card">
              <span>Persisted</span>
              <strong>{formatInteger(job?.resources_persisted)}</strong>
            </article>
            <article className="kpi-card">
              <span>Warnings</span>
              <strong>{formatInteger(job?.warning_count)}</strong>
            </article>
            <article className="kpi-card">
              <span>Errors</span>
              <strong>{formatInteger(job?.error_count)}</strong>
            </article>
            <article className="kpi-card">
              <span>Source rows</span>
              <strong>{formatInteger(job?.total_source_records)}</strong>
            </article>
          </div>

          <div className="metric-row">
            <span>Persist progress</span>
            <b>{formatRate(overallProgress)}</b>
          </div>
          <div className="meter">
            <span style={{ width: `${toMeterWidth(overallProgress)}%` }}></span>
          </div>

          <div className="integration-capabilities">
            <span>
              Started: <strong>{formatDateTime(job?.started_at)}</strong>
            </span>
            <span>
              Completed: <strong>{formatDateTime(job?.completed_at)}</strong>
            </span>
          </div>

          {stageRows.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Stage</th>
                    <th>Status</th>
                    <th>Elapsed</th>
                  </tr>
                </thead>
                <tbody>
                  {stageRows.map((stage) => (
                    <tr key={stage.name}>
                      <td>{asStatusLabel(stage.name)}</td>
                      <td>{asStatusLabel(stage.status)}</td>
                      <td>{typeof stage.elapsed_seconds === "number" ? `${stage.elapsed_seconds.toFixed(1)}s` : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-message">No stage progress yet.</p>
          )}
        </article>
      </div>

      <article className="card">
        <div className="card-head row-split">
          <h2>Feed completeness scorecard</h2>
          <small>Resource-level progress from runtime telemetry</small>
        </div>

        {resourceProgressRows.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Resource</th>
                  <th>Total</th>
                  <th>Processed</th>
                  <th>Warnings</th>
                  <th>Rejected</th>
                  <th>Completion</th>
                </tr>
              </thead>
              <tbody>
                {resourceProgressRows.map((row) => {
                  const completion = asPercent(row.processed, row.total)
                  return (
                    <tr key={row.resourceType}>
                      <td>{row.resourceType}</td>
                      <td>{formatInteger(row.total)}</td>
                      <td>{formatInteger(row.processed)}</td>
                      <td>{formatInteger(row.warnings)}</td>
                      <td>{formatInteger(row.rejected)}</td>
                      <td>{formatRate(completion)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="card-message">No resource progress data available for the selected run.</p>
        )}
      </article>

      <article className="card">
        <div className="card-head row-split">
          <h2>Post-Ingestion Workflow Status</h2>
        </div>

        <CardMessage text={messages.ourSteps.text} error={messages.ourSteps.error} />

        {!mode?.flowPath ? (
          <p className="card-message">This backend profile does not expose the flow endpoint.</p>
        ) : !ourStepsBatch ? (
          <p className="card-message">Not started yet: run an ingestion job to start post-ingestion workflow steps.</p>
        ) : (
          <>
            <p className="note">
              Batch {ourStepsBatch.batch_id ?? "-"} | {asStatusLabel(ourStepsBatch.status)} | updated{" "}
              {formatDateTime(ourStepsBatch.updated_at)}
            </p>

            {!selectedBatchMatchesJob ? (
              <p className="card-message">
                Latest workflow batch belongs to another ingestion job ({ourStepsBatch.ingest_job_id ?? "-"}).
              </p>
            ) : null}

            {!showCurrentBatchSteps ? <p className="card-message">Waiting for workflow status for the selected ingestion job.</p> : null}

            <div className="step-runtime-note">
              <span>
                Execution model: <strong>{displayedExecutionModel}</strong>
              </span>
              <span>
                Active tasks: <strong>{formatInteger(displayedRunningTasks)}</strong>
              </span>
              <span>
                Failed tasks: <strong>{formatInteger(displayedFailedTasks)}</strong>
              </span>
            </div>

            <div className="metric-row">
              <span>Workflow progress</span>
              <b>{formatRate(displayedProgress)}</b>
            </div>
            <div className="meter">
              <span style={{ width: `${toMeterWidth(displayedProgress)}%` }}></span>
            </div>

            <ol className="steps">
              {displayedSteps.map((step) => {
                const state = step.state
                return (
                  <li key={step.key} className={`step ${state}`}>
                    <span className="dot" aria-hidden="true">
                      {state === "done"
                        ? "✓"
                        : state === "failed"
                          ? "!"
                          : state === "running"
                            ? <span className="running-glyph"></span>
                            : "○"}
                    </span>
                    <span className="step-name">{step.label}</span>
                    <span className={`step-state-badge ${state}`}>{stepStateLabel(state)}</span>
                  </li>
                )
              })}
            </ol>

            {ourStepsBatch.error ? <p className="card-message error">{ourStepsBatch.error}</p> : null}

            {topLevelCounts.length > 0 ? (
              <div className="chips">
                {topLevelCounts.map(([key, value]) => (
                  <span key={key} className="chip info">
                    {asStatusLabel(key)}: {formatInteger(Number(value))}
                  </span>
                ))}
              </div>
            ) : null}

            {taskRows.length > 0 ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Task</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {taskRows.map((task) => (
                      <tr key={task.task}>
                        <td>{asStatusLabel(task.task)}</td>
                        <td>
                          <span className={`step-state-badge ${task.state}`}>{asStatusLabel(task.status)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        )}
      </article>

      <div className="grid two">
        <article className="card">
          <div className="card-head row-split">
            <h2>Validation issues</h2>
            <button
              type="button"
              className="btn outline"
              disabled={!mode || !jobId || busy.validation}
              onClick={() => {
                void loadValidation()
              }}
            >
              {busy.validation ? "Loading..." : "Load validation"}
            </button>
          </div>

          <CardMessage text={messages.validation.text} error={messages.validation.error} />

          {validationIssues.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Type</th>
                    <th>Message</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {validationIssues.slice(0, 100).map((issue, index) => (
                    <tr key={`${issue.id ?? "issue"}-${index}`}>
                      <td>{asStatusLabel(issue.severity)}</td>
                      <td>{issue.issue_type ?? issue.issue_code ?? "-"}</td>
                      <td>{issue.message ?? issue.details ?? "-"}</td>
                      <td>{issue.source_file ?? issue.source_record_id ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-message">No validation issues loaded yet.</p>
          )}
        </article>

        <article className="card">
          <div className="card-head row-split">
            <h2>Patients and FHIR</h2>
            <button
              type="button"
              className="btn outline"
              disabled={!mode || !jobId || busy.patients}
              onClick={() => {
                void loadPatients()
              }}
            >
              {busy.patients ? "Loading..." : "Load patients"}
            </button>
          </div>

          <div className="actions">
            <select
              className="text-input compact"
              value={selectedPatientId}
              onChange={(event) => {
                void selectPatient(event.target.value)
              }}
            >
              <option value="">Choose a patient</option>
              {patients.map((patient) => (
                <option key={patient.patient_id} value={patient.patient_id}>
                  {patient.patient_id} ({formatInteger(patient.resource_count)} resources)
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn outline"
              disabled={!selectedPatientId || busy.exportPatient}
              onClick={() => {
                void downloadSelectedBundle()
              }}
            >
              {busy.exportPatient ? "Preparing..." : "Download selected FHIR"}
            </button>
            <button
              type="button"
              className="btn outline"
              disabled={!jobId || busy.exportAll}
              onClick={() => {
                void downloadAllBundles()
              }}
            >
              {busy.exportAll ? "Preparing..." : "Download all FHIR"}
            </button>
          </div>

          <CardMessage text={messages.patients.text} error={messages.patients.error} />

          {patients.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Patient</th>
                    <th>Resources</th>
                    <th>Estimated size</th>
                    <th>Saved at</th>
                  </tr>
                </thead>
                <tbody>
                  {patients.slice(0, 50).map((patient) => (
                    <tr key={patient.patient_id}>
                      <td>{patient.patient_id}</td>
                      <td>{formatInteger(patient.resource_count)}</td>
                      <td>{asKb(patient.estimated_size_bytes)}</td>
                      <td>{formatDateTime(patient.saved_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="card-message">No patient list loaded yet.</p>
          )}

          {selectedResourceTypeRows.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Selected patient resource type</th>
                    <th>Records</th>
                    <th>Persisted</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedResourceTypeRows.map((row) => (
                    <tr key={row.resourceType}>
                      <td>{row.resourceType}</td>
                      <td>{formatInteger(row.total)}</td>
                      <td>{formatInteger(row.persisted)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </article>
      </div>

      <article className="card">
        <div className="card-head row-split">
          <h2>Job history</h2>
          <button
            type="button"
            className="btn outline"
            disabled={!mode || busy.history}
            onClick={() => {
              void reloadHistoryList()
            }}
          >
            {busy.history ? "Loading..." : "Reload list"}
          </button>
        </div>

        <CardMessage text={messages.history.text} error={messages.history.error} />

        {history.length > 0 ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Generated</th>
                  <th>Persisted</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 50).map((item) => (
                  <tr
                    key={item.id}
                    className={item.id === jobId ? "history-row-active" : ""}
                    onClick={() => {
                      void selectJob(item.id)
                    }}
                  >
                    <td>{item.id}</td>
                    <td>{asStatusLabel(item.status)}</td>
                    <td>{formatDateTime(item.started_at)}</td>
                    <td>{formatInteger(item.resources_generated)}</td>
                    <td>{formatInteger(item.resources_persisted)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="card-message">No ingestion jobs found yet.</p>
        )}
      </article>
    </section>
  )
}
