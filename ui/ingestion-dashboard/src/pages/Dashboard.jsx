import React from 'react';

export default function Dashboard() {
  return (
    <>
      <header className="page-header">
        <div>
          <p className="eyebrow">Overview</p>
          <h1>Dashboard</h1>
          <p className="subtitle">System status and recent activity</p>
        </div>
        <div className="header-status"><span className="status-dot"></span>Pipeline ready</div>
      </header>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>Dashboard</h2>
          </div>
        </div>
        <p className="subtitle">Dashboard content coming soon</p>
      </section>
    </>
  );
}
