import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { StockSelectionTrainingWizard } from '@/features/research/components/training/StockSelectionTrainingWizard';
import type { StockSelectionDatasetVersion } from '@/generated/gql/graphql';
import { StockSelectionTrainingBackend } from '@/generated/gql/graphql';

const mocks = vi.hoisted(() => ({
  datasets: [] as StockSelectionDatasetVersion[],
  datasetRefresh: vi.fn(),
  capability: null as unknown,
  capabilityError: undefined as unknown,
  capabilityRefresh: vi.fn(),
  previewExecute: vi.fn(),
  previewOptions: [] as Array<{ pause?: boolean }>,
  previewResult: {
    data: undefined as unknown,
    error: undefined as unknown,
    fetching: false,
  },
  startDevelopment: vi.fn(),
  startResult: { fetching: false },
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
  useQuery: (options: { query: unknown; pause?: boolean }) => {
    const name = operationName(options.query);
    if (name === 'PreviewStockSelectionTraining') {
      mocks.previewOptions.push(options);
      return [mocks.previewResult, mocks.previewExecute];
    }
    if (name === 'StockSelectionTrainingCapabilities') {
      return [
        {
          data: mocks.capability
            ? { stockSelectionTrainingCapabilities: mocks.capability }
            : undefined,
          error: mocks.capabilityError,
          fetching: false,
        },
        mocks.capabilityRefresh,
      ];
    }
    return [
      {
        data: { stockSelectionDatasetVersions: mocks.datasets },
        error: undefined,
        fetching: false,
      },
      mocks.datasetRefresh,
    ];
  },
  useMutation: (document: unknown) => {
    if (operationName(document) === 'StartStockSelectionDevelopmentTraining') {
      return [mocks.startResult, mocks.startDevelopment];
    }
    return [{ fetching: false }, vi.fn()];
  },
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

function dataset(
  status: string,
  version: string
): StockSelectionDatasetVersion {
  return {
    __typename: 'StockSelectionDatasetVersion',
    createdAt: '2026-08-01T00:00:00Z',
    datasetVersion: version,
    dateEnd: '2025-12-31',
    dateStart: '2020-01-01',
    factorSetHash: 'factor-hash',
    factorSetVersion: 'factor-v1',
    indicatorVersion: 'indicator-v1',
    labelVersion: 'label-v1',
    manifestSha256: 'a'.repeat(64),
    qualitySummary: {},
    sampleCount: 100,
    sourceKind: 'CERTIFIED_SOURCE',
    status,
    stockCount: 10,
    tradingDayCount: 100,
    universeSpec: {
      kind: 'ORDINARY_A_SHARE',
      benchmark_code: '000300.SH',
      minimum_listing_days: 252,
    },
  };
}

const preview = {
  data: {
    previewStockSelectionTraining: {
      previewFingerprint: 'preview-fingerprint-1',
      datasetVersion: 'certified-v1',
      requestedBackend: StockSelectionTrainingBackend.Cpu,
      resolvedBackend: 'CPU',
      coverage: {},
      leakage: {},
      shadowReasons: [],
      blockers: [],
      warnings: [],
      capability: {},
      specHash: 'spec-hash',
      coordinateHash: 'coordinate-hash',
      canSubmit: true,
      folds: [],
      resourceEstimate: {
        memoryMib: 256,
        diskMib: 64,
        gpuMemoryMib: 0,
        estimatedMinutes: 1,
        durationLevel: 'LOW',
        sampleCount: 100,
        stockCount: 10,
        tradingDayCount: 100,
        foldCount: 0,
      },
    },
  },
  error: undefined,
  fetching: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.datasets = [
    dataset('CERTIFIED', 'certified-v1'),
    dataset('DRAFT', 'draft-v1'),
  ];
  mocks.capability = {
    cpuAvailable: true,
    gpuStatus: 'GPU_UNQUALIFIED',
    fresh: false,
    updatedAt: '2026-09-01T00:00:00Z',
    qualification: { reason: 'build evidence unavailable' },
  };
  mocks.capabilityError = undefined;
  mocks.previewOptions.length = 0;
  mocks.previewResult = preview;
  mocks.startResult.fetching = false;
  mocks.startDevelopment.mockReset();
  mocks.startDevelopment.mockResolvedValue({
    data: undefined,
    error: undefined,
  });
});

async function goToPreflight(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText('训练数据集'), 'certified-v1');
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await user.click(screen.getByRole('button', { name: '下一步' }));
  await user.click(screen.getByRole('button', { name: '下一步' }));
}

describe('StockSelectionTrainingWizard', () => {
  it('only offers certified datasets and keeps the empty state actionable', () => {
    render(<StockSelectionTrainingWizard onCreated={vi.fn()} />);

    expect(
      screen.getByRole('option', { name: /certified-v1/ })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: /draft-v1/ })
    ).not.toBeInTheDocument();
    expect(screen.getByText('普通沪深 A 股')).toBeInTheDocument();
  });

  it('does not request preflight until the explicit action and invalidates its evidence on edits', async () => {
    const user = userEvent.setup();
    render(<StockSelectionTrainingWizard onCreated={vi.fn()} />);

    await goToPreflight(user);
    expect(mocks.previewOptions.every(options => options.pause !== false)).toBe(
      true
    );

    await user.click(screen.getByRole('button', { name: '执行预检' }));
    await waitFor(() =>
      expect(
        screen.getByTestId('training-preview-evidence')
      ).toBeInTheDocument()
    );
    expect(mocks.previewOptions.some(options => options.pause === false)).toBe(
      true
    );
    expect(
      screen.getByRole('button', { name: '提交 DEVELOPMENT' })
    ).not.toBeDisabled();

    await user.click(screen.getByRole('button', { name: '上一步' }));
    await user.type(screen.getByLabelText('训练备注'), 'coordinate changed');
    await user.click(screen.getByRole('button', { name: '下一步' }));

    expect(
      screen.queryByTestId('training-preview-evidence')
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '提交 DEVELOPMENT' })
    ).toBeDisabled();
  });

  it('shows capability readiness without changing the selected backend', async () => {
    const user = userEvent.setup();
    render(<StockSelectionTrainingWizard onCreated={vi.fn()} />);

    await user.selectOptions(
      screen.getByLabelText('训练数据集'),
      'certified-v1'
    );
    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.click(screen.getByRole('button', { name: '下一步' }));

    expect(screen.getByText('GPU_UNQUALIFIED')).toBeInTheDocument();
    expect(screen.getByText('需重检')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'CPU' })).toHaveAttribute(
      'aria-checked',
      'true'
    );
    await user.click(screen.getByRole('radio', { name: 'GPU_REQUIRED' }));
    expect(screen.getByRole('radio', { name: 'GPU_REQUIRED' })).toHaveAttribute(
      'aria-checked',
      'true'
    );
  });

  it('keeps capability errors local and offers a retry with the raw message', async () => {
    const user = userEvent.setup();
    mocks.capability = null;
    mocks.capabilityError = { message: 'capability service unavailable' };
    render(<StockSelectionTrainingWizard onCreated={vi.fn()} />);

    await user.selectOptions(
      screen.getByLabelText('训练数据集'),
      'certified-v1'
    );
    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.click(screen.getByRole('button', { name: '下一步' }));

    expect(
      screen.getByText('能力摘要读取失败：capability service unavailable')
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重试能力读取' }));
    expect(mocks.capabilityRefresh).toHaveBeenCalled();
  });

  it('shows client-side bounds before preview and preserves the wizard step', async () => {
    const user = userEvent.setup();
    render(<StockSelectionTrainingWizard onCreated={vi.fn()} />);

    await user.selectOptions(
      screen.getByLabelText('训练数据集'),
      'certified-v1'
    );
    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.clear(screen.getByLabelText('Bootstrap 样本数'));
    await user.type(screen.getByLabelText('Bootstrap 样本数'), '99');
    await user.click(screen.getByRole('button', { name: '下一步' }));
    await user.click(screen.getByRole('button', { name: '执行预检' }));

    expect(
      await screen.findByTestId('training-validation-errors')
    ).toHaveTextContent('bootstrap 必须是 100–20,000 之间的整数。');
    expect(screen.getByTestId('training-step-preflight')).toBeInTheDocument();
    expect(mocks.previewExecute).not.toHaveBeenCalled();
  });

  it('reuses the fingerprint idempotency key after a failed create and calls onCreated on success', async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    mocks.startDevelopment
      .mockResolvedValueOnce({ error: { message: 'temporary create failure' } })
      .mockResolvedValueOnce({
        data: {
          startStockSelectionDevelopmentTraining: {
            runId: 'development-run-1',
          },
        },
        error: undefined,
      });
    render(<StockSelectionTrainingWizard onCreated={onCreated} />);

    await goToPreflight(user);
    await user.click(screen.getByRole('button', { name: '执行预检' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '提交 DEVELOPMENT' })
      ).not.toBeDisabled()
    );

    await user.click(screen.getByRole('button', { name: '提交 DEVELOPMENT' }));
    await waitFor(() =>
      expect(mocks.startDevelopment).toHaveBeenCalledTimes(1)
    );
    expect(
      await screen.findByText('DEVELOPMENT 提交失败：temporary create failure')
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '提交 DEVELOPMENT' }));
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith('development-run-1')
    );
    expect(mocks.startDevelopment.mock.calls[0][0].idempotencyKey).toBe(
      mocks.startDevelopment.mock.calls[1][0].idempotencyKey
    );
    expect(mocks.startDevelopment.mock.calls[0][0].previewFingerprint).toBe(
      'preview-fingerprint-1'
    );
  });
});
