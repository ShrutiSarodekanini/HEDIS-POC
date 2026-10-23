import { Navigate, Route, Routes } from 'react-router-dom';
import { RunEvaluationWorkspace } from '../features/evaluations/RunEvaluationWorkspace';
import StarCalculationApp from '../star-calculation/App.jsx';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<RunEvaluationWorkspace />} />
      <Route path="/star/*" element={<StarCalculationApp />} />
      <Route path="/star-calculation/*" element={<StarCalculationApp />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
