import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { TrainerServiceStatus } from '@/features/research/components/training/TrainerServiceStatus';

const state = vi.hoisted(() => ({
  data: {
    service: 'ALIVE',
    admission: 'DRAINING',
    fresh: true,
    updatedAt: '2026-09-10T04:00:00Z',
    resourceReason: 'TRADING_OR_POST_CLOSE_CRITICAL_WINDOW',
    training: {
      state: 'FRESH',
      status: 'QUEUED',
      reason: 'TRADING_OR_POST_CLOSE_CRITICAL_WINDOW',
    },
    preparation: { state: 'UNKNOWN', status: null, reason: null },
  },
  error: undefined as Error | undefined,
  fetching: false,
  refresh: vi.fn(),
}));
vi.mock('@/features/research/hooks/useStockSelectionTrainerStatus', () => ({
  useStockSelectionTrainerStatus: () => state,
}));
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-10T04:00:10Z'));
  state.error = undefined;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('shows a running protected Trainer separately from capability probes', () => {
  render(<TrainerServiceStatus />);
  expect(screen.getByText('服务运行中')).toBeInTheDocument();
  expect(screen.getByText('已暂停领取新任务')).toBeInTheDocument();
  expect(screen.getAllByText('实盘保护时段，任务保持排队')).toHaveLength(2);
  expect(screen.getByText('等待新的调度状态')).toBeInTheDocument();
});

it('hides stale or failed observations instead of showing the old queue reason', () => {
  vi.setSystemTime(new Date('2026-09-10T04:02:00Z'));
  const view = render(<TrainerServiceStatus />);
  expect(screen.getByText('状态未确认')).toBeInTheDocument();
  expect(
    screen.queryByText('实盘保护时段，任务保持排队')
  ).not.toBeInTheDocument();
  state.error = new Error('offline');
  view.rerender(<TrainerServiceStatus />);
  expect(screen.getByRole('alert')).toHaveTextContent('服务状态读取失败');
});
