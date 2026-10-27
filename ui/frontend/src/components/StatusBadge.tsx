import clsx from 'clsx';
import type { EvaluationStatus, MeasureExecutionStatus } from '../types/api';

type BadgeStatus = EvaluationStatus | MeasureExecutionStatus | 'ok' | 'degraded';

interface StatusBadgeProps {
  status: BadgeStatus;
}

const labels: Record<BadgeStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  unresolved: 'Unresolved',
  ready: 'Ready',
  retired: 'Retired',
  unknown: 'Unknown',
  unavailable: 'Unavailable',
  ok: 'Connected',
  degraded: 'Degraded',
};

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span className={clsx('status-badge', `status-${status}`)}>
      {labels[status] ?? status}
    </span>
  );
}
