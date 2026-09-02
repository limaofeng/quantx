import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import ResearchCenterPage from '@/features/research/pages/ResearchCenterPage';

const mocks = vi.hoisted(() => ({
  research: {
    error: undefined as Error | undefined,
    fetching: false,
    refresh: vi.fn(),
    runs: [] as Array<Record<string, unknown>>,
    total: 0,
  },
  training: {
    error: undefined as Error | undefined,
    fetching: false,
    polling: false,
    refresh: vi.fn(),
    runs: [] as Array<Record<string, unknown>>,
    total: 0,
  },
  capabilities: {
    data: null as Record<string, unknown> | null,
    error: undefined as Error | undefined,
    fetching: false,
    refresh: vi.fn(),
  },
  datasets: {
    data: [] as Array<Record<string, unknown>>,
    error: undefined as Error | undefined,
    fetching: false,
    refresh: vi.fn(),
  },
}));

vi.mock('@/features/research/hooks', () => ({
  useResearchRuns: () => mocks.research,
  useStockSelectionDatasetVersions: () => mocks.datasets,
  useStockSelectionTrainingCapabilities: () => mocks.capabilities,
  useStockSelectionTrainingRuns: () => mocks.training,
}));

function renderPage() {
  const location = memoryLocation({ path: '/research' });
  return render(
    <Router hook={location.hook}>
      <ResearchCenterPage />
    </Router>
  );
}

function resetMocks() {
  Object.assign(mocks.research, {
    error: undefined,
    fetching: false,
    runs: [],
    total: 0,
  });
  Object.assign(mocks.training, {
    error: undefined,
    fetching: false,
    runs: [],
    total: 0,
  });
  Object.assign(mocks.capabilities, {
    data: null,
    error: undefined,
    fetching: false,
  });
  Object.assign(mocks.datasets, {
    data: [],
    error: undefined,
    fetching: false,
  });
  vi.clearAllMocks();
}

beforeEach(resetMocks);
afterEach(cleanup);

describe('ResearchCenterPage', () => {
  it('keeps the overview task hierarchy without embedding the old registry or wizard', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '研究中心' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '需要处理' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '进行中' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '最近有效证据' })).toBeVisible();
    expect(screen.getByRole('link', { name: /新建模型训练/ })).toHaveAttribute(
      'href',
      '/research/training/new'
    );
    expect(screen.getByRole('button', { name: '新建指标研究' })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: '新建模型训练' })).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(
      screen.queryByText(/SelectionModelRegistry|TrainingWizard/)
    ).toBeNull();
  });

  it('shows independent loading states before replacing them with empty states', () => {
    mocks.research.fetching = true;
    mocks.training.fetching = true;
    mocks.capabilities.fetching = true;
    mocks.datasets.fetching = true;

    renderPage();

    expect(screen.getByText('正在读取认证数据集…')).toBeVisible();
    expect(screen.getByText('正在读取训练能力快照…')).toBeVisible();
    expect(screen.getByText('正在读取训练运行…')).toBeVisible();
    expect(screen.getByText('正在读取有效证据…')).toBeVisible();
    expect(screen.queryByText('当前没有需要人工处理的事项。')).toBeNull();
    expect(screen.queryByText('当前没有排队或运行中的任务。')).toBeNull();
    expect(screen.queryByText('还没有可审阅的有效证据。')).toBeNull();
  });

  it('keeps the available evidence source visible when the other source fails', () => {
    mocks.research.error = new Error('research unavailable');
    mocks.training.runs = [
      {
        completedAt: '2026-09-02T08:02:00Z',
        completedUnits: 10,
        conclusion: 'ACTIVE_ELIGIBLE',
        datasetVersion: 'dataset-v1',
        errorCode: null,
        errorMessage: null,
        registerable: false,
        requestedAt: '2026-09-02T08:00:00Z',
        runId: 'training-run-1',
        runKind: 'DEVELOPMENT',
        startedAt: '2026-09-02T08:01:00Z',
        status: 'SUCCEEDED',
        totalUnits: 10,
      },
    ];

    renderPage();

    expect(screen.getByRole('alert')).toHaveTextContent(
      '离线研究证据读取失败。'
    );
    expect(screen.getByText('次日上涨概率')).toBeVisible();
    expect(screen.getByText('dataset-v1')).toBeVisible();
    expect(screen.getByRole('link', { name: /查看运行/ })).toHaveAttribute(
      'href',
      '/research/training/runs/training-run-1'
    );
  });

  it('turns a successful but missing capability snapshot into an actionable item', () => {
    mocks.datasets.data = [{ status: 'CERTIFIED' }];

    renderPage();

    expect(screen.getByText('训练能力快照缺失或已过期')).toBeVisible();
    expect(screen.getAllByRole('link', { name: '处理' })[0]).toHaveAttribute(
      'href',
      '/settings/status'
    );
    expect(screen.getByText('1 项')).toBeVisible();
  });
});
