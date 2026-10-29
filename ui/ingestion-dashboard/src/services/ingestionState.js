const INGESTION_ACTIVITY_KEY = 'hedis.ingestion.activity';
const INGESTION_ACTIVITY_EVENT = 'hedis:ingestion-activity-changed';

function canUseBrowserStorage() {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function emitIngestionActivityChanged(detail) {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(INGESTION_ACTIVITY_EVENT, { detail }));
}

function parseStoredActivity(value) {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    if (!parsed || parsed.active !== true || !parsed.jobId) return null;
    return { active: true, jobId: String(parsed.jobId) };
  } catch {
    return null;
  }
}

export function getIngestionActivity() {
  if (!canUseBrowserStorage()) {
    return { active: false, jobId: null };
  }
  const parsed = parseStoredActivity(window.localStorage.getItem(INGESTION_ACTIVITY_KEY));
  if (!parsed) {
    return { active: false, jobId: null };
  }
  return parsed;
}

export function isIngestionActive() {
  return getIngestionActivity().active;
}

export function setIngestionActivity(active, jobId = null) {
  if (!canUseBrowserStorage()) return;

  if (active && jobId) {
    window.localStorage.setItem(
      INGESTION_ACTIVITY_KEY,
      JSON.stringify({ active: true, jobId: String(jobId), updatedAt: Date.now() })
    );
    emitIngestionActivityChanged({ active: true, jobId: String(jobId) });
    return;
  }

  window.localStorage.removeItem(INGESTION_ACTIVITY_KEY);
  emitIngestionActivityChanged({ active: false, jobId: null });
}

export { INGESTION_ACTIVITY_EVENT, INGESTION_ACTIVITY_KEY };
