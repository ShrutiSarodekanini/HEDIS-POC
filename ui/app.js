const inputDir = document.getElementById('inputDir');
const folderPicker = document.getElementById('folderPicker');
const zipPicker = document.getElementById('zipPicker');
const pickFolderBtn = document.getElementById('pickFolderBtn');
const uploadFolderBtn = document.getElementById('uploadFolderBtn');
const pickZipBtn = document.getElementById('pickZipBtn');
const uploadZipBtn = document.getElementById('uploadZipBtn');
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
let selectedZip = null;

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

  uploadFolderBtn.disabled = true;
  uploadFolderBtn.textContent = 'Uploading...';

  const form = new FormData();
  csvOnly.forEach(file => {
    const rel = file.webkitRelativePath || file.name;
    form.append('files', file, rel);
  });

  try {
    const res = await fetchWithTimeout('/api/datasets/upload', {
      method: 'POST',
      body: form
    }, 180000);

    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
    }

    const result = await res.json();
    inputDir.value = result.input_dir;
    inspectResult.textContent = pretty(result);
  } finally {
    uploadFolderBtn.disabled = false;
    uploadFolderBtn.textContent = 'Upload Selected Folder';
  }
}

async function uploadSelectedZip() {
  if (!selectedZip) {
    inspectResult.textContent = 'Select a ZIP file first.';
    return;
  }

  uploadZipBtn.disabled = true;
  uploadZipBtn.textContent = 'Uploading...';

  const form = new FormData();
  form.append('file', selectedZip, selectedZip.name);

  try {
    const res = await fetchWithTimeout('/api/datasets/upload-zip', {
      method: 'POST',
      body: form
    }, 180000);

    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
    }

    const result = await res.json();
    inputDir.value = result.input_dir;
    inspectResult.textContent = pretty(result);
  } finally {
    uploadZipBtn.disabled = false;
    uploadZipBtn.textContent = 'Upload ZIP';
  }
}

function renderStages(runtime) {
  stagesList.innerHTML = '';
  if (!runtime || !runtime.stages) {
    return;
  }
  runtime.stages.forEach(stage => {
    const li = document.createElement('li');
    li.className = `status-${stage.status}`;
    const elapsed = typeof stage.elapsed_seconds === 'number'
      ? ` (${stage.elapsed_seconds.toFixed(3)}s)`
      : '';
    li.textContent = `${stage.name}: ${stage.status}${elapsed}${stage.details ? ` - ${stage.details}` : ''}`;
    stagesList.appendChild(li);
  });
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

folderPicker.onchange = () => {
  const all = Array.from(folderPicker.files || []);
  selectedFiles = all.filter(file => SUPPORTED_CSV.has((file.name || '').toLowerCase()));
  if (!selectedFiles.length) {
    inspectResult.textContent = 'No supported Synthea CSV files selected.';
    return;
  }
  const top = selectedFiles[0].webkitRelativePath?.split('/')[0] || 'selected-folder';
  inspectResult.textContent = `Selected ${selectedFiles.length} supported CSV file(s) from ${top}. Click \"Upload Selected Folder\" next.`;
};

pickZipBtn.onclick = () => {
  zipPicker.click();
};

zipPicker.onchange = () => {
  selectedZip = (zipPicker.files && zipPicker.files[0]) ? zipPicker.files[0] : null;
  if (!selectedZip) {
    inspectResult.textContent = 'No ZIP selected.';
    return;
  }
  inspectResult.textContent = `Selected ZIP: ${selectedZip.name}. Click \"Upload ZIP\" next.`;
};

uploadFolderBtn.onclick = async () => {
  try {
    await uploadSelectedFolder();
  } catch (err) {
    inspectResult.textContent = err.name === 'AbortError'
      ? 'Upload timed out after 3 minutes. Retry with ZIP upload or a smaller folder selection.'
      : err.message;
  }
};

uploadZipBtn.onclick = async () => {
  try {
    await uploadSelectedZip();
  } catch (err) {
    inspectResult.textContent = err.name === 'AbortError'
      ? 'ZIP upload timed out after 3 minutes. Retry once or use a smaller ZIP.'
      : err.message;
  }
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
    summary.textContent = pretty(result);
    await refreshBtn.onclick();
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
    renderStages(result.runtime);
    await loadPatients(jobId);
  } catch (err) {
    summary.textContent = err.message;
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
