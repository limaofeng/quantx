import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SelectionModelLibrary } from '@/features/research/components/SelectionModelLibrary';
import {
  StockSelectionModelStage,
  type StockSelectionModelsQuery,
} from '@/generated/gql/graphql';

const mocks = vi.hoisted(() => ({
  models: [] as StockSelectionModelsQuery['stockSelectionModels'],
  modelsRefresh: vi.fn(),
  setStage: vi.fn(),
  setStageResult: { fetching: false },
  mutationOperations: [] as string[],
  toast: vi.fn(),
}));

function operationName(document: unknown) {
  const definitions = (document as { definitions?: unknown[] })?.definitions;
  const definition = definitions?.find(
    item =>
      typeof item === 'object' &&
      item !== null &&
      (item as { kind?: string }).kind === 'OperationDefinition'
  ) as { name?: { value?: string } } | undefined;
  return definition?.name?.value;
}

vi.mock('urql', () => ({
  useQuery: () => [
    {
      data: { stockSelectionModels: mocks.models },
      error: undefined,
      fetching: false,
    },
    mocks.modelsRefresh,
  ],
  useMutation: (document: unknown) => {
    mocks.mutationOperations.push(operationName(document) ?? 'unknown');
    return [mocks.setStageResult, mocks.setStage];
  },
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

function model(
  overrides: Partial<
    StockSelectionModelsQuery['stockSelectionModels'][number]
  > = {}
): StockSelectionModelsQuery['stockSelectionModels'][number] {
  return {
    __typename: 'StockSelectionModel',
    modelVersion: 'model-v1',
    runKey: 'run-key-1',
    selectedFamily: 'Logistic',
    artifactManifestSha256: 'a'.repeat(64),
    stage: StockSelectionModelStage.Candidate,
    indicatorVersion: 'indicator-v1',
    factorSetVersion: 'factor-v1',
    factorSetHash: 'factor-hash',
    labelVersion: 'label-v1',
    calibratorVersion: 'platt-v1',
    trainingStart: '2020-01-01',
    trainingEnd: '2024-12-31',
    calibrationStart: '2025-01-01',
    calibrationEnd: '2025-06-30',
    testStart: '2025-07-01',
    testEnd: '2025-12-31',
    historicalUniverseComplete: true,
    effectGatePassed: true,
    metrics: {
      frozen_test: {
        probability: { brier_skill: 0.1234, ece: 0.02 },
        ranking: { top_20: { up_rate_lift_ci_low: 0.04 } },
      },
    },
    gates: { conclusion: 'ACTIVE_ELIGIBLE' },
    evidence: { source: 'FINAL_EVALUATION' },
    approvedBy: 'reviewer-1',
    approvedAt: '2026-08-01T00:00:00Z',
    stateVersion: 3,
    createdAt: '2026-08-01T00:00:00Z',
    updatedAt: '2026-08-02T00:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.models = [model()];
  mocks.mutationOperations.length = 0;
  mocks.setStageResult.fetching = false;
  mocks.setStage.mockReset();
  mocks.setStage.mockResolvedValue({ data: undefined, error: undefined });
});

describe('SelectionModelLibrary', () => {
  it('leaves page-level title and description ownership to the frame', () => {
    render(<SelectionModelLibrary />);

    expect(
      screen.queryByRole('heading', { name: '模型库' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/管理具备 FINAL 证据的模型版本/)
    ).not.toBeInTheDocument();
    expect(screen.getByText('最多 1 ACTIVE / 2 SHADOW')).toBeInTheDocument();
  });

  it('renders an actionable empty state and never asks for a runKey', () => {
    mocks.models = [];
    render(<SelectionModelLibrary />);

    expect(
      screen.getByText(/尚未登记具备 FINAL 证据的模型/)
    ).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(mocks.mutationOperations).toEqual(['SetStockSelectionModelStage']);
  });

  it('shows audit evidence and sends only the selected stage with expectedVersion', async () => {
    const user = userEvent.setup();
    render(<SelectionModelLibrary />);

    expect(screen.getByText('reviewer-1')).toBeInTheDocument();
    expect(screen.getByText('approvedAt')).toBeInTheDocument();
    expect(screen.getByText('stateVersion 3')).toBeInTheDocument();
    expect(screen.getByText('Brier Skill')).toBeInTheDocument();
    expect(screen.getByText('最多 1 ACTIVE / 2 SHADOW')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '转为 SHADOW' }));
    await waitFor(() => expect(mocks.setStage).toHaveBeenCalledTimes(1));
    expect(mocks.setStage).toHaveBeenCalledWith({
      modelVersion: 'model-v1',
      stage: StockSelectionModelStage.Shadow,
      expectedVersion: 3,
    });
  });

  it('keeps the selected detail and shows the raw conflict on a stage error', async () => {
    const user = userEvent.setup();
    mocks.setStage.mockResolvedValue({
      error: { message: 'VERSION_CONFLICT: model changed' },
    });
    render(<SelectionModelLibrary />);

    await user.click(screen.getByRole('button', { name: '转为 SHADOW' }));
    expect(
      await screen.findByText(/VERSION_CONFLICT: model changed/)
    ).toBeInTheDocument();
    expect(screen.getByTestId('selection-model-detail')).toHaveTextContent(
      'model-v1'
    );
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        description: 'VERSION_CONFLICT: model changed',
      })
    );
  });
});
