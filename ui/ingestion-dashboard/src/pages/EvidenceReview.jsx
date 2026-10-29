import React from 'react';

export default function EvidenceReview() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Documentation Review</p>
          <h1>Evidence Review</h1>
          <p className="subtitle">Review source documentation and validation results</p>
        </div>
        <div className="header-status"><span className="status-dot"></span>Ready</div>
      </header>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">FHIR Resource Validation</p>
            <h2>Evidence Documentation</h2>
          </div>
        </div>

        <div style={{ padding: '40px', textAlign: 'center', color: '#9aa8b1' }}>
          <p>Review FHIR resources, validation issues, and source documentation.</p>
          <p style={{ fontSize: '12px', marginTop: '20px' }}>Coming soon: Detailed evidence view and validation reports</p>
        </div>
      </section>
    </>
  );
}
