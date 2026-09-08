import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ExitPlanEventsQuery, ExitPlansQuery } from '../hooks/usePortfolio';

import { SellHistoryPanel } from './SellManagementPanels';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  deleteHistory: vi.fn(),
  refetch: vi.fn(),
  toast: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', async () => ({
  ...(await vi.importActual<Record<string, unknown>>('urql')),
  useMutation: () => [{ fetching: false }, mocks.deleteHistory],
  useQuery: (options: unknown) => mocks.useQuery(options),
  useSubscription: () => [{ data: undefined }],
}));
vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({ confirm: mocks.confirm }),
}));
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

function plan(
  planId: string,
  status = 'CANCELLED',
  pendingClientOrderId: string | null = null
) {
  return {
    planId,
    status,
    pendingClientOrderId,
    instrumentCode: `${planId}.SH`,
    sourceType: 'MANUAL_POSITION',
    updatedAt: '2026-08-30T10:00:00+08:00',
  };
}

function setup(plans = [plan('600000'), plan('600001')]) {
  mocks.useQuery.mockImplementation(({ query, variables }) => {
    if (query === ExitPlansQuery) {
      return [{ data: { exitPlans: plans }, fetching: false }, mocks.refetch];
    }
    if (query === ExitPlanEventsQuery) {
      return [
        {
          data: {
            exitPlanEvents: [
              {
                eventId: 'event-1',
                eventType: `EVENT_${variables.planId}`,
                payload: {},
                createdAt: '2026-08-30T10:00:00+08:00',
              },
            ],
          },
          fetching: false,
        },
        vi.fn(),
      ];
    }
    return [{ fetching: false }, vi.fn()];
  });
  return render(<SellHistoryPanel accountId="account-1" />);
}

function openMenu(code = '600000') {
  fireEvent.contextMenu(
    screen.getByRole('button', { name: new RegExp(`${code}.SH`) }),
    {
      clientX: 400,
      clientY: 150,
    }
  );
  return screen.getByRole('menuitem', { name: /^删除记录/ });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.confirm.mockResolvedValue(true);
  mocks.deleteHistory.mockResolvedValue({
    data: {
      deleteExitPlanHistory: { success: true, message: '审计数据仍保留' },
    },
  });
});

describe('SellHistoryPanel', () => {
  it('uses the neutral shared menu and deletes the right-clicked record after confirmation', async () => {
    setup();
    const item = openMenu('600001');
    expect(screen.getByRole('menu', { name: '卖出记录菜单' })).toHaveAttribute(
      'data-studio-menu'
    );
    expect(item).toHaveClass('text-slate-300', 'hover:bg-white/5');
    expect(item).not.toHaveClass('text-rose-300');
    expect(screen.getByRole('button', { name: /600001.SH/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    );
    fireEvent.click(item);
    await waitFor(() =>
      expect(mocks.deleteHistory).toHaveBeenCalledWith({ planId: '600001' })
    );
    expect(mocks.confirm).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除 600001.SH 的卖出记录？',
        description: expect.stringContaining('审计数据仍保留'),
      })
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /600001.SH/ })
      ).not.toBeInTheDocument()
    );
    expect(screen.getByText('EVENT_600000')).toBeInTheDocument();
    expect(mocks.refetch).toHaveBeenCalledWith({
      requestPolicy: 'network-only',
    });
  });

  it('does not send a mutation when confirmation is cancelled', async () => {
    mocks.confirm.mockResolvedValue(false);
    setup();
    fireEvent.click(openMenu());
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalled());
    expect(mocks.deleteHistory).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: /600000.SH/ })
    ).toBeInTheDocument();
  });

  it.each([
    'ACTIVE',
    'PAUSED',
    'ERROR',
    'EXIT_PENDING',
    'PARTIALLY_EXITED',
    'PENDING_ENTRY',
  ])('disables deletion for %s plans', status => {
    setup([plan('600000', status)]);
    expect(openMenu()).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it('disables deletion while a terminal plan still has a pending order', () => {
    setup([plan('600000', 'CANCELLED', 'pending-order')]);
    expect(openMenu()).toBeDisabled();
  });

  it.each([
    { error: new Error('网络异常') },
    {
      data: {
        deleteExitPlanHistory: { success: false, message: '计划状态已变化' },
      },
    },
  ])('keeps the record when deletion fails', async result => {
    mocks.deleteHistory.mockResolvedValue(result);
    setup();
    fireEvent.click(openMenu());
    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '删除卖出记录失败',
          variant: 'destructive',
        })
      )
    );
    expect(
      screen.getByRole('button', { name: /600000.SH/ })
    ).toBeInTheDocument();
    expect(mocks.refetch).not.toHaveBeenCalled();
  });

  it('clears the old timeline when the last record is deleted', async () => {
    setup([plan('600000', 'COMPLETED')]);
    fireEvent.click(openMenu());
    await waitFor(() =>
      expect(screen.getByText('暂无卖出记录')).toBeInTheDocument()
    );
    expect(screen.getByText('选择卖出记录查看时间线')).toBeInTheDocument();
    expect(screen.queryByText('EVENT_600000')).not.toBeInTheDocument();
  });

  it('opens with Shift+F10 and closes with Escape or list scrolling', () => {
    setup();
    const row = screen.getByRole('button', { name: /600000.SH/ });
    fireEvent.keyDown(row, { key: 'F10', shiftKey: true });
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    openMenu();
    fireEvent.scroll(row.closest('section')!);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('does not hide a previously deleted plan that returns to an active state', async () => {
    const plans = [plan('600000')];
    const view = setup(plans);
    fireEvent.click(openMenu());
    await waitFor(() =>
      expect(screen.getByText('暂无卖出记录')).toBeInTheDocument()
    );
    plans[0].status = 'ACTIVE';
    view.rerender(<SellHistoryPanel accountId="account-1" />);
    expect(
      screen.getByRole('button', { name: /600000.SH/ })
    ).toBeInTheDocument();
    expect(openMenu()).toBeDisabled();
  });

  it('blocks duplicate deletion while the first request is in progress', async () => {
    let resolveDelete!: (result: unknown) => void;
    mocks.deleteHistory.mockReturnValue(
      new Promise(resolve => {
        resolveDelete = resolve;
      })
    );
    setup();
    fireEvent.click(openMenu());
    await waitFor(() => expect(mocks.deleteHistory).toHaveBeenCalledTimes(1));
    expect(openMenu('600001')).toBeDisabled();
    resolveDelete({
      data: { deleteExitPlanHistory: { success: true, message: 'deleted' } },
    });
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /600000.SH/ })
      ).not.toBeInTheDocument()
    );
  });
});
