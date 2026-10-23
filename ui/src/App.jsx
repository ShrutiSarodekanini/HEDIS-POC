import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import RunIngestion from './pages/RunIngestion';
import RunGapCalculation from './pages/RunGapCalculation';
import Prioritization from './pages/Prioritization';
import EvidenceReview from './pages/EvidenceReview';

export default function App() {
  return (
    <Router basename={process.env.NODE_ENV === 'production' ? '/ui' : undefined}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/run" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/run" element={<RunIngestion />} />
          <Route path="/gap-calc" element={<RunGapCalculation />} />
          <Route path="/priority" element={<Prioritization />} />
          <Route path="/evidence" element={<EvidenceReview />} />
        </Route>
      </Routes>
    </Router>
  );
}
