const inputDir = document.getElementById('inputDir');
const folderPicker = document.getElementById('folderPicker');
const pickFolderBtn = document.getElementById('pickFolderBtn');
const inspectBtn = document.getElementById('inspectBtn');
const startBtn = document.getElementById('startBtn');
const refreshBtn = document.getElementById('refreshBtn');
const persistBtn = document.getElementById('persistBtn');
const jobsBtn = document.getElementById('jobsBtn');
const issuesBtn = document.getElementById('issuesBtn');
const loadPatientBtn = document.getElementById('loadPatientBtn');

const inspectResult = document.getElementById('inspectResult');
const summary = document.getElementById('summary');
const jobsResult = document.getElementById('jobsResult');
const issuesResult = document.getElementById('issuesResult');
const patientSelect = document.getElementById('patientSelect');
const patientResources = document.getElementById('patientResources');
const jobIdInput = document.getElementById('jobId');
const stagesList = document.getElementById('stages');
const pipelineStagesList = document.getElementById('pipelineStages');

const SUPPORTED_CSV = new Set([
  'patients.csv',
  'encounters.csv',
  'conditions.csv',
  'observations.csv',
  'procedures.csv',
  'medications.csv',
  'immunizations.csv',
  'careplans.csv',
  'claims.csv',
  'coverages.csv'
]);

let selectedFiles = [];
let refreshTimer = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }
  return res.json();
}

async function fetchWithTimeout(url, opts = {}, timeoutMs = 120000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function currentJobId() {
  return jobIdInput.value.trim();
}

function currentInputDir() {
  return inputDir.value.trim();
}

function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

async function uploadSelectedFolder() {
  if (!selectedFiles.length) {
    inspectResult.textContent = 'Select a local folder first.';
    return;
  }

  const csvOnly = selectedFiles.filter(file => SUPPORTED_CSV.has((file.name || '').toLowerCase()));
  if (!csvOnly.length) {
    inspectResult.textContent = 'No supported Synthea CSV files found in the selected folder.';
    return;
  }

  const form = new FormData();
  csvOnly.forEach(file => {
    const rel = file.webkitRelativePath || file.name;
    form.append('files', file, rel);
  });

  try {
    const result = await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', '/api/datasets/upload');
      request.timeout = 180000;
      request.upload.onprogress = event => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded / event.total) * 100);
          inspectResult.textContent = `Uploading dataset... ${percent}%`;
        }
      };
      request.onload = () => {
        if (request.status < 200 || request.status >= 300) {
          reject(new Error(request.responseText || `Upload failed (${request.status})`));
          return;
        }
        resolve(JSON.parse(request.responseText));
      };
      request.onerror = () => reject(new Error('Upload failed. Check the backend connection.'));
      request.ontimeout = () => reject(new Error('Folder upload timed out after 3 minutes. Try entering the local path directly.'));
      request.send(form);
    });
    inputDir.value = result.input_dir;
    inspectResult.textContent = pretty(result);
  } catch (err) {
    inspectResult.textContent = err.name === 'AbortError'
      ? 'Folder upload timed out after 3 minutes. Try a smaller folder.'
      : err.message;
  }
}

function setRunProgress(processed, total) {
  const safeTotal = Number(total) || 0;
  const safeProcessed = Number(processed) || 0;
  const percent = safeTotal ? Math.min(100, (safeProcessed / safeTotal) * 100) : 0;
  const progressBar = document.querySelector('.progress-track span');
  if (progressBar) {
    progressBar.style.width = `${percent}%`;
  }
  document.getElementById('progressText').textContent = safeTotal
    ? `${safeProcessed.toLocaleString()} / ${safeTotal.toLocaleString()} resources`
    : `${safeProcessed.toLocaleString()} resources processed`;
}

function renderStages(runtime, job) {
  stagesList.innerHTML = '';
  pipelineStagesList.innerHTML = '';
  if (!runtime || !runtime.stages) {
    setRunProgress(0, job?.resources_generated);
    return;
  }

  const completed = runtime.stages.filter(stage => stage.status === 'completed').length;
  const total = runtime.stages.length;
  const current = runtime.stages.find(stage => stage.status === 'running');
  const generated = job?.resources_generated || job?.summary?.resources_generated || 0;
  const processed = runtime.status === 'completed' || runtime.status === 'partially_completed'
    ? generated
    : Math.round((completed / total) * generated);
  setRunProgress(processed, generated);

  const displayStages = [...runtime.stages];
  const validationIndex = displayStages.findIndex(stage => stage.name === 'validation');
  const generationIndex = displayStages.findIndex(stage => stage.name === 'fhir_generation');
  const validationDuringMapping = displayStages[validationIndex]?.details?.includes('during mapping');
  if (validationDuringMapping && validationIndex > generationIndex) {
    const [validationStage] = displayStages.splice(validationIndex, 1);
    displayStages.splice(generationIndex, 0, validationStage);
  }

  displayStages.forEach(stage => {
    const li = document.createElement('li');
    li.className = `status-${stage.status}`;
    const name = document.createElement('span');
    name.textContent = stage.name.replaceAll('_', ' ');
    const details = document.createElement('span');
    details.textContent = stage.details || '--';
    const status = document.createElement('span');
    status.textContent = stage.status;
    li.append(name, details, status);
    pipelineStagesList.appendChild(li);
  });

  if (runtime.resource_progress && Object.keys(runtime.resource_progress).length) {
    const validationStage = runtime.stages.find(stage => stage.name === 'validation');
    renderLiveResources(runtime.resource_progress, runtime.status, validationStage?.status);
    return;
  }
  document.getElementById('progressRate').textContent = current
    ? `Currently: ${current.name.replaceAll('_', ' ')}`
    : `${runtime.status || 'ready'}`;
}

function renderLiveResources(progress, runStatus, validationStatus = 'pending') {
  stagesList.innerHTML = '';
  let processed = 0;
  let total = 0;

  Object.entries(progress).forEach(([resourceType, entry]) => {
    const resourceTotal = Number(entry.total) || 0;
    const resourceProcessed = Number(entry.processed) || 0;
    processed += resourceProcessed;
    total += resourceTotal;
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = resourceType;
    const count = document.createElement('span');
    count.textContent = `${resourceProcessed.toLocaleString()} / ${resourceTotal.toLocaleString()}`;
    const status = document.createElement('span');
    const rejected = Number(entry.rejected) || 0;
    const warnings = Number(entry.warnings) || 0;
    const validationProcessed = Number(entry.validation_processed) || 0;
    const isComplete = resourceProcessed >= resourceTotal && resourceTotal > 0;
    const resourceIsValidating = validationStatus === 'running' && resourceProcessed > validationProcessed;
    const resourceValidationComplete = resourceTotal > 0
      && resourceProcessed >= resourceTotal
      && validationProcessed >= resourceTotal;
    status.textContent = resourceValidationComplete && rejected
      ? 'Rejected'
      : resourceIsValidating
        ? 'Validating'
        : resourceValidationComplete
          ? (warnings ? 'Warning' : 'Validated')
          : isComplete ? 'Loaded'
          : resourceProcessed ? 'Loading' : 'Queued';
    status.className = resourceValidationComplete && rejected
      ? 'status-failed'
      : resourceValidationComplete && !warnings
        ? 'status-validated'
        : resourceIsValidating ? 'status-running' : resourceProcessed ? 'status-running' : 'status-queued';
    li.append(name, count, status);
    stagesList.appendChild(li);
  });

  setRunProgress(processed, total);
  const rate = total && processed ? Math.round(processed / Math.max((Date.now() - (window.runStartedAt || Date.now())) / 1000, 1)) : 0;
  document.getElementById('progressRate').textContent = runStatus === 'running'
    ? `~${rate.toLocaleString()} resources/sec`
    : `${processed.toLocaleString()} resources loaded`;
}

function renderResourceStatus(resources, job) {
  if (!resources.length) {
    return false;
  }

  const grouped = new Map();
  resources.forEach(resource => {
    const entry = grouped.get(resource.resource_type) || { count: 0, statuses: new Set() };
    entry.count += 1;
    entry.statuses.add(resource.lifecycle_status || resource.validation_status || 'generated');
    grouped.set(resource.resource_type, entry);
  });

  stagesList.innerHTML = '';
  grouped.forEach((entry, resourceType) => {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = resourceType;
    const count = document.createElement('span');
    count.textContent = entry.count.toLocaleString();
    const status = document.createElement('span');
    const hasRejected = entry.statuses.has('rejected');
    const hasWarnings = [...entry.statuses].some(value => value.includes('warning') || value === 'quarantined');
    status.textContent = hasRejected ? 'Rejected' : hasWarnings ? 'Warning' : 'Validated';
    status.className = hasRejected ? 'status-failed' : hasWarnings ? 'status-running' : 'status-completed';
    li.append(name, count, status);
    stagesList.appendChild(li);
  });

  const generated = job?.resources_generated || job?.summary?.resources_generated || resources.length;
  setRunProgress(resources.length, generated);
  document.getElementById('progressRate').textContent = `${resources.length.toLocaleString()} resources loaded`;
  return true;
}

function renderInterruptedStatus() {
  stagesList.innerHTML = '';
  const li = document.createElement('li');
  li.className = 'status-failed';
  const name = document.createElement('span');
  name.textContent = 'Pipeline runtime';
  const details = document.createElement('span');
  details.textContent = 'No active worker state';
  const status = document.createElement('span');
  status.textContent = 'Interrupted';
  li.append(name, details, status);
  stagesList.appendChild(li);
  setRunProgress(0, 0);
  document.getElementById('progressRate').textContent = 'Start a new ingestion run';
}

async function loadPatients(jobId) {
  const result = await api(`/api/jobs/${jobId}/patients`);
  patientSelect.innerHTML = '';
  result.patients.forEach(patient => {
    const opt = document.createElement('option');
    opt.value = patient;
    opt.textContent = patient;
    patientSelect.appendChild(opt);
  });
}

pickFolderBtn.onclick = () => {
  folderPicker.click();
};

folderPicker.onchange = async () => {
  const all = Array.from(folderPicker.files || []);
  selectedFiles = all.filter(file => SUPPORTED_CSV.has((file.name || '').toLowerCase()));
  if (!selectedFiles.length) {
    inspectResult.textContent = 'No supported Synthea CSV files selected.';
    return;
  }
  const top = selectedFiles[0].webkitRelativePath?.split('/')[0] || 'selected-folder';
  inspectResult.textContent = `Uploading ${selectedFiles.length} supported CSV file(s) from ${top}...`;
  await uploadSelectedFolder();
};

inspectBtn.onclick = async () => {
  const dir = currentInputDir();
  if (!dir) {
    inspectResult.textContent = 'Input folder is empty. Upload a folder/ZIP first or type a valid path.';
    return;
  }
  try {
    const result = await api(`/api/datasets/inspect?input_dir=${encodeURIComponent(dir)}`);
    inspectResult.textContent = pretty(result);
  } catch (err) {
    inspectResult.textContent = err.message;
  }
};

startBtn.onclick = async () => {
  const dir = currentInputDir();
  if (!dir) {
    summary.textContent = 'Input folder is empty. Upload a folder/ZIP first or type a valid path.';
    return;
  }
  try {
    const result = await api(`/api/jobs/start?input_dir=${encodeURIComponent(dir)}`, { method: 'POST' });
    jobIdInput.value = result.job_id;
    window.runStartedAt = Date.now();
    summary.textContent = pretty(result);
    await refreshBtn.onclick();
    clearInterval(refreshTimer);
    refreshTimer = setInterval(async () => {
      await refreshBtn.onclick();
      const status = document.getElementById('runBadge').textContent;
      if (status !== 'running') {
        clearInterval(refreshTimer);
      }
    }, 1000);
  } catch (err) {
    summary.textContent = err.message;
  }
};

refreshBtn.onclick = async () => {
  const jobId = currentJobId();
  if (!jobId) {
    summary.textContent = 'No job selected.';
    return;
  }
  try {
    const result = await api(`/api/jobs/${jobId}`);
    summary.textContent = pretty(result);
    window.runStartedAt = result.runtime?.started_at ? result.runtime.started_at * 1000 : window.runStartedAt;
    const runtimeStatus = result.runtime?.status || 'unknown';
    const displayStatus = runtimeStatus === 'unknown' ? 'interrupted' : result.status;
    document.getElementById('liveJobTitle').textContent = `Batch ${jobId.slice(0, 8)} · ${displayStatus}`;
    document.getElementById('runBadge').textContent = displayStatus.replaceAll('_', ' ');
    document.getElementById('runBadge').className = `running-badge badge-${displayStatus}`;
    if (runtimeStatus === 'unknown') {
      renderInterruptedStatus();
      return;
    }
    renderStages(result.runtime, result);
    if (result.status !== 'running' && !Object.keys(result.runtime?.resource_progress || {}).length) {
      const resources = await api(`/api/jobs/${jobId}/resources`);
      renderResourceStatus(resources.resources || [], result);
    }
    await loadPatients(jobId);
  } catch (err) {
    summary.textContent = err.message;
  }
};

waitValidationBtn.onclick = async () => {
  const jobId = currentJobId();
  if (!jobId) {
    summary.textContent = 'No job selected.';
    return;
  }
  waitValidationBtn.disabled = true;
  waitValidationBtn.textContent = 'Waiting...';
  try {
    let result;
    for (let i = 0; i < 600; i++) {
      result = await api(`/api/jobs/${jobId}`);
      const validationStage = result.runtime?.stages?.find(s => s.name === 'validation');
      if (validationStage && validationStage.status !== 'running') {
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    summary.textContent = `Validation completed: ${result.resources_validated} validated, ${result.warning_count} warnings, ${result.error_count} errors`;
    await refreshBtn.onclick();
  } catch (err) {
    summary.textContent = `Wait failed: ${err.message}`;
  } finally {
    waitValidationBtn.disabled = false;
    waitValidationBtn.textContent = 'Wait for validation';
  }
};

persistBtn.onclick = async () => {
  const jobId = currentJobId();
  if (!jobId) {
    summary.textContent = 'No job selected.';
    return;
  }
  try {
    const result = await api(`/api/jobs/${jobId}/persist`, { method: 'POST' });
    summary.textContent = pretty(result);
    await refreshBtn.onclick();
  } catch (err) {
    summary.textContent = err.message;
  }
};

jobsBtn.onclick = async () => {
  try {
    const result = await api('/api/jobs');
    jobsResult.textContent = pretty(result);
  } catch (err) {
    jobsResult.textContent = err.message;
  }
};

issuesBtn.onclick = async () => {
  const jobId = currentJobId();
  if (!jobId) {
    issuesResult.textContent = 'No job selected.';
    return;
  }
  try {
    const result = await api(`/api/jobs/${jobId}/validation`);
    issuesResult.textContent = pretty(result);
  } catch (err) {
    issuesResult.textContent = err.message;
  }
};

loadPatientBtn.onclick = async () => {
  const jobId = currentJobId();
  const patientId = patientSelect.value;
  if (!jobId || !patientId) {
    patientResources.textContent = 'Select a job and patient first.';
    return;
  }
  try {
    const result = await api(`/api/jobs/${jobId}/patients/${encodeURIComponent(patientId)}/resources`);
    patientResources.textContent = pretty(result);
  } catch (err) {
    patientResources.textContent = err.message;
  }
};

async function loadLatestJob() {
  try {
    const result = await api('/api/jobs');
    const latest = result.jobs && result.jobs[0];
    if (!latest) {
      return;
    }
    jobIdInput.value = latest.id;
    await refreshBtn.onclick();
  } catch (err) {
    summary.textContent = `Unable to load latest job: ${err.message}`;
  }
}

loadLatestJob();
