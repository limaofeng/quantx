import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ExitPlansPanel } from '@/features/portfolio/components/SellManagementPanels';
import { SetExitPlanEnabledMutation } from '@/features/portfolio/hooks/usePortfolio';
import type { Position } from '@/features/portfolio/types';

const mocks = vi.hoisted(() => ({
  exitPlans: [] as Array<Record<string, unknown>>,
  refetch: vi.fn(),
  togglePlan: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: (document: unknown) => [
    { fetching: false },
    document === SetExitPlanEnabledMutation ? mocks.togglePlan : vi.fn(),
  ],
  useQuery: ({ query }: { query: { definitions?: unknown[] } }) => {
    const operationName = (
      query.definitions?.[0] as { name?: { value?: string } } | undefined
    )?.name?.value;
    if (operationName === 'ExitPlans' || operationName === undefined) {
      return [
        {
          data: { exitPlans: mocks.exitPlans },
          error: undefined,
          fetching: false,
        },
        mocks.refetch,
      ];
    }
    return [{ data: undefined, error: undefined, fetching: false }, vi.fn()];
  },
  useSubscription: () => [{ data: undefined }],
}));

vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({ confirm: vi.fn() }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

function makePlan(instrumentCode: string) {
  return {
    accountId: '300000013250',
    autoExitAuthorizationConfigVersion: null,
    autoExitAuthorizationExpiresAt: null,
    autoExitAuthorized: false,
    bucket: 'SWING',
    canEditRules: false,
    capacityError: null,
    capacityStatus: 'READY',
    completionNote: null,
    completionStrategy: null,
    configVersion: 1,
    costBasis: {},
    createdAt: '2026-08-21T09:30:00+08:00',
    dataQuality: 'MARKET_DATA_STALE',
    editRoute: null,
    enabled: true,
    entryAvgPrice: 28.3628,
    environment: 'LIVE',
    executionOwner: {
      ownerId: `plan-${instrumentCode}`,
      ownerType: 'EXIT_PLAN',
    },
    exitedVolume: 0,
    groupId: null,
    instrumentCode,
    lastDecision: 'market_data_stale',
    lastError: null,
    lastEvaluatedAt: '2026-08-21T15:00:00+08:00',
    metadata: {},
    peakDrawdownPct: 0,
    peakPrice: 0,
    pendingClientOrderId: null,
    pendingIntentId: null,
    phase: 'WAITING_ARM',
    planId: `plan-${instrumentCode}`,
    protectedVolume: 400,
    recoveryAction: null,
    recoveryMessage: null,
    remainingVolume: 400,
    rules: [],
    sourceExecutionOwner: {
      ownerId: 'manual-command-1',
      ownerType: 'MANUAL_COMMAND',
    },
    sourceId: 'manual',
    sourceType: 'MANUAL_POSITION',
    status: 'ACTIVE',
    strategyRunId: null,
    stateVersion: 7,
    trailingFloorPct: null,
    updatedAt: '2026-08-21T15:00:00+08:00',
  };
}

describe('ExitPlansPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.exitPlans = [makePlan('300917.SZ')];
  });

  it('shows the holding name with the instrument code on an exit plan', () => {
    const holdings = [
      {
        instrumentName: '特发服务',
        stockCode: '300917.SZ',
      } as Position,
    ];

    render(
      <ExitPlansPanel
        accountId="300000013250"
        holdings={holdings}
        onNavigate={vi.fn()}
      />
    );

    expect(
      screen.getByRole('heading', { name: /特发服务.*300917\.SZ/ })
    ).toBeVisible();
  });

  it('falls back to the instrument code when the holding name is unavailable', () => {
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { name: '300917.SZ' })).toBeVisible();
  });

  it('shows a meaningful label instead of the internal rule code', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        rules: [
          {
            rule_id: 'adaptive-volume-price',
            strategy: 'ADAPTIVE_VOLUME_PRICE_TRAILING',
          },
        ],
      },
    ];

    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText('量价动态止盈')).toBeVisible();
    expect(
      screen.queryByText('ADAPTIVE_VOLUME_PRICE_TRAILING')
    ).not.toBeInTheDocument();
  });

  it('shows the execution owner and durable state revision', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        executionOwner: {
          ownerId: 'plan-300917.SZ',
          ownerType: 'EXIT_PLAN',
        },
        sourceExecutionOwner: {
          ownerId: 't-run-1',
          ownerType: 'STRATEGY_RUN',
        },
        sourceType: 'T_TRADE_BATCH',
        strategyRunId: 't-run-1',
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText(/执行归属\s*退出计划/)).toBeVisible();
    expect(screen.getByText(/来源归属\s*策略运行/)).toBeVisible();
    expect(screen.getByText('状态修订 r7')).toBeVisible();
  });

  it('shows manual plans as exit-plan-owned and editable', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        canEditRules: true,
        executionOwner: {
          ownerId: 'plan-300917.SZ',
          ownerType: 'EXIT_PLAN',
        },
        sourceExecutionOwner: {
          ownerId: 'manual-command-1',
          ownerType: 'MANUAL_COMMAND',
        },
        sourceType: 'MANUAL_POSITION',
        strategyRunId: null,
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText(/执行归属\s*退出计划/)).toBeVisible();
    expect(screen.getByText(/来源归属\s*人工命令/)).toBeVisible();
    expect(screen.getByRole('button', { name: '编辑计划' })).toBeEnabled();
  });

  it('only exposes cancellation after a repaired plan requires rebuilding', () => {
    mocks.exitPlans = [
      {
        ...makePlan('302132.SZ'),
        canEditRules: false,
        enabled: false,
        lastError: 'QUARANTINE_REPAIRED:intent-old',
        recoveryAction: 'CANCEL_AND_REBUILD',
        recoveryMessage:
          '隔离委托已完成券商事实修复。旧计划不能恢复；请取消旧计划，再按最新持仓重新创建并授权。',
        status: 'ERROR',
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText(/旧计划不能恢复/)).toBeVisible();
    expect(
      screen.queryByRole('button', { name: '恢复' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '立即检查' })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '编辑计划' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消旧计划' })).toBeEnabled();
  });

  it('keeps cancellation disabled until broker reconciliation is complete', () => {
    mocks.exitPlans = [
      {
        ...makePlan('302132.SZ'),
        canEditRules: false,
        enabled: false,
        recoveryAction: 'COMPLETE_RECONCILIATION',
        recoveryMessage: '计划存在尚未解除的券商事实隔离，请先完成账户对账。',
        status: 'ERROR',
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(
      screen.queryByRole('button', { name: '恢复' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
  });

  it('uses server recovery and edit capabilities for operation gates', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        canEditRules: false,
        recoveryAction: 'COMPLETE_RECONCILIATION',
        recoveryMessage: '计划需要先完成账户对账。',
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: '编辑计划' })
    ).not.toBeInTheDocument();
  });

  it('fails closed when the execution owner does not identify the plan', () => {
    mocks.exitPlans = [
      {
        ...makePlan('300917.SZ'),
        canEditRules: true,
        executionOwner: {
          ownerId: 'another-plan',
          ownerType: 'EXIT_PLAN',
        },
        pendingIntentId: 'intent-1',
      },
    ];
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    expect(screen.getByText('执行归属校验失败，计划操作已停用')).toBeVisible();
    expect(screen.getByRole('button', { name: '回放测试' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: '预览并确认 SELL' })
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: '拒绝意图' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '暂停' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '立即检查' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: '编辑计划' })
    ).not.toBeInTheDocument();
  });

  it('reuses an enable-state operation key on retry and rotates it after success', async () => {
    const user = userEvent.setup();
    mocks.togglePlan
      .mockResolvedValueOnce({ error: new Error('Engine 尚未确认操作') })
      .mockResolvedValue({
        data: {
          setExitPlanEnabled: {
            configVersion: 1,
            enabled: false,
            planId: 'plan-300917.SZ',
            status: 'PAUSED',
          },
        },
        error: undefined,
      });
    render(<ExitPlansPanel accountId="300000013250" onNavigate={vi.fn()} />);

    const pause = screen.getByRole('button', { name: '暂停' });
    await user.click(pause);
    await waitFor(() => expect(mocks.togglePlan).toHaveBeenCalledTimes(1));
    await user.click(pause);
    await waitFor(() => expect(mocks.togglePlan).toHaveBeenCalledTimes(2));

    const first = mocks.togglePlan.mock.calls[0][0];
    const retry = mocks.togglePlan.mock.calls[1][0];
    expect(first).toEqual(
      expect.objectContaining({
        configVersion: 1,
        enabled: false,
        idempotencyKey: expect.any(String),
        planId: 'plan-300917.SZ',
      })
    );
    expect(retry.idempotencyKey).toBe(first.idempotencyKey);

    await user.click(pause);
    await waitFor(() => expect(mocks.togglePlan).toHaveBeenCalledTimes(3));
    expect(mocks.togglePlan.mock.calls[2][0].idempotencyKey).not.toBe(
      first.idempotencyKey
    );
  });
});
