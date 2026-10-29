import React, { useEffect, useState, useRef } from 'react';
import { api } from '../services/api';
import { setIngestionActivity } from '../services/ingestionState';

export default function RunIngestion() {
  const [sourceSystem, setSourceSystem] = useState('Synthea CSV');
  const [sourceDescription, setSourceDescription] = useState('Synthea - 30 California seed=1234');
  const [inputDir, setInputDir] = useState('');
  const [jobId, setJobId] = useState('');
  const [inspectResult, setInspectResult] = useState('');
  const [jobStatus, setJobStatus] = useState(null);
  const [runBadge, setRunBadge] = useState('Ready');
  const [uploadStatus, setUploadStatus] = useState('');
  const [detectedAdapter, setDetectedAdapter] = useState('');
  const [availableAdapters, setAvailableAdapters] = useState([]);
  const [selectedAdapter, setSelectedAdapter] = useState('');
  const [uploadedFiles, setUploadedFiles] = useState(null);  // Track if files were uploaded
  const fileInputRef = useRef(null);

  // Friendly display names for adapters
  const adapterDisplayNames = {
    'synthea_csv': 'CSV',
    'ccda_hie': 'C-CDA Documents',
    'ehr_fhir_r4': 'FHIR JSON',
    'claims_837': 'Claims (837)',
    'ncpdp_pharmacy': 'NCPDP Pharmacy'
  };

  useEffect(() => {
    const restoreLatestJob = async () => {
      try {
        const response = await api('/api/jobs');
        const latestJob = response.jobs?.[0];
        if (!latestJob) return;

        const status = await api(`/api/jobs/${latestJob.id}`);
        setJobId(latestJob.id);
        setInputDir(latestJob.input_dir || '');
        setJobStatus(status);
        setRunBadge(
          status.status === 'completed'
            ? 'Completed'
            : status.status === 'partially_completed'
              ? 'Partially completed'
              : status.status
        );
        const restoredStatus = String(status.status || '').toLowerCase();
        if (['completed', 'partially_completed', 'failed', 'interrupted'].includes(restoredStatus)) {
          setIngestionActivity(false);
        } else {
          setIngestionActivity(true, latestJob.id);
        }
      } catch {
        // The page remains ready for a new ingestion if no previous job is available.
      }
    };

    restoreLatestJob();
    // Set default adapters on mount - will be updated when user uploads files
    setAvailableAdapters(['synthea_csv', 'ccda_hie', 'ehr_fhir_r4', 'claims_837', 'ncpdp_pharmacy']);
  }, []);

  const handlePickFolder = () => {
    fileInputRef.current?.click();
  };

  const handleFolderSelected = async (event) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;

    setUploadStatus(`Uploading ${files.length} file${files.length === 1 ? '' : 's'}...`);
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file, file.name));
      
      // Detect file type from actual files using same patterns as backend
      const fileNames = files.map(f => f.name.toLowerCase());
      const isCcda = fileNames.some(f => f.endsWith('.xml') || f.endsWith('.ccda'));
      const isClaims = fileNames.some(f => f.endsWith('.837'));
      const isNcpdp = fileNames.some(f => f.startsWith('ncpdp') || f.includes('_ncpdp'));
      
      // FHIR resource type patterns: practitioner*.json, patient*.json, bundle.json, etc.
      const fhirPatterns = ['bundle', 'practitioner', 'patient', 'encounter', 'claim', 'medication', 'coverage', 'observation', 'organization', 'provider'];
      const isFhir = fileNames.some(f => 
        (f.endsWith('.json') || f.endsWith('.fhir')) && 
        fhirPatterns.some(pattern => f.includes(pattern))
      );
      
      // Determine which endpoint to call based on file type
      let endpoint = '/api/datasets/upload';  // Default: Synthea CSV
      if (isCcda) endpoint = '/api/datasets/upload-ccda';
      else if (isClaims) endpoint = '/api/datasets/upload-claims';
      else if (isNcpdp) endpoint = '/api/datasets/upload-ncpdp';
      else if (isFhir) endpoint = '/api/datasets/upload-fhir';
      
      const response = await api(endpoint, {
        method: 'POST',
        body: formData,
      });
      
      setInputDir(response.input_dir);
      setUploadedFiles(response.saved_files);  // Track uploaded files
      
      // Capture detected adapter from response - ensure it's explicitly set
      const detected = response.detected_adapter || 'synthea_csv';
      const adapters = response.available_adapters || ['synthea_csv', 'ccda_hie', 'ehr_fhir_r4', 'claims_837', 'ncpdp_pharmacy'];
      
      setDetectedAdapter(detected);
      setSelectedAdapter(detected);  // Always set, never leave empty
      setAvailableAdapters(adapters);
      
      setUploadStatus(`✓ Uploaded ${response.saved_files.length} file${response.saved_files.length === 1 ? '' : 's'}.`);
    } catch (error) {
      setUploadStatus(`✗ Upload failed: ${error.message}`);
    } finally {
      event.target.value = '';
    }
  };

  const handleInspect = async () => {
    try {
      const path = inputDir.trim() || '/data/synthea/output/fhir';
      const response = await api(`/api/datasets/inspect?input_dir=${encodeURIComponent(path)}`);
      setInspectResult(JSON.stringify(response, null, 2));
    } catch (error) {
      setInspectResult(`Error: ${error.message}`);
    }
  };

  const handleRun = async () => {
    try {
      if (!inputDir.trim()) {
        alert('Please upload files or enter a data path');
        return;
      }
      if (!selectedAdapter) {
        alert('Please select or auto-detect an adapter');
        return;
      }
      
      setRunBadge('Running');
      const payload = {
        source_system: sourceSystem,
        source_description: sourceDescription,
        input_path: inputDir.trim()
      };
      
      const response = await api(`/api/jobs/start?input_dir=${encodeURIComponent(payload.input_path)}&adapter_name=${selectedAdapter}`, {
        method: 'POST'
      });
      setJobId(response.job_id);
      setJobStatus(response);
      setRunBadge('Running');
      setIngestionActivity(true, response.job_id);
      pollJobStatus(response.job_id);
    } catch (error) {
      setRunBadge('Error');
      setInspectResult(`Error: ${error.message}`);
      setIngestionActivity(false);
    }
  };

  const pollJobStatus = async (id) => {
    let attempts = 0;
    const maxAttempts = 360; // 30 minutes with 5-second intervals
    
    const poll = async () => {
      if (attempts >= maxAttempts) {
        setRunBadge('Timeout');
        setIngestionActivity(false);
        return;
      }
      
      try {
        const response = await api(`/api/jobs/${id}`);
        setJobStatus(response);
        
        if (['completed', 'partially_completed', 'failed'].includes(response.status)) {
          setRunBadge(response.status === 'completed' ? 'Completed' : response.status === 'partially_completed' ? 'Partially completed' : 'Failed');
          setIngestionActivity(false);
        } else {
          setRunBadge(response.status);
          setIngestionActivity(true, id);
          attempts++;
          setTimeout(poll, 5000);
        }
      } catch (error) {
        attempts++;
        setTimeout(poll, 5000);
      }
    };
    
    poll();
  };

  const runtimeStatus = jobStatus?.runtime || jobStatus;
  const resourceProgress = runtimeStatus?.resource_progress || jobStatus?.resources || {};
  const resourceEntries = Object.entries(resourceProgress);
  const totalResources = resourceEntries.reduce((total, [, counts]) => total + (counts.total || 0), 0);
  const processedResources = resourceEntries.reduce((total, [, counts]) => total + (counts.processed || counts.loaded || 0), 0);
  const progress = totalResources ? Math.round((processedResources / totalResources) * 100) : 0;
  const stageElapsed = Object.fromEntries(
    (runtimeStatus?.stages || []).map((stage) => [stage.name, stage.elapsed_seconds])
  );
  const pipelineTimings = jobStatus?.summary?.timings || {};

  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Layer 1 of 8</p>
          <h1>Run Ingestion</h1>
          <p className="subtitle">Load Synthea FHIR export and normalize it into Layer 1 data model</p>
        </div>
        <div className="header-status"><span className="status-dot"></span>Pipeline ready</div>
      </header>

      <div className="workspace-grid">
        <section className="panel new-run">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">New ingestion run</p>
              <h2>Configure source</h2>
            </div>
            <span className="step-chip">01 / 08</span>
          </div>

          <label>Source description</label>
          <input
            type="text"
            value={sourceDescription}
            onChange={(e) => setSourceDescription(e.target.value)}
            placeholder="e.g., Synthea 30 California, FHIR bundle, C-CDA export"
          />

          {uploadedFiles ? (
            <>
              <p style={{ fontSize: '12px', color: '#32947a', marginBottom: '8px' }}>
                ✓ Uploaded: {uploadedFiles.join(', ')}
              </p>
              <div style={{ 
                padding: '12px', 
                marginBottom: '15px', 
                backgroundColor: '#f0f8ff', 
                border: '1px solid #4da6ff', 
                borderRadius: '4px' 
              }}>
                <p style={{ margin: '0 0 8px 0', fontSize: '13px', fontWeight: 600, color: '#0052cc' }}>
                  Auto-detected: <strong>{adapterDisplayNames[detectedAdapter] || detectedAdapter}</strong>
                </p>
                <label style={{ fontSize: '12px', display: 'block', marginBottom: '6px', color: '#626f86' }}>
                  Change if needed:
                </label>
                <select 
                  key={detectedAdapter}
                  value={selectedAdapter || detectedAdapter}
                  onChange={(e) => setSelectedAdapter(e.target.value)}
                  style={{ width: '100%', padding: '6px', fontSize: '12px' }}
                >
                  {availableAdapters.map((adapter) => (
                    <option key={adapter} value={adapter}>{adapterDisplayNames[adapter] || adapter}</option>
                  ))}
                </select>
              </div>
            </>
          ) : (
            <>
              <label>Data source</label>
              <input
                ref={fileInputRef}
                type="file"
                webkitdirectory="true"
                directory="true"
                multiple
                hidden
                onChange={handleFolderSelected}
              />

              <div className="drop-zone">
                <span className="upload-icon">↑</span>
                <strong>Upload your data folder or <button className="inline-button" type="button" onClick={() => fileInputRef.current?.click()}>browse</button></strong>
                <small>Auto-detects format (Synthea CSV, C-CDA, FHIR, Claims 837, NCPDP)</small>
              </div>

              <label style={{ marginTop: '15px' }}>OR: Use direct path</label>
              <input
                type="text"
                value={inputDir}
                onChange={(e) => setInputDir(e.target.value)}
                placeholder="C:\data\synthea or /data/fhir"
              />
              
              {inputDir && (
                <div style={{ 
                  padding: '12px', 
                  marginTop: '12px',
                  marginBottom: '15px', 
                  backgroundColor: '#fff3cd', 
                  border: '1px solid #ffc107', 
                  borderRadius: '4px' 
                }}>
                  <p style={{ margin: '0 0 8px 0', fontSize: '13px', fontWeight: 600, color: '#856404' }}>
                    Select adapter:
                  </p>
                  <select 
                    value={selectedAdapter} 
                    onChange={(e) => setSelectedAdapter(e.target.value)}
                    style={{ width: '100%', padding: '6px', fontSize: '12px' }}
                  >
                    <option value="">-- Choose adapter --</option>
                    {availableAdapters.map((adapter) => (
                      <option key={adapter} value={adapter}>{adapterDisplayNames[adapter] || adapter}</option>
                    ))}
                  </select>
                </div>
              )}
            </>
          )}

          {uploadStatus && <p className="helper-text">{uploadStatus}</p>}

          <div className="action-row">
            <button className="primary-button" onClick={handleRun}>Run ingestion</button>
            <button className="secondary-button" onClick={handleInspect}>Inspect dataset</button>
          </div>

          <p className="helper-text">For large datasets, enter the folder path and run directly. Otherwise select a folder.</p>
          <pre className="mono" style={{ display: inspectResult ? 'block' : 'none' }}>{inspectResult}</pre>
        </section>

        <section className="panel live-status">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Live run status</p>
              <h2 id="liveJobTitle">{jobStatus ? `Job: ${jobId}` : 'Waiting for a run'}</h2>
            </div>
            <span className={`running-badge ${runBadge === 'Completed' ? 'badge-completed' : runBadge === 'Partially completed' ? 'badge-partially_completed' : runBadge === 'Failed' || runBadge === 'Error' ? 'badge-failed' : ''}`} id="runBadge">{runBadge}</span>
          </div>

          {jobStatus && (
            <div style={{ padding: '15px 0' }}>
              <p className="pipeline-section-label">Resource Pipeline</p>
              <ul className="stage-table">
                {resourceEntries.map(([resource, counts]) => {
                  const status = counts.status || (counts.processed >= (counts.total || 0) ? 'completed' : 'queued');
                  return (
                    <li key={resource}>
                      <span>{resource}</span>
                      <span>{counts.processed || counts.loaded || 0}/{counts.total || 0}</span>
                      <span className={`status-${status}`}>{status === 'queued' ? 'Queued' : status === 'loading' ? 'Loading' : 'completed' ? 'completed' : status}</span>
                    </li>
                  );
                })}
              </ul>
              
              {totalResources > 0 && (
                <>
                  <div className="progress-track">
                    <span style={{ width: `${progress}%` }}></span>
                  </div>
                  <div className="progress-meta">
                    <span>{processedResources} / {totalResources} resources processed</span>
                    <span>{progress}%</span>
                  </div>
                </>
              )}
              <div className="timing-summary">
                <p className="muted-label">Processing time</p>
                <div><span>CSV reading</span><strong>{pipelineTimings.csv_reading_seconds ?? stageElapsed.csv_input ?? '--'}s</strong></div>
                <div><span>Mapping</span><strong>{pipelineTimings.mapping_seconds ?? '--'}s</strong></div>
                <div><span>FHIR validation</span><strong>{pipelineTimings.validation_seconds ?? '--'}s</strong></div>
                <div><span>Database insertion</span><strong>{pipelineTimings.database_insertion_seconds ?? (['completed', 'partially_completed'].includes(jobStatus.status) ? 'Completed' : 'Pending')}</strong></div>
              </div>
              <p className="helper-text">Validated resources are stored automatically during ingestion.</p>

              {jobStatus?.completeness_scorecard && (
                <div className="completeness-panel">
                  <h3 style={{ margin: '24px 0 10px', fontSize: '14px', fontWeight: 700, color: '#304957' }}>Feed completeness scorecard</h3>
                  <p className="muted-label" style={{ margin: '0 0 15px' }}>v_feed_completeness_latest · last completed batch</p>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', color: '#637680' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid #e4ebef', color: '#a0acb2', textTransform: 'uppercase', fontSize: '9px', fontWeight: 700 }}>
                          <th style={{ padding: '9px 8px', textAlign: 'left' }}>Resource Type</th>
                          <th style={{ padding: '9px 8px', textAlign: 'left' }}>Record Count</th>
                          <th style={{ padding: '9px 8px', textAlign: 'left' }}>Distinct Patients</th>
                          <th style={{ padding: '9px 8px', textAlign: 'left' }}>Measure-Relevant</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(jobStatus.completeness_scorecard).map(([resourceType, data]) => (
                          <tr key={resourceType} style={{ borderBottom: '1px solid #f0f3f5' }}>
                            <td style={{ padding: '9px 8px' }}>{resourceType}</td>
                            <td style={{ padding: '9px 8px' }}>{data.record_count ?? data.count ?? '--'}</td>
                            <td style={{ padding: '9px 8px' }}>{data.distinct_patients ?? '--'}</td>
                            <td style={{ padding: '9px 8px', color: data.measure_relevant ? '#32947a' : '#9ba8af' }}>
                              {data.measure_relevant ? 'Yes' : 'No'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
