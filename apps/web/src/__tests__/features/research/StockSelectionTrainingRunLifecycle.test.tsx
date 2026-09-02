import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { StockSelectionTrainingRunLifecycle } from '@/features/research/components/training/StockSelectionTrainingRunLifecycle';
import {
  StockSelectionResolvedBackend,
  StockSelectionTrainingBackend,
  StockSelectionTrainingPhase,
  StockSelectionTrainingRunKind,
  StockSelectionTrainingRunStatus,
  type StockSelectionTrainingRun,
} from '@/generated/gql/graphql';

const mocks = vi.hoisted(() => ({
  cancel: vi.fn(),
  cancelResult: { fetching: false },
  cancelRefresh: vi.fn(),
  comparison: null as unknown,
  comparisonRefresh: vi.fn(),
  detailRefresh: vi.fn(),
  register: vi.fn(),
  registerResult: { fetching: false },
  run: null as StockSelectionTrainingRun | null,
  runs: [] as StockSelectionTrainingRun[],
  runsRefresh: vi.fn(),
  startFinal: vi.fn(),
  finalResult: { fetching: false },
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
  useQuery: (options: { query: unknown }) => {
    switch (operationName(options.query)) {
      case 'StockSelectionTrainingRun':
        return [
          {
            data: { stockSelectionTrainingRun: mocks.run },
            error: undefined,
            fetching: false,
          },
          mocks.detailRefresh,
        ];
      case 'StockSelectionTrainingRuns':
        return [
          {
            data: {
              stockSelectionTrainingRuns: {
                total: mocks.runs.length,
                limit: 100,
                offset: 0,
                items: mocks.runs,
              },
            },
            error: undefined,
            fetching: false,
          },
          mocks.runsRefresh,
        ];
      case 'StockSelectionTrainingComparison':
        return [
          {
            data: mocks.comparison
              ? { stockSelectionTrainingComparison: mocks.comparison }
              : undefined,
            error: undefined,
            fetching: false,
          },
          mocks.comparisonRefresh,
        ];
      default:
        return [
          { data: undefined, error: undefined, fetching: false },
          vi.fn(),
        ];
    }
  },
  useMutation: (document: unknown) => {
    switch (operationName(document)) {
      case 'CancelStockSelectionTrainingRun':
        return [mocks.cancelResult, mocks.cancel];
      case 'StartStockSelectionFinalEvaluation':
        return [mocks.finalResult, mocks.startFinal];
      case 'RegisterStockSelectionModel':
        return [mocks.registerResult, mocks.register];
      default:
        return [{ fetching: false }, vi.fn()];
    }
  },
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

function trainingRun(
  overrides: Partial<StockSelectionTrainingRun> = {}
): StockSelectionTrainingRun {
  return {
    __typename: 'StockSelectionTrainingRun',
    runId: 'run-1',
    runKey: 'run-key-1',
    runKind: StockSelectionTrainingRunKind.Development,
    parentRunId: null,
    status: StockSelectionTrainingRunStatus.Queued,
    phase: StockSelectionTrainingPhase.Preflight,
    completedUnits: 0,
    totalUnits: 10,
    requestedAt: '2026-09-01T00:00:00Z',
    startedAt: null,
    completedAt: null,
    cancelRequestedAt: null,
    stateVersion: 7,
    datasetVersion: 'certified-v1',
    requestedBackend: StockSelectionTrainingBackend.Cpu,
    resolvedBackend: StockSelectionResolvedBackend.Cpu,
    specHash: 'spec-hash',
    environmentRequirementHash: 'environment-hash',
    coordinateHash: 'coordinate-hash',
    experimentGroupHash: 'experiment-hash',
    artifactManifestSha256: 'a'.repeat(64),
    environmentEvidence: { backend: 'CPU' },
    metricsSummary: {
      probability: { brierSkill: 0.12 },
      ranking: { top20: { upRateLiftCiLow: 0.03 } },
    },
    gateSummary: { conclusion: 'ACTIVE_ELIGIBLE' },
    conclusion: null,
    registerable: false,
    queueReason: null,
    errorCode: null,
    errorMessage: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.run = trainingRun();
  mocks.runs = [];
  mocks.comparison = null;
  mocks.cancelResult.fetching = false;
  mocks.finalResult.fetching = false;
  mocks.registerResult.fetching = false;
  mocks.cancel.mockReset();
  mocks.startFinal.mockReset();
  mocks.register.mockReset();
  mocks.cancel.mockResolvedValue({ data: undefined, error: undefined });
  mocks.startFinal.mockResolvedValue({ data: undefined, error: undefined });
  mocks.register.mockResolvedValue({ data: undefined, error: undefined });
});

function renderLifecycle(
  callbacks: {
    onFinalCreated?: (runId: string) => void;
    onRegistered?: () => void;
  } = {}
) {
  return render(
    <StockSelectionTrainingRunLifecycle
      runId="run-1"
      onFinalCreated={callbacks.onFinalCreated ?? vi.fn()}
      onRegistered={callbacks.onRegistered ?? vi.fn()}
    />
  );
}

describe('StockSelectionTrainingRunLifecycle', () => {
  it('cancels with expectedVersion and keeps the pending idempotency key for retry', async () => {
    const user = userEvent.setup();
    mocks.cancel
      .mockResolvedValueOnce({ error: { message: 'VERSION_CONFLICT' } })
      .mockResolvedValueOnce({
        data: {
          cancelStockSelectionTrainingRun: {
            runId: 'run-1',
            stateVersion: 8,
          },
        },
        error: undefined,
      });
    renderLifecycle();

    await user.click(screen.getByRole('button', { name: '取消训练' }));
    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('alert')).toHaveTextContent('VERSION_CONFLICT');

    await user.click(screen.getByRole('button', { name: '取消训练' }));
    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledTimes(2));
    expect(mocks.cancel.mock.calls[0][0]).toMatchObject({
      runId: 'run-1',
      expectedVersion: 7,
    });
    expect(mocks.cancel.mock.calls[0][0].idempotencyKey).toBe(
      mocks.cancel.mock.calls[1][0].idempotencyKey
    );
    expect(mocks.detailRefresh).toHaveBeenCalled();
  });

  it('shows FINAL only when the development gate has a run key and a valid manifest hash', () => {
    mocks.run = trainingRun({
      status: StockSelectionTrainingRunStatus.Succeeded,
    });
    const view = renderLifecycle();
    expect(
      screen.getByRole('button', { name: '执行 FINAL_EVALUATION' })
    ).toBeInTheDocument();

    mocks.run = trainingRun({
      status: StockSelectionTrainingRunStatus.Succeeded,
      artifactManifestSha256: 'not-a-sha256',
    });
    view.rerender(
      <StockSelectionTrainingRunLifecycle
        runId="run-1"
        onFinalCreated={vi.fn()}
        onRegistered={vi.fn()}
      />
    );
    expect(
      screen.queryByRole('button', { name: '执行 FINAL_EVALUATION' })
    ).not.toBeInTheDocument();
  });

  it('starts FINAL from the current development run and invokes the callback', async () => {
    const user = userEvent.setup();
    const onFinalCreated = vi.fn();
    mocks.run = trainingRun({
      status: StockSelectionTrainingRunStatus.Succeeded,
    });
    mocks.startFinal.mockResolvedValue({
      data: {
        startStockSelectionFinalEvaluation: { runId: 'final-run-1' },
      },
      error: undefined,
    });
    renderLifecycle({ onFinalCreated });

    await user.click(
      screen.getByRole('button', { name: '执行 FINAL_EVALUATION' })
    );
    await waitFor(() =>
      expect(onFinalCreated).toHaveBeenCalledWith('final-run-1')
    );
    expect(mocks.startFinal).toHaveBeenCalledWith(
      expect.objectContaining({ parentRunId: 'run-1' })
    );
  });

  it('registers a successful FINAL using its internal runKey without an input field', async () => {
    const user = userEvent.setup();
    const onRegistered = vi.fn();
    mocks.run = trainingRun({
      runKind: StockSelectionTrainingRunKind.FinalEvaluation,
      status: StockSelectionTrainingRunStatus.Succeeded,
      phase: StockSelectionTrainingPhase.FrozenTest,
      runKey: 'internal-final-run-key',
      registerable: true,
    });
    renderLifecycle({ onRegistered });

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '登记到模型库' }));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledTimes(1));
    expect(mocks.register).toHaveBeenCalledWith({
      runKey: 'internal-final-run-key',
    });
  });
});
