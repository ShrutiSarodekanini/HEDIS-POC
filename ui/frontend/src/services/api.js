/**
 * API Service - Centralized backend communication
 */

const API_BASE = (import.meta.env.VITE_API_BASE || 'http://localhost:8000').replace(/\/$/, '');

export async function api(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const isFormData = options.body instanceof FormData;
  
  try {
    const response = await fetch(url, {
      headers: isFormData
        ? { ...options.headers }
        : { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`API Error: ${response.status} - ${errorText}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`API call failed: ${endpoint}`, error);
    throw error;
  }
}

// Job Management
export const jobs = {
  async convert(payload) {
    return api('/api/jobs/convert', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async getStatus(jobId) {
    return api(`/api/jobs/${jobId}`);
  },

  async list() {
    return api('/api/jobs');
  },

  async cancel(jobId) {
    return api(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
  },
};

// Coverage Data
export const coverage = {
  async getReport(filters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value && value !== 'All') params.append(key, value);
    });
    return api(`/api/coverage/report?${params}`);
  },

  async getPatientCoverage(patientId) {
    return api(`/api/coverage/patient/${patientId}`);
  },

  async getGapDetails(coverId) {
    return api(`/api/coverage/${coverId}/gaps`);
  },
};

// Dataset Inspection
export async function inspectDataset(path) {
  return api(`/api/datasets/inspect?input_dir=${encodeURIComponent(path)}`);
}

// Validation Issues
export async function getValidationIssues(jobId, filters = {}) {
  const params = new URLSearchParams({ job_id: jobId });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.append(key, value);
  });
  return api(`/api/validation/issues?${params}`);
}

// DQ / AD
export const dq = {
  async getBatches(limit = 100) {
    return api(`/api/batches?limit=${limit}`);
  },

  async getMeasureConfig() {
    return api('/api/config/measures');
  },

  async getMeasures(batchId) {
    const suffix = batchId ? `?batch_id=${encodeURIComponent(batchId)}` : '';
    return api(`/api/measures${suffix}`);
  },

  async runChecks({ jobId, batchId, measureId, forceRerun = true }) {
    const params = new URLSearchParams({
      job_id: jobId,
      batch_id: batchId,
      force_rerun: String(forceRerun),
    });
    if (measureId) params.append('measure_id', measureId);
    return api(`/api/run-checks?${params}`, { method: 'POST' });
  },

  async getRuns(filters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.append(key, value);
    });
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return api(`/api/dq/runs${suffix}`);
  },

  async getSummary(filters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') params.append(key, value);
    });
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return api(`/api/summary${suffix}`);
  },

  async getAnomalies(filters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '' && value !== 'All') params.append(key, value);
    });
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return api(`/api/anomalies${suffix}`);
  },

  async getChecks(dqRunId, limit = 1000) {
    return api(`/api/dq/runs/${dqRunId}/checks?limit=${limit}`);
  },

  async getTrends(limit = 30) {
    return api(`/api/trends/anomalies-by-batch?limit=${limit}`);
  },
};
