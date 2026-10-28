import React, { useState, useEffect } from 'react';
import { api } from '../services/api';

export default function CoverageReport() {
  const [coverages, setCoverages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [selectedPayer, setSelectedPayer] = useState('All');
  const [selectedCompliance, setSelectedCompliance] = useState('All');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [payers, setPayers] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchCoverageData();
  }, [selectedPayer, selectedCompliance, startDate, endDate]);

  const fetchCoverageData = async () => {
    try {
      setLoading(true);
      setError('');

      // Build query parameters
      const params = new URLSearchParams();
      if (selectedPayer !== 'All') params.append('payer', selectedPayer);
      if (selectedCompliance !== 'All') params.append('compliance', selectedCompliance);
      if (startDate) params.append('start_date', startDate);
      if (endDate) params.append('end_date', endDate);

      const response = await api(`/api/coverage/report?${params}`);
      
      setCoverages(response.coverage_records || []);
      setStats(response.summary || null);
      
      // Extract unique payers
      const uniquePayers = [...new Set(response.coverage_records?.map(c => c.payer) || [])];
      setPayers(['All', ...uniquePayers.filter(p => p)]);
    } catch (error) {
      setError(`Error loading coverage data: ${error.message}`);
      setCoverages([]);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      });
    } catch {
      return dateStr;
    }
  };

  const getComplianceIcon = (compliant) => compliant ? '✅' : '❌';
  const getComplianceLabel = (compliant) => compliant ? 'Compliant' : 'Violation';

  return (
    <>
      {/* Summary Stats */}
      {stats && (
        <div style={{ marginBottom: '20px' }}>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Coverage Statistics</p>
                <h2>Summary</h2>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '15px', padding: '20px' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#087f7d' }}>
                  {stats.total_covered || 0}
                </div>
                <div style={{ fontSize: '12px', color: '#7a8a97', marginTop: '5px' }}>Total Covered</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#32947a' }}>
                  {stats.continuous_enrollment || 0}
                </div>
                <div style={{ fontSize: '12px', color: '#7a8a97', marginTop: '5px' }}>Continuous</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#b94b45' }}>
                  {stats.hedis_violations || 0}
                </div>
                <div style={{ fontSize: '12px', color: '#7a8a97', marginTop: '5px' }}>Violations</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#e6a23c' }}>
                  {stats.compliance_rate ? `${stats.compliance_rate}%` : 'N/A'}
                </div>
                <div style={{ fontSize: '12px', color: '#7a8a97', marginTop: '5px' }}>Compliance</div>
              </div>
            </div>
          </section>
        </div>
      )}

      {/* Filters */}
      <div style={{ marginBottom: '20px' }}>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Filters</p>
              <h2>Coverage Data</h2>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '15px', padding: '15px 0' }}>
            <div>
              <label>Payer</label>
              <select value={selectedPayer} onChange={(e) => setSelectedPayer(e.target.value)}>
                {payers.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div>
              <label>Compliance Status</label>
              <select value={selectedCompliance} onChange={(e) => setSelectedCompliance(e.target.value)}>
                <option value="All">All</option>
                <option value="Compliant">Compliant</option>
                <option value="Violation">Violation</option>
              </select>
            </div>
            <div>
              <label>Start Date</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div>
              <label>End Date</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          </div>
        </section>
      </div>

      {/* Patient List with Gaps */}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Coverage Details</p>
            <h2>Patient Enrollment Records</h2>
          </div>
          {loading && <span className="running-badge">Loading...</span>}
        </div>

        {error && (
          <div className="alert-banner">
            <div className="alert-icon">!</div>
            <strong>{error}</strong>
          </div>
        )}

        {!loading && coverages.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '11px',
              lineHeight: '1.6'
            }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #e4ebef', paddingBottom: '10px' }}>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Patient ID</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Payer</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Coverage Period</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Gap Detected</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Gap Days</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Gap Period</th>
                  <th style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold', color: '#7a8a97' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {coverages.map((cov, idx) => (
                  <tr key={idx} style={{ borderBottom: '1px solid #f0f3f5' }}>
                    <td style={{ padding: '10px', color: '#087f7d', fontWeight: 'bold' }}>
                      {cov.patient_id}
                    </td>
                    <td style={{ padding: '10px', color: '#637680' }}>
                      {cov.payer || '-'}
                    </td>
                    <td style={{ padding: '10px', color: '#637680' }}>
                      {formatDate(cov.period_start)} - {formatDate(cov.period_end)}
                    </td>
                    <td style={{ padding: '10px', textAlign: 'center' }}>
                      {cov.gap_detected ? '✓' : '-'}
                    </td>
                    <td style={{ padding: '10px', color: '#637680' }}>
                      {cov.gap_days ? `${cov.gap_days}d` : '-'}
                    </td>
                    <td style={{ padding: '10px', color: '#637680', fontSize: '10px' }}>
                      {cov.gap_start_date && cov.gap_end_date 
                        ? `${formatDate(cov.gap_start_date)} - ${formatDate(cov.gap_end_date)}`
                        : '-'}
                    </td>
                    <td style={{ padding: '10px' }}>
                      <span style={{
                        padding: '4px 8px',
                        borderRadius: '10px',
                        fontSize: '9px',
                        fontWeight: 'bold',
                        ...(cov.continuous_enrollment_compliant
                          ? { color: '#2f8b72', background: '#e7f5ef' }
                          : { color: '#b94b45', background: '#fff0ef' })
                      }}>
                        {getComplianceIcon(cov.continuous_enrollment_compliant)} {getComplianceLabel(cov.continuous_enrollment_compliant)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : !loading && (
          <div style={{ padding: '40px', textAlign: 'center', color: '#9aa8b1' }}>
            <p>No coverage records found. Upload data via Run Ingestion first.</p>
          </div>
        )}
      </section>
    </>
  );
}
