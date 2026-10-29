import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { dq } from '../services/api';

const ALL_MEASURES = '__all_measures__';

export default function DataQuality() {
  const [batches, setBatches] = useState([]);
  const [selectedBatchId, setSelectedBatchId] = useState('');
  const [selectedMeasureId, setSelectedMeasureId] = useState(ALL_MEASURES);
  const [measures, setMeasures] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [summary, setSummary] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [checks, setChecks] = useState([]);
  const [severityFilter, setSeverityFilter] = useState('All');
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [measureMeta, setMeasureMeta] = useState({});
  const [dqReady, setDqReady] = useState(false);

  const selectedBatch = useMemo(
    () => batches.find((batch) => batch.batch_id === selectedBatchId) || null,
    [batches, selectedBatchId]
  );
  const selectedRun = useMemo(
    () => runs.find((run) => String(run.id) === String(selectedRunId)) || null,
    [runs, selectedRunId]
  );
  const selectedRunMeasureScope = useMemo(() => {
    if (!selectedRun || !selectedRun.summary) return null;
    return selectedRun.summary?.trigger?.measure_id || selectedRun.summary?.selected_measure || null;
  }, [selectedRun]);

  useEffect(() => {
    loadInitial();
  }, []);

  useEffect(() => {
    if (selectedBatchId) {
      loadRuns(selectedBatchId);
      loadMeasures(selectedBatchId);
    }
  }, [selectedBatchId]);

  useEffect(() => {
    if (selectedRunId) {
      loadRunData(selectedRunId, severityFilter);
    }
  }, [selectedRunId, severityFilter]);

  const loadInitial = async () => {
    try {
      setLoading(true);
      setError('');
      const [batchResp, measureResp] = await Promise.all([
        dq.getBatches(100),
        dq.getMeasureConfig(),
      ]);
      const allBatches = batchResp.batches || [];
      const nextBatches = allBatches.filter((batch) => {
        const status = String(batch?.status || '').toLowerCase();
        return status === 'completed' || status === 'partially_completed';
      });
      setBatches(nextBatches);
      setDqReady(nextBatches.length > 0);
      if (nextBatches.length > 0) {
        setSelectedBatchId(nextBatches[0].batch_id);
      } else {
        setSelectedBatchId('');
        setSelectedRunId('');
        setSummary(null);
        setAnomalies([]);
        setChecks([]);
      }
      const configMeasures = measureResp.measures || [];
      const measureOptions = [ALL_MEASURES, ...configMeasures.map((item) => item.measure_id)];
      const meta = {};
      configMeasures.forEach((item) => {
        meta[item.measure_id] = item;
      });
      setMeasureMeta(meta);
      setMeasures(measureOptions);
      setSelectedMeasureId(ALL_MEASURES);
    } catch (e) {
      setError(`Unable to load DQ dashboard data: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const loadMeasures = async (batchId) => {
    try {
      const response = await dq.getMeasures(batchId);
      const options = [ALL_MEASURES, ...(response.measures || [])];
      setMeasures(options);
      if (options.length > 0 && !options.includes(selectedMeasureId)) {
        setSelectedMeasureId(options[0]);
      }
    } catch {
      // Keep previously loaded measures if this call fails.
    }
  };

  const loadRuns = async (batchId) => {
    try {
      const response = await dq.getRuns({ batch_id: batchId });
      const allRuns = response.dq_runs || [];
      const completedRuns = allRuns.filter((run) => String(run?.status || '').toLowerCase() === 'completed');
      const nextRuns = completedRuns.length > 0 ? completedRuns : allRuns;
      setRuns(nextRuns);
      let nextRunId = '';
      if (nextRuns.length > 0) {
        nextRunId = String(nextRuns[0].id);
        setSelectedRunId(nextRunId);
      } else {
        setSelectedRunId('');
        setSummary(null);
        setAnomalies([]);
        setChecks([]);
      }
      return nextRunId;
    } catch (e) {
      setError(`Unable to load DQ runs: ${e.message}`);
      return '';
    }
  };

  const loadRunData = async (runId, severity) => {
    try {
      setLoading(true);
      const [summaryResp, anomaliesResp, checksResp] = await Promise.all([
        dq.getSummary({ dq_run_id: runId }),
        dq.getAnomalies({
          dq_run_id: runId,
          severity: severity === 'All' ? '' : severity.toLowerCase(),
          limit: 500,
        }),
        dq.getChecks(runId, 1000),
      ]);
      setSummary(summaryResp);
      setAnomalies(anomaliesResp.anomalies || []);
      setChecks(checksResp.checks || []);
    } catch (e) {
      setError(`Unable to load DQ results: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleRunChecks = async () => {
    if (!selectedBatch || !selectedBatchId) {
      setError('Select a batch before running checks.');
      return;
    }
    if (!selectedBatch.status || selectedBatch.status === 'pending') {
      setError('Selected batch is not ready yet.');
      return;
    }
    const targetJobId = selectedBatch.job_id || runs[0]?.job_id;
    if (!targetJobId) {
      setError('Unable to locate job for selected batch.');
      return;
    }
    try {
      setError('');
      setRunning(true);
      await dq.runChecks({
        jobId: targetJobId,
        batchId: selectedBatchId,
        measureId: selectedMeasureId && selectedMeasureId !== ALL_MEASURES ? selectedMeasureId : undefined,
        forceRerun: true,
      });
      const latestRunId = await loadRuns(selectedBatchId);
      if (latestRunId) {
        await loadRunData(latestRunId, severityFilter);
      }
    } catch (e) {
      setError(`Run checks failed: ${e.message}`);
    } finally {
      setRunning(false);
    }
  };

  const filteredAnomalies = useMemo(() => {
    if (!selectedMeasureId || selectedMeasureId === ALL_MEASURES) return anomalies;
    return anomalies.filter((anomaly) => anomaly?.details?.measure_id === selectedMeasureId);
  }, [anomalies, selectedMeasureId]);

  const filteredChecks = useMemo(() => {
    if (!selectedMeasureId || selectedMeasureId === ALL_MEASURES) return checks;
    return checks.filter((check) => {
      const detailsMeasure = check?.details?.measure_id;
      if (detailsMeasure) return detailsMeasure === selectedMeasureId;
      if (typeof check?.metric_name === 'string' && check.metric_name.startsWith(`${selectedMeasureId}_`)) return true;
      return false;
    });
  }, [checks, selectedMeasureId]);

  const displayedKpis = useMemo(() => {
    const severityCounts = {};
    filteredAnomalies.forEach((anomaly) => {
      const key = String(anomaly?.severity || 'unknown').toLowerCase();
      severityCounts[key] = (severityCounts[key] || 0) + 1;
    });
    const passedChecks = filteredChecks.filter((check) => check?.status === 'passed').length;
    const failedChecks = filteredChecks.filter((check) => check?.status === 'failed').length;
    const totalChecks = filteredChecks.length;
    const topSeverity = Object.entries(severityCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || 'none';
    return {
      total_checks: totalChecks,
      passed_checks: passedChecks,
      failed_checks: failedChecks,
      anomalies_raised: filteredAnomalies.length,
      total_anomalies: filteredAnomalies.length,
      top_severity: topSeverity,
      severity_counts: severityCounts,
    };
  }, [filteredAnomalies, filteredChecks]);

  if (!dqReady && !loading) {
    return (
      <div className="dq-surface">
        <header className="page-header">
          <div>
            <p className="eyebrow">Layer 8 of 8</p>
            <h1>Data Quality &amp; Anomaly Detection</h1>
            <p className="subtitle">Run ingestion first to unlock DQ/AD results.</p>
          </div>
        </header>
        <section className="panel dq-empty-state">
          <h2>DQ/AD becomes available after ingestion completion</h2>
          <p className="helper-text" style={{ marginTop: '8px' }}>
            No completed ingestion batch is available yet. Complete one run in Run Ingestion, then come back to DQ &amp; AD.
          </p>
          {error && <p className="helper-text" style={{ color: '#b94b45' }}>{error}</p>}
          <div style={{ marginTop: '12px' }}>
            <Link to="/run" className="primary-button" style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}>
              Go to Run Ingestion
            </Link>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="dq-surface">
      <header className="page-header">
        <div>
          <p className="eyebrow">Layer 8 of 8</p>
          <h1>Data Quality &amp; Anomaly Detection</h1>
          <p className="subtitle">Monitor DQ runs, check metrics, and anomaly queue</p>
        </div>
        <div className="header-status"><span className="status-dot"></span>DQ ready</div>
      </header>

      <section className="panel" style={{ marginBottom: '18px' }}>
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Execution</p>
            <h2>Run and filter</h2>
          </div>
        </div>
        <div className="dq-filter-grid">
          <div>
            <label>Batch</label>
            <select value={selectedBatchId} onChange={(e) => setSelectedBatchId(e.target.value)}>
              {batches.map((batch) => (
                <option key={batch.batch_id} value={batch.batch_id}>
                  {batch.batch_id} · {batch.status}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Measure</label>
            <select value={selectedMeasureId} onChange={(e) => setSelectedMeasureId(e.target.value)}>
              {measures.map((measure) => (
                <option key={measure} value={measure}>{measure === ALL_MEASURES ? 'All measures' : measure}</option>
              ))}
            </select>
          </div>
          <div>
            <label>DQ Run</label>
            <select value={selectedRunId} onChange={(e) => setSelectedRunId(e.target.value)}>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  #{run.id} · {run.status}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label>Severity</label>
            <select value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option>All</option>
              <option>Critical</option>
              <option>High</option>
              <option>Medium</option>
              <option>Low</option>
            </select>
          </div>
        </div>
        {selectedMeasureId && selectedMeasureId !== ALL_MEASURES && measureMeta[selectedMeasureId] && (
          <div className="dq-measure-hint">
            <span className="pill neutral">Phase {measureMeta[selectedMeasureId].phase ?? '-'}</span>
            <span>{measureMeta[selectedMeasureId].description || 'Measure-specific DQ checks enabled.'}</span>
          </div>
        )}
        <details className="dq-advanced-rerun">
          <summary>Advanced rerun (optional)</summary>
          <p className="helper-text" style={{ marginTop: '6px' }}>
            Normal flow is automatic after ingestion completion. Use manual rerun only after rule/mapping updates
            or for targeted operational re-validation.
          </p>
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: '8px' }}>
            <button className="secondary-button" onClick={handleRunChecks} disabled={running || !selectedBatchId}>
              {running ? 'Running…' : 'Run checks now'}
            </button>
          </div>
        </details>
        {selectedRunMeasureScope && selectedMeasureId === ALL_MEASURES && (
          <div className="dq-scope-note">
            Selected DQ run <strong>#{selectedRunId}</strong> was executed for measure <strong>{selectedRunMeasureScope}</strong> only.
            To view all measures, click <strong>Run checks</strong> while measure is set to <strong>All measures</strong>.
          </div>
        )}
        {selectedRunMeasureScope && selectedMeasureId !== ALL_MEASURES && selectedMeasureId !== selectedRunMeasureScope && (
          <div className="dq-scope-note">
            Selected DQ run <strong>#{selectedRunId}</strong> contains measure scope <strong>{selectedRunMeasureScope}</strong>.
            Current filter is <strong>{selectedMeasureId}</strong>, so results can appear empty or partial.
          </div>
        )}
        {error && <p className="helper-text" style={{ color: '#b94b45' }}>{error}</p>}
      </section>

      <div className="workspace-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">KPI</p>
              <h2>Summary</h2>
            </div>
          </div>
          {summary?.dq_run && (
            <p className="dq-kpi-context">
              Showing metrics for Batch <strong>{summary.dq_run.batch_id || '-'}</strong>, Run <strong>#{summary.dq_run.id}</strong>
              {selectedRunMeasureScope ? (
                <> · Scope <strong>{selectedRunMeasureScope}</strong></>
              ) : (
                <> · Scope <strong>All measures</strong></>
              )}
            </p>
          )}
          {summary?.kpis ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
              <Kpi title="Total checks" value={displayedKpis.total_checks} />
              <Kpi title="Passed checks" value={displayedKpis.passed_checks} />
              <Kpi title="Failed checks" value={displayedKpis.failed_checks} />
              <Kpi title="Anomalies raised" value={displayedKpis.anomalies_raised} />
            </div>
          ) : (
            <p className="helper-text">{loading ? 'Loading summary...' : 'No DQ run selected.'}</p>
          )}
          {summary?.severity_counts && (
            <SeverityProgressBar severityCounts={displayedKpis.severity_counts} />
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Checks</p>
              <h2>Latest check outcomes</h2>
            </div>
          </div>
          <div style={{ maxHeight: '240px', overflow: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', tableLayout: 'fixed' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #e4ebef' }}>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Check</th>
                  <th style={{ textAlign: 'left', padding: '8px' }}>Status</th>
                  <th style={{ textAlign: 'right', padding: '8px' }}>Metric</th>
                </tr>
              </thead>
              <tbody>
                {filteredChecks.slice(0, 20).map((check) => (
                  <tr key={check.id} style={{ borderBottom: '1px solid #f0f3f5' }}>
                    <td style={{ padding: '8px', wordBreak: 'break-word' }}>{check.check_name}</td>
                    <td style={{ padding: '8px' }}>
                      <span className={`pill ${check.status === 'passed' ? 'ok' : check.status === 'failed' ? 'bad' : 'neutral'}`}>
                        {check.status}
                      </span>
                    </td>
                    <td style={{ padding: '8px', textAlign: 'right' }}>{Number(check.metric_value || 0).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="panel" style={{ marginTop: '18px' }}>
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Queue</p>
            <h2>Anomalies</h2>
          </div>
        </div>
        <div className="dq-anomaly-list">
          {filteredAnomalies.length === 0 && <p className="helper-text">No anomalies for selected filters.</p>}
          {filteredAnomalies.map((anomaly) => {
            const insight = buildAnomalyInsight(anomaly);
            return (
              <article key={anomaly.id} className="dq-anomaly-card">
                <div className="dq-anomaly-header">
                  <span className={`pill ${severityPillClass(anomaly.severity)}`}>{anomaly.severity || 'unknown'}</span>
                  <strong>{insight.title}</strong>
                  <span className="dq-meta">Resource: {deriveResourceLabel(anomaly)}</span>
                  <span className="dq-meta">Detected: {formatTimestamp(anomaly.detected_at)}</span>
                </div>
                <p className="dq-anomaly-summary">{insight.summary}</p>
                <p className="dq-anomaly-impact">{insight.impact}</p>
                <dl className="dq-details-grid">
                  {insight.keyFacts.map(([key, value]) => (
                    <React.Fragment key={`${anomaly.id}-${key}`}>
                      <dt>{key}</dt>
                      <dd className="dq-number">{value}</dd>
                    </React.Fragment>
                  ))}
                </dl>
                <details className="dq-raw-details">
                  <summary>View raw anomaly payload</summary>
                  <pre>{JSON.stringify(anomaly.details || {}, null, 2)}</pre>
                </details>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function severityPillClass(severity) {
  const value = (severity || '').toLowerCase();
  if (value === 'critical' || value === 'high') return 'bad';
  if (value === 'medium') return 'warn';
  return 'neutral';
}

function formatTimestamp(value) {
  if (!value) return '-';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function humanizeToken(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

function buildAnomalyInsight(anomaly) {
  const type = anomaly?.anomaly_type || 'unknown_issue';
  const details = anomaly?.details || {};
  const measureId = details.measure_id || 'N/A';
  const base = {
    title: humanizeToken(type),
    summary: `Measure ${measureId}: anomaly detected.`,
    impact: 'Review this issue before finalizing DQ result exports.',
    keyFacts: [
      ['Measure', measureId],
      ['Anomaly type', humanizeToken(type)],
    ],
  };

  if (type === 'measure_evidence_missing') {
    const missing = (details.evidence_checks || [])
      .filter((item) => (item?.row_count || 0) === 0)
      .map((item) => item.resource_type)
      .filter(Boolean);
    return {
      title: 'Missing clinical evidence for measure',
      summary: `No required evidence found for ${measureId}.`,
      impact: 'This usually means required clinical source records did not ingest or code mapping is incomplete.',
      keyFacts: [
        ['Measure', measureId],
        ['Evidence mode', details.clinical_evidence_mode || 'any'],
        ['Missing sources', missing.length ? missing.join(', ') : 'None detected'],
      ],
    };
  }

  if (type === 'measure_low_patient_coverage') {
    return {
      title: 'Low patient coverage for measure',
      summary: `Measure ${measureId} has insufficient distinct patients with evidence.`,
      impact: 'Population may be too small for reliable trend/anomaly conclusions.',
      keyFacts: [
        ['Measure', measureId],
        ['Actual patients', details.max_patient_count ?? 0],
        ['Required minimum', details.min_distinct_patients_with_evidence ?? 0],
      ],
    };
  }

  if (type === 'measure_observation_missing') {
    return {
      title: 'Expected observation data missing',
      summary: `Required observation codes are missing for ${measureId}.`,
      impact: 'Numerator/clinical result quality checks cannot be trusted until observation feeds are fixed.',
      keyFacts: [
        ['Measure', measureId],
        ['Expected codes', (details.observation_codes || []).join(', ') || '-'],
      ],
    };
  }

  if (type === 'mapping_error' && details.reason === 'missing_or_invalid_unit') {
    return {
      title: 'Observation unit mismatch',
      summary: `${measureId} value has invalid unit for clinical interpretation.`,
      impact: 'Threshold checks may fail incorrectly when units are inconsistent.',
      keyFacts: [
        ['Measure', measureId],
        ['Code', details.code || '-'],
        ['Expected unit', details.expected_unit || '-'],
        ['Actual unit', details.actual_unit || '-'],
      ],
    };
  }

  if (type === 'mapping_error' && details.reason === 'value_out_of_expected_range') {
    return {
      title: 'Observation value out of expected range',
      summary: `${measureId} has outlier/non-numeric observation value.`,
      impact: 'Could indicate source data quality issue or mapping/parsing defect.',
      keyFacts: [
        ['Measure', measureId],
        ['Code', details.code || '-'],
        ['Actual value', details.actual_value ?? details.raw_value ?? '-'],
        ['Expected range', `${details.min_expected ?? '-'} to ${details.max_expected ?? '-'}`],
      ],
    };
  }

  if (type === 'mapping_error_rate') {
    return {
      title: 'High mapping error rate',
      summary: 'Validation errors exceeded allowed threshold.',
      impact: 'Ingestion quality is degraded; downstream analytics may be unreliable.',
      keyFacts: [
        ['Error count', details.error_count ?? 0],
        ['Total resources', details.total_resources ?? 0],
        ['Error rate', details.error_rate ?? '-'],
      ],
    };
  }

  if (type === 'coverage_gap_violation') {
    return {
      title: 'Coverage gap threshold violation',
      summary: 'Detected enrollment gaps beyond configured limit.',
      impact: 'Denominator eligibility and continuity-based measures can be distorted.',
      keyFacts: [
        ['Violation count', details.violation_count ?? 0],
        ['Gap threshold (days)', details.threshold_gap_days ?? 45],
      ],
    };
  }

  if (type === 'required_feed_missing') {
    return {
      title: 'Required feed resource missing',
      summary: `No records found for required resource type ${details.resource_type || anomaly.resource_type || '-'}.`,
      impact: 'Measure calculations relying on this resource cannot run correctly.',
      keyFacts: [
        ['Resource type', details.resource_type || anomaly.resource_type || '-'],
        ['Record count', details.record_count ?? 0],
      ],
    };
  }

  if (type === 'feed_dropout' || type === 'volume_drop' || type === 'volume_spike') {
    return {
      title: `Feed ${humanizeToken(type)}`,
      summary: `Current volume deviated sharply from historical baseline for ${anomaly.resource_type || 'resource feed'}.`,
      impact: 'Potential source extract issue, partial load, or duplicate burst.',
      keyFacts: [
        ['Current count', details.current_count ?? 0],
        ['Baseline avg', details.baseline_avg_count ?? '-'],
        ['Drop %', details.drop_pct ?? 0],
        ['Spike %', details.spike_pct ?? 0],
      ],
    };
  }

  return base;
}

function deriveResourceLabel(anomaly) {
  if (anomaly?.resource_type) return anomaly.resource_type;
  const details = anomaly?.details || {};
  if (Array.isArray(details.evidence_checks) && details.evidence_checks.length > 0) {
    const missing = details.evidence_checks
      .filter((item) => (item?.row_count || 0) === 0)
      .map((item) => item?.resource_type)
      .filter(Boolean);
    if (missing.length > 0) return `${missing.join(', ')} (missing)`;
    const available = details.evidence_checks
      .map((item) => item?.resource_type)
      .filter(Boolean);
    if (available.length > 0) return available.join(', ');
  }
  if (details.observation_codes) return 'Observation';
  if (anomaly?.anomaly_type === 'mapping_error_rate') return 'Ingestion mapping';
  return 'Multiple resources';
}

function SeverityProgressBar({ severityCounts }) {
  const severityOrder = ['critical', 'high', 'medium', 'low'];
  const allKeys = Object.keys(severityCounts || {});
  const ordered = [
    ...severityOrder.filter((key) => allKeys.includes(key)),
    ...allKeys.filter((key) => !severityOrder.includes(key)).sort(),
  ];
  const total = ordered.reduce((sum, key) => sum + Number(severityCounts?.[key] || 0), 0);
  if (total === 0) return null;

  const segments = ordered
    .map((key) => ({ key, count: Number(severityCounts?.[key] || 0) }))
    .filter((item) => item.count > 0);

  return (
    <div className="dq-severity-card">
      <p className="eyebrow" style={{ marginBottom: '8px' }}>Severity distribution</p>
      <div className="dq-severity-track" role="img" aria-label="Severity distribution bar">
        {segments.map((segment) => (
          <div
            key={segment.key}
            className="dq-severity-segment"
            style={{ width: `${(segment.count / total) * 100}%`, background: severityColor(segment.key) }}
            title={`${humanizeToken(segment.key)}: ${segment.count}`}
          />
        ))}
      </div>
      <div className="dq-severity-legend">
        {segments.map((segment) => (
          <div key={`legend-${segment.key}`} className="dq-severity-item">
            <span className="dq-severity-dot" style={{ background: severityColor(segment.key) }} />
            <span>{humanizeToken(segment.key)}</span>
            <strong className="dq-number">{segment.count}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function severityColor(severity) {
  const key = String(severity || '').toLowerCase();
  if (key === 'critical') return '#c44536';
  if (key === 'high') return '#e17055';
  if (key === 'medium') return '#f5a623';
  if (key === 'low') return '#00a896';
  if (key === 'info') return '#3b82f6';
  if (key === 'warning') return '#f59e0b';
  if (key === 'unknown') return '#8b98a2';
  return '#6c5ce7';
}

function Kpi({ title, value }) {
  return (
    <div style={{ border: '1px solid #e4ebef', borderRadius: '6px', padding: '12px' }}>
      <div style={{ color: '#7a8a97', fontSize: '11px', marginBottom: '6px' }}>{title}</div>
      <div className="dq-number" style={{ color: '#087f7d', fontSize: '24px', fontWeight: '700' }}>{value ?? 0}</div>
    </div>
  );
}
