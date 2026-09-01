import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FlowRunDetailPage } from './FlowRunDetailPage';

const mocks = vi.hoisted(() => ({
  reexecuteQuery: vi.fn(),
  toast: vi.fn(),
  useQuery: vi.fn(),
  useSubscription: vi.fn(),
}));

vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
  useSubscription: mocks.useSubscription,
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

vi.mock('@/components/ui/resizable', () => ({
  ResizableHandle: () => <div role="separator" />,
  ResizablePanel: ({ children }: PropsWithChildren) => <div>{children}</div>,
  ResizablePanelGroup: ({ children }: PropsWithChildren) => (
    <div data-testid="panel-group">{children}</div>
  ),
}));

vi.mock('../components/DataStudioPageFrame', () => ({
  DataStudioPageFrame: ({ children }: PropsWithChildren) => (
    <div data-testid="page-frame">{children}</div>
  ),
}));

const flowRun = {
  id: '42d66aab-38b5-4e48-b5a6-540b2e47fa6f',
  flowName: 'prophetic-oyster',
  state: 'RUNNING',
  startedAt: '2026-09-01T08:31:48.000Z',
  finishedAt: null,
  totalRunTime: 0,
  parameters: JSON.stringify({
    periods: ['1d'],
    sectors: ['沪深A股', '沪深ETF'],
    compute_daily_signals: true,
  }),
  taskRuns: [],
  detailedLogs: [
    {
      time: '2026-09-01T08:31:48.000Z',
      level: 20,
      message: '行情同步参数已加载',
    },
    {
      time: '2026-09-01T08:38:45.000Z',
      level: 30,
      message: 'Agent 行情批次 1/24 延迟',
    },
  ],
};

describe('FlowRunDetailPage', () => {
  beforeEach(() => {
    mocks.reexecuteQuery.mockReset();
    mocks.toast.mockReset();
    mocks.useQuery.mockReturnValue([
      { data: { flowRun }, fetching: false, error: undefined },
      mocks.reexecuteQuery,
    ]);
    mocks.useSubscription.mockReturnValue([
      { data: undefined, fetching: true, error: undefined },
    ]);
    vi.stubGlobal(
      'requestAnimationFrame',
      (callback: (time: number) => void) => {
        callback(0);
        return 1;
      }
    );
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders a blue running state and a separate green live connection state', () => {
    render(<FlowRunDetailPage params={{ id: flowRun.id }} />);

    screen.getAllByText('运行中').forEach(label => {
      expect(label.closest('.text-blue-300')).not.toBeNull();
      expect(label.closest('.text-emerald-300')).toBeNull();
    });
    expect(
      screen.getByText('实时已连接').closest('.text-emerald-300')
    ).not.toBeNull();
    expect(screen.getByRole('heading', { name: '运行日志' })).toBeVisible();
  });

  it('keeps an empty task state in the inspector instead of the main canvas', async () => {
    const user = userEvent.setup();
    render(<FlowRunDetailPage params={{ id: flowRun.id }} />);

    expect(
      screen.queryByText('当前流程未产生独立任务记录')
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /任务\s*0/ }));

    expect(screen.getByText('当前流程未产生独立任务记录')).toBeVisible();
    expect(screen.getByText('运行进度请以日志为准')).toBeVisible();
  });

  it('filters log rows with the search control', async () => {
    const user = userEvent.setup();
    render(<FlowRunDetailPage params={{ id: flowRun.id }} />);

    await user.type(screen.getByLabelText('搜索日志'), '不存在的日志');

    expect(screen.getByText('没有匹配的日志')).toBeVisible();
    expect(screen.getByText('0 / 2 条')).toBeVisible();
  });

  it('reconciles a live run with server truth every ten seconds', () => {
    vi.useFakeTimers();
    render(<FlowRunDetailPage params={{ id: flowRun.id }} />);

    act(() => {
      vi.advanceTimersByTime(10_000);
    });

    expect(mocks.reexecuteQuery).toHaveBeenCalledWith({
      requestPolicy: 'network-only',
    });
  });
});
