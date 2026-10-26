import React, { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import RunIngestion from './pages/RunIngestion';
import Prioritization from './pages/Prioritization';
import EvidenceReview from './pages/EvidenceReview';
import DataQuality from './pages/DataQuality';
import { INGESTION_ACTIVITY_EVENT, INGESTION_ACTIVITY_KEY, isIngestionActive } from './services/ingestionState';

function DqRouteGuard() {
  const [ingestionActive, setIngestionActive] = useState(() => isIngestionActive());

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

  if (ingestionActive) {
    return <Navigate to="/run" replace />;
  }

  return <DataQuality />;
}

export default function App() {
  return (
    <Router basename={process.env.NODE_ENV === 'production' ? '/ui' : undefined}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/run" replace />} />
          <Route path="/run" element={<RunIngestion />} />
          <Route path="/dq" element={<DqRouteGuard />} />
          <Route path="/priority" element={<Prioritization />} />
          <Route path="/evidence" element={<EvidenceReview />} />
        </Route>
      </Routes>
    </Router>
  );
}
