import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { TrainingCapabilityStatus } from '@/features/settings/components/TrainingCapabilityStatus';

vi.mock('@/features/research/components/training/TrainerServiceStatus', () => ({
  TrainerServiceStatus: () => <section>独立训练服务</section>,
}));

const mocks = vi.hoisted(() => ({
  data: null as Record<string, unknown> | null,
  error: undefined as Error | undefined,
  fetching: false,
  refresh: vi.fn(),
}));
vi.mock('@/features/research/hooks/useStockSelectionTraining', () => ({
  useStockSelectionTrainingCapabilities: () => mocks,
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-08T01:44:15Z'));
  mocks.data = {
    cpuAvailable: true,
    fresh: true,
    updatedAt: '2026-09-08T01:44:00Z',
    gpuStatus: 'GPU_UNAVAILABLE_BUILD',
  };
  mocks.error = undefined;
  mocks.fetching = false;
  mocks.refresh.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('shows a healthy heartbeat separately from the unqualified GPU build', () => {
  render(<TrainingCapabilityStatus />);
  expect(
    screen.getByRole('heading', { name: 'CPU / GPU 训练能力心跳' })
  ).toBeVisible();
  expect(screen.getByText('心跳正常')).toBeVisible();
  expect(screen.getByText('可用')).toBeVisible();
  expect(
    screen.getByText('当前 LightGBM 构建未启用 GPU（OpenCL）支持')
  ).toBeVisible();
  expect(screen.getByText(/2026\/9\/8/)).toHaveTextContent('09:44:00');
  expect(
    screen.getByRole('link', { name: '管理训练数据与 GPU 资格' })
  ).toHaveAttribute('href', '/settings/data/research');
});

it('expires cached evidence even if the last server response was fresh', () => {
  render(<TrainingCapabilityStatus />);
  act(() => vi.advanceTimersByTime(180000));
  expect(screen.getByText('心跳已过期')).toBeVisible();
  expect(screen.getByText('上次 GPU 探测')).toBeVisible();
  expect(screen.queryByText('心跳正常')).toBeNull();
});

it('does not present cached successful evidence as current after a query error', () => {
  mocks.error = new Error('offline');
  render(<TrainingCapabilityStatus />);
  expect(screen.getByText('读取失败')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('训练能力读取失败');
  expect(screen.queryByText('心跳正常')).toBeNull();
});

it('keeps missing evidence unknown rather than treating it as unavailable hardware', () => {
  mocks.data = null;
  render(<TrainingCapabilityStatus />);
  expect(screen.getByText('尚无心跳')).toBeVisible();
  expect(screen.getByText('无记录')).toBeVisible();
  expect(screen.queryByText(/构建未启用/)).toBeNull();
});

it('refreshes automatically and manually, and stops polling on unmount', () => {
  const view = render(<TrainingCapabilityStatus />);
  act(() => vi.advanceTimersByTime(15000));
  expect(mocks.refresh).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '刷新训练心跳' }));
  expect(mocks.refresh).toHaveBeenCalledTimes(2);
  view.unmount();
  act(() => vi.advanceTimersByTime(30000));
  expect(mocks.refresh).toHaveBeenCalledTimes(2);
});
