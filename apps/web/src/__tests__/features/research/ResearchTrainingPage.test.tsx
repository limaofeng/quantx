import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import ResearchTrainingPage from '@/features/research/pages/ResearchTrainingPage';

const mocks = vi.hoisted(() => ({
  lifecycle: {
    error: undefined as Error | undefined,
    fetching: false,
    polling: false,
    refresh: vi.fn(),
    runs: [] as Array<Record<string, unknown>>,
    total: 0,
  },
  capabilities: {
    data: {
      cpuAvailable: true,
      fresh: true,
      gpuStatus: 'GPU_AVAILABLE',
      updatedAt: '2026-09-02T08:00:00Z',
    } as Record<string, unknown> | null,
    error: undefined as Error | undefined,
    fetching: false,
    refresh: vi.fn(),
  },
  datasets: {
    data: [{ status: 'CERTIFIED', datasetVersion: 'dataset-v1' }] as Array<
      Record<string, unknown>
    >,
    error: undefined as Error | undefined,
    fetching: false,
    refresh: vi.fn(),
  },
}));

vi.mock('@/features/research/hooks', () => ({
  useResearchLifecycleRuns: () => mocks.lifecycle,
  useStockSelectionDatasetVersions: () => mocks.datasets,
  useStockSelectionTrainingCapabilities: () => mocks.capabilities,
}));

function renderPage() {
  const location = memoryLocation({ path: '/research/training' });
  return render(
    <Router hook={location.hook}>
      <ResearchTrainingPage />
    </Router>
  );
}

function resetMocks() {
  Object.assign(mocks.lifecycle, {
    error: undefined,
    fetching: false,
    runs: [],
    total: 0,
  });
  Object.assign(mocks.capabilities, {
    data: {
      cpuAvailable: true,
      fresh: true,
      gpuStatus: 'GPU_AVAILABLE',
      updatedAt: '2026-09-02T08:00:00Z',
    },
    error: undefined,
    fetching: false,
  });
  Object.assign(mocks.datasets, {
    data: [{ status: 'CERTIFIED', datasetVersion: 'dataset-v1' }],
    error: undefined,
    fetching: false,
  });
  vi.clearAllMocks();
}

beforeEach(resetMocks);
afterEach(cleanup);

describe('ResearchTrainingPage', () => {
  it('shows readiness and a single enabled CTA without embedding the wizard', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '模型训练' })).toBeVisible();
    expect(screen.getByRole('link', { name: /新建训练/ })).toHaveAttribute(
      'href',
      '/research/training/new'
    );
    expect(screen.getByText('训练创建门禁已满足。')).toBeVisible();
    expect(screen.getByText('认证数据集')).toBeVisible();
    expect(screen.getByText('CPU')).toBeVisible();
    expect(screen.getByText('GPU')).toBeVisible();
    expect(screen.queryByRole('heading', { name: '新建模型训练' })).toBeNull();
    expect(screen.queryByText(/预检指纹|训练预览/)).toBeNull();
  });

  it('places readiness above the table below xl and to its right at xl', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { name: '模型训练任务' }).closest('section')
    ).toHaveClass('order-2', 'xl:order-1');
    expect(
      screen.getByRole('heading', { name: '训练准备度' }).closest('section')
    ).toHaveClass('order-1', 'xl:order-2');
  });

  it('uses a real disabled button when certified dataset status cannot be confirmed', () => {
    mocks.datasets.data = [
      { status: 'CERTIFIED', datasetVersion: 'cached-v1' },
    ];
    mocks.datasets.error = new Error('dataset unavailable');

    renderPage();

    expect(screen.getByTestId('training-new-button')).toBeDisabled();
    expect(screen.queryByRole('link', { name: /新建训练/ })).toBeNull();
    expect(
      screen.getAllByText('认证数据集读取失败，请先重试或前往数据管理。')[0]
    ).toBeVisible();
  });

  it('blocks creation when the capability snapshot is missing', () => {
    mocks.capabilities.data = null;

    renderPage();

    expect(screen.getByTestId('training-new-button')).toBeDisabled();
    expect(
      screen.getAllByText('能力快照缺失，请先前往运行环境。')[0]
    ).toBeVisible();
  });
});
