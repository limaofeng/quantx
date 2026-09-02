import { render, screen } from '@testing-library/react';

import { ResearchLifecycleRunTable } from '@/features/research/components/ResearchLifecycleRunTable';
import { researchLifecycleRunHref } from '@/features/research/model';
import {
  ResearchLifecycleRunStage,
  ResearchLifecycleRunStatus,
  ResearchLifecycleRunTarget,
  StockSelectionResolvedBackend,
  StockSelectionTrainingBackend,
  StockSelectionTrainingPhase,
  type ResearchLifecycleRun,
} from '@/generated/gql/graphql';

const trainingRun = {
  id: 'training:run-1',
  runId: 'run-1',
  studyId: 'next-day-selection',
  stage: ResearchLifecycleRunStage.Development,
  status: ResearchLifecycleRunStatus.Running,
  requestedAt: '2026-09-02T08:00:00Z',
  startedAt: '2026-09-02T08:01:00Z',
  completedAt: null,
  updatedAt: '2026-09-02T08:02:00Z',
  target: ResearchLifecycleRunTarget.TrainingRun,
  artifact: null,
  training: {
    runKey: 'opaque-run-key',
    datasetVersion: 'dataset-v1',
    requestedBackend: StockSelectionTrainingBackend.Cpu,
    resolvedBackend: StockSelectionResolvedBackend.Cpu,
    phase: StockSelectionTrainingPhase.WalkForward,
    completedUnits: 2,
    totalUnits: 10,
    conclusion: null,
    registerable: false,
    canStartFinal: false,
    queueReason: null,
    errorCode: null,
    errorMessage: null,
  },
} satisfies ResearchLifecycleRun;

const artifactRun = {
  id: 'artifact:indicator-key',
  runId: 'artifact-run-1',
  studyId: 'volume-shock',
  stage: ResearchLifecycleRunStage.Research,
  status: ResearchLifecycleRunStatus.Succeeded,
  requestedAt: '2026-09-01T08:00:00Z',
  startedAt: '2026-09-01T08:00:00Z',
  completedAt: '2026-09-01T08:02:00Z',
  updatedAt: '2026-09-01T08:02:00Z',
  target: ResearchLifecycleRunTarget.ResearchEvidence,
  artifact: {
    key: 'indicator-key',
    version: 'v1',
    eventCount: 120,
    elapsedSeconds: 5,
    configHash: 'config-hash',
    hasMetrics: true,
    artifactErrors: [],
  },
  training: null,
} satisfies ResearchLifecycleRun;

describe('ResearchLifecycleRunTable', () => {
  it('uses the training detail route for training runs', () => {
    expect(researchLifecycleRunHref(trainingRun)).toBe(
      '/research/training/runs/run-1'
    );
  });

  it('uses the opaque artifact key route for research evidence', () => {
    expect(researchLifecycleRunHref(artifactRun)).toBe(
      '/research/volume-shock/v1/runs/artifact-run-1?key=indicator-key'
    );
  });

  it('renders the shared columns and type-specific links', () => {
    render(<ResearchLifecycleRunTable runs={[trainingRun, artifactRun]} />);

    expect(
      screen.getByRole('columnheader', { name: '状态' })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('columnheader', { name: '数据集或版本' })
    ).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /查看/ });
    expect(links[0]).toHaveAttribute('href', '/research/training/runs/run-1');
    expect(links[1]).toHaveAttribute(
      'href',
      '/research/volume-shock/v1/runs/artifact-run-1?key=indicator-key'
    );
  });
});
