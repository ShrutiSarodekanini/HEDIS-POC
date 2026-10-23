import React, { useEffect, useState, useRef } from 'react';
import { api } from '../services/api';

export default function RunIngestion() {
  const [sourceSystem, setSourceSystem] = useState('Synthea CSV');
  const [sourceDescription, setSourceDescription] = useState('Synthea - 30 California seed=1234');
  const [inputDir, setInputDir] = useState('');
  const [jobId, setJobId] = useState('');
  const [inspectResult, setInspectResult] = useState('');
  const [jobStatus, setJobStatus] = useState(null);
  const [runBadge, setRunBadge] = useState('Ready');
  const [uploadStatus, setUploadStatus] = useState('');
  const fileInputRef = useRef(null);

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
      } catch {
        // The page remains ready for a new ingestion if no previous job is available.
      }
    };

    restoreLatestJob();
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
      const isCcda = sourceSystem === 'C-CDA / HIE';
      const response = await api(isCcda ? '/api/datasets/upload-ccda' : '/api/datasets/upload', {
        method: 'POST',
        body: formData,
      });
      setInputDir(response.input_dir);
      setUploadStatus(`Uploaded ${response.saved_files.length} ${isCcda ? 'C-CDA' : 'supported CSV'} file${response.saved_files.length === 1 ? '' : 's'}.`);
    } catch (error) {
      setUploadStatus(`Upload failed: ${error.message}`);
    } finally {
      event.target.value = '';
    }
  };

  const handleInspect = async () => {
    try {
      const path = inputDir.trim() || '/data/synthea/output/fhir';
      const response = await api(`/api/inspect?path=${encodeURIComponent(path)}`);
      setInspectResult(JSON.stringify(response, null, 2));
    } catch (error) {
      setInspectResult(`Error: ${error.message}`);
    }
  };

  const handleRun = async () => {
    try {
      setRunBadge('Running');
      const payload = {
        source_system: sourceSystem,
        source_description: sourceDescription,
        input_path: inputDir.trim() || '/data/synthea/output/fhir'
      };
      const adapterName = sourceSystem === 'EHR FHIR R4'
        ? 'ehr_fhir_r4'
        : sourceSystem === 'C-CDA / HIE'
          ? 'ccda_hie'
          : sourceSystem === 'Claims 837'
            ? 'claims_837'
            : sourceSystem === 'NCPDP Pharmacy'
              ? 'ncpdp_pharmacy'
          : 'synthea_csv';
      const response = await api(`/api/jobs/start?input_dir=${encodeURIComponent(payload.input_path)}&adapter_name=${adapterName}`, {
        method: 'POST'
      });
      setJobId(response.job_id);
      setJobStatus(response);
      setRunBadge('Running');
      pollJobStatus(response.job_id);
    } catch (error) {
      setRunBadge('Error');
      setInspectResult(`Error: ${error.message}`);
    }
  };

  const pollJobStatus = async (id) => {
    let attempts = 0;
    const maxAttempts = 360; // 30 minutes with 5-second intervals
    
    const poll = async () => {
      if (attempts >= maxAttempts) {
        setRunBadge('Timeout');
        return;
      }
      
      try {
        const response = await api(`/api/jobs/${id}`);
        setJobStatus(response);
        
        if (['completed', 'partially_completed', 'failed'].includes(response.status)) {
          setRunBadge(response.status === 'completed' ? 'Completed' : response.status === 'partially_completed' ? 'Partially completed' : 'Failed');
        } else {
          setRunBadge(response.status);
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

          <label>Source system</label>
          <select value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)}>
            <option>Synthea CSV</option>
            <option>FHIR R4 bundle</option>
            <option>EHR FHIR R4</option>
            <option>C-CDA / HIE</option>
            <option>Claims 837</option>
            <option>NCPDP Pharmacy</option>
          </select>

          <label>Source description</label>
          <input
            type="text"
            value={sourceDescription}
            onChange={(e) => setSourceDescription(e.target.value)}
            placeholder="Dataset description"
          />

          <label>Input path</label>
          <input
            type="text"
            value={inputDir}
            onChange={(e) => setInputDir(e.target.value)}
            placeholder={sourceSystem === 'C-CDA / HIE' ? 'C:\\data\\ccda' : '/data/synthea/output/fhir'}
          />

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
            <strong>Select a {sourceSystem === 'C-CDA / HIE' ? 'C-CDA XML folder' : 'Synthea CSV folder'} or <button className="inline-button" type="button" onClick={handlePickFolder}>browse</button></strong>
            <small>Upload starts automatically · {sourceSystem === 'C-CDA / HIE' ? '.xml and .ccda files' : 'Synthea CSV export only'}</small>
          </div>

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
