import React from 'react';

export default function Prioritization() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Quality Improvement</p>
          <h1>Prioritization</h1>
          <p className="subtitle">Identify and prioritize patients needing intervention</p>
        </div>
        <div className="header-status"><span className="status-dot"></span>Ready</div>
      </header>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Patient Prioritization</p>
            <h2>Intervention Queue</h2>
          </div>
        </div>

        <div style={{ padding: '40px', textAlign: 'center', color: '#9aa8b1' }}>
          <p>Patient prioritization based on coverage gaps and compliance status.</p>
          <p style={{ fontSize: '12px', marginTop: '20px' }}>Coming soon: Integration with Coverage dashboard results</p>
        </div>
      </section>
    </>
  );
}
