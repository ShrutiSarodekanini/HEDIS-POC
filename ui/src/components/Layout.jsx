import React, { useCallback, useEffect, useState } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import { dq } from '../services/api';
import { INGESTION_ACTIVITY_EVENT, INGESTION_ACTIVITY_KEY, isIngestionActive } from '../services/ingestionState';

export default function Layout() {
  const location = useLocation();
  const [dqEnabled, setDqEnabled] = useState(false);
  const [ingestionActive, setIngestionActive] = useState(() => isIngestionActive());

  const isActive = (path) => location.pathname === path;
  const dqClickable = dqEnabled && !ingestionActive;

  const loadDqAvailability = useCallback(async () => {
    try {
      const response = await dq.getBatches(50);
      const completed = (response?.batches || []).some((batch) => {
        const status = String(batch?.status || '').toLowerCase();
        return status === 'completed' || status === 'partially_completed';
      });
      setDqEnabled(completed);
    } catch {
      setDqEnabled(false);
    }
  }, []);

  useEffect(() => {
    loadDqAvailability();
  }, [loadDqAvailability]);

  useEffect(() => {
    if (!ingestionActive) {
      loadDqAvailability();
    }
  }, [ingestionActive, loadDqAvailability]);

  useEffect(() => {
    const syncIngestionActivity = () => {
      setIngestionActive(isIngestionActive());
    };
    const onStorage = (event) => {
      if (!event || event.key === INGESTION_ACTIVITY_KEY) {
        syncIngestionActivity();
      }
    };

    window.addEventListener(INGESTION_ACTIVITY_EVENT, syncIngestionActivity);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(INGESTION_ACTIVITY_EVENT, syncIngestionActivity);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  useEffect(() => {
    let intervalId = null;
    const shouldPoll = location.pathname === '/run' && ingestionActive;
    if (shouldPoll) {
      loadDqAvailability();
      intervalId = setInterval(loadDqAvailability, 5000);
    }
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, [location.pathname, ingestionActive, loadDqAvailability]);

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
          {dqClickable ? (
            <Link to="/dq" className={`nav-item ${isActive('/dq') ? 'active' : ''}`}>
              <span className="nav-icon">⚑</span>DQ &amp; AD
            </Link>
          ) : (
            <span
              className="nav-item disabled"
              title={ingestionActive ? 'DQ & AD is available after ingestion completes.' : 'Complete one ingestion run to enable DQ & AD'}
            >
              <span className="nav-icon">⚑</span>DQ &amp; AD
            </span>
          )}
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
