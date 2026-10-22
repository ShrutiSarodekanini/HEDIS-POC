import React from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';

export default function Layout() {
  const location = useLocation();

  const isActive = (path) => location.pathname === path;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">+</span>
          <span>HEDIS Ops</span>
        </div>
        <nav aria-label="Main navigation">
          <Link to="/run" className={`nav-item ${isActive('/run') ? 'active' : ''}`}>
            <span className="nav-icon">↻</span>Run Ingestion
          </Link>
          <Link to="/priority" className={`nav-item ${isActive('/priority') ? 'active' : ''}`}>
            <span className="nav-icon">≡</span>Prioritization
          </Link>
          <Link to="/evidence" className={`nav-item ${isActive('/evidence') ? 'active' : ''}`}>
            <span className="nav-icon">▣</span>Evidence Review
          </Link>
        </nav>
        <div className="sidebar-footer">
          Layer 1 of 8<br /><span>CSV to FHIR data pipeline</span>
        </div>
      </aside>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
