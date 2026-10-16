import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { RunEvaluationWorkspace } from './RunEvaluationWorkspace';
import { renderWithProviders } from '../../test/renderWithProviders';
import type { FqmApiService } from '../../services/fqmApiService';

function createRealService(): FqmApiService {
  return {
    mode: 'real',
    healthCheck: async () => ({
      status: 'ok',
      checkedAt: new Date().toISOString(),
      message: 'Service reachable',
    }),
    listMeasures: async () => ({
      items: [
        {
          id: 'AAB',
          name: null,
          description: null,
          measurementPeriod: null,
          executionStatus: 'ready',
          metadataAvailable: false,
        },
      ],
      page: 1,
      pageSize: 10,
      total: 0,
    }),
    getMeasureDetails: async () => {
      throw new Error('not implemented');
    },
    submitEvaluation: async () => ({
      evaluationId: 'real-run-001',
      status: 'pending',
      submittedAt: new Date().toISOString(),
    }),
    getEvaluationResult: async () => ({
      evaluationId: 'real-run-001',
      status: 'completed',
      measureId: 'AAB',
      submittedAt: new Date().toISOString(),
      populationCounts: {
        initialPopulation: 3,
        denominator: 2,
        numerator: 1,
      },
      patientResults: [
        {
          patientId: 'patient-001',
          status: 'completed',
          populations: {
            numerator: true,
          },
        },
      ],
      warnings: ['Terminology lookup used pre-expanded valuesets.'],
    }),
    listEvaluationHistory: async () => ({
      items: [],
      page: 1,
      pageSize: 10,
      total: 0,
    }),
    listPatientResults: async () => ({
      items: [],
      page: 1,
      pageSize: 10,
      total: 0,
    }),
  };
}

describe('RunEvaluationWorkspace', () => {
  it('shows empty state and disabled run button before inputs', async () => {
    renderWithProviders(<RunEvaluationWorkspace />);

    expect(
      screen.getByText(/no evaluation results yet/i),
    ).toBeVisible();
    expect(
      screen.getByRole('button', { name: /run fqm evaluation/i }),
    ).toBeDisabled();
  });

  it('runs evaluation in real mode and displays returned results', async () => {
    renderWithProviders(<RunEvaluationWorkspace />, {
      config: { mode: 'real' },
      service: createRealService(),
    });

    const user = userEvent.setup();

    expect(await screen.findByText(/auto-detect related measures/i)).toBeVisible();

    const patientFileInput = screen.getByLabelText(/patient bundle upload/i);

    const patientFile = new File(['{"resourceType":"Bundle"}'], 'patient-1.json', {
      type: 'application/json',
    });

    await user.upload(patientFileInput, patientFile);

    await user.click(screen.getByRole('button', { name: /run fqm evaluation/i }));

    expect(await screen.findByText(/execution status/i)).toBeVisible();
    expect(screen.getAllByText(/^AAB$/).length).toBeGreaterThan(0);
    expect(screen.getByText(/patient-level outcomes/i)).toBeVisible();
  });

  it('clears selected files and result output', async () => {
    renderWithProviders(<RunEvaluationWorkspace />, {
      config: { mode: 'real' },
      service: createRealService(),
    });

    const user = userEvent.setup();

    const patientFileInput = screen.getByLabelText(/patient bundle upload/i);

    await user.upload(
      patientFileInput,
      new File(['{"resourceType":"Bundle"}'], 'patient-1.json', {
        type: 'application/json',
      }),
    );

    await user.click(screen.getByRole('button', { name: /run fqm evaluation/i }));
    await waitFor(() => {
      expect(screen.getByText(/execution status/i)).toBeVisible();
    });

    await user.click(screen.getByRole('button', { name: /clear/i }));

    expect(
      screen.getByText(/no evaluation results yet/i),
    ).toBeVisible();
    expect(screen.getByText(/auto-detect related measures/i)).toBeVisible();
  });

  it('preserves duplicate member-measure rows', async () => {
    const service: FqmApiService = {
      mode: 'real',
      healthCheck: async () => ({
        status: 'ok',
        checkedAt: new Date().toISOString(),
      }),
      listMeasures: async () => ({
        items: [
          {
            id: 'AAB',
            name: 'AAB',
            description: null,
            measurementPeriod: null,
            executionStatus: 'ready',
            metadataAvailable: true,
          },
        ],
        page: 1,
        pageSize: 10,
        total: 1,
      }),
      getMeasureDetails: async () => {
        throw new Error('not implemented');
      },
      submitEvaluation: async () => ({
        evaluationId: 'real-run-dup-rows',
        status: 'completed',
        submittedAt: new Date().toISOString(),
      }),
      getEvaluationResult: async () => ({
        evaluationId: 'real-run-dup-rows',
        status: 'completed',
        measureId: '__ALL_10_MEASURES__',
        submittedAt: new Date().toISOString(),
        completedAt: new Date().toISOString(),
        resultPayload: [
          {
            results: [
              {
                member_id: 'patient-42',
                measure_id: 'AAB',
                status: 'completed',
                outcome: 'numerator_not_met',
                populations: {
                  denominator: true,
                  numerator: false,
                  denominator_exclusion: false,
                },
                care_gaps: [
                  {
                    gap_id: 'gap-1',
                    missing_element: 'antibiotic avoid documentation',
                    recommended_action: 'reach-out',
                    decision_state: 'open',
                    source_evidence_refs: ['Procedure/123'],
                  },
                ],
                data_quality_flags: ['missing_claim_source'],
              },
              {
                member_id: 'patient-42',
                measure_id: 'AAB',
                status: 'unresolved',
                outcome: 'unresolved_policy',
                populations: {
                  denominator: true,
                  numerator: false,
                  denominator_exclusion: false,
                },
                care_gaps: [
                  {
                    gap_id: 'gap-2',
                    missing_element: 'encounter date policy mismatch',
                    recommended_action: 'manual-review',
                    decision_state: 'unresolved_policy',
                    source_evidence_refs: ['Encounter/456'],
                  },
                ],
                warnings: ['policy_conflict'],
              },
            ],
          },
        ],
        patientResults: [
          {
            patientId: 'patient-42',
            status: 'completed',
            populations: {
              denominator: true,
              numerator: false,
            },
          },
        ],
      }),
      listEvaluationHistory: async () => ({
        items: [],
        page: 1,
        pageSize: 10,
        total: 0,
      }),
      listPatientResults: async () => ({
        items: [],
        page: 1,
        pageSize: 10,
        total: 0,
      }),
    };

    renderWithProviders(<RunEvaluationWorkspace />, {
      config: { mode: 'real' },
      service,
    });

    const user = userEvent.setup();

    await user.upload(
      screen.getByLabelText(/patient bundle upload/i),
      new File(['{"resourceType":"Bundle"}'], 'patient-42.json', {
        type: 'application/json',
      }),
    );

    await user.click(screen.getByRole('button', { name: /run fqm evaluation/i }));

    expect(await screen.findByText(/measure outcome table/i)).toBeVisible();
    expect(screen.getAllByText('patient-42').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/policy blockers/i)).toBeVisible();
  });
});
