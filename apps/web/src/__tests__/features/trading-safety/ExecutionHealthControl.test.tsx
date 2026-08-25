import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ContextType, ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ExecutionHealthControl } from '@/features/trading-safety';
import { TradingSafetyContext } from '@/features/trading-safety/trading-safety-context';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  reexecute: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
}));

vi.mock('@/components/studio-workspace', () => ({
  useStudioNavigate: () => mocks.navigate,
}));

function safetySnapshot(overrides: Record<string, unknown> = {}) {
  return {
    accountId: '300000013250',
    agentMode: 'live',
    agentStatus: 'READY',
    authorizationState: 'AUTHORIZED',
    blockedReasons: [],
    canActivateAutomation: true,
    canIncreaseRisk: true,
    canReduceRisk: true,
    checkedAt: '2026-08-25T10:21:31+08:00',
    checks: [],
    deadLetterCount: 0,
    engineStatus: 'RUNNING',
    executionMode: 'TRADING',
    executionWindowActive: true,
    externalOrderCount: 0,
    externalTradeCount: 0,
    healthStatus: 'HEALTHY',
    killSwitch: false,
    newExternalOrderCount: 0,
    newExternalTradeCount: 0,
    protocolVersion: '1.1',
    queueDelaySeconds: 0,
    queuedCommandCount: 0,
    reconciliationAgeSeconds: 12,
    reconcileStatus: 'READY',
    snapshotAt: '2026-08-25T10:21:19+08:00',
    snapshotHash: 'hash',
    snapshotId: 'snapshot-1',
    stateVersion: 2,
    summary: '账户已对账',
    unresolvedCriticalAlertCount: 0,
    workingExternalOrderCount: 0,
    ...overrides,
  };
}

function renderControl(
  control: ReactNode,
  contextOverrides: Partial<ContextType<typeof TradingSafetyContext>> = {}
) {
  return render(
    <TradingSafetyContext.Provider
      value={{
        accountId: '300000013250',
        blockedReasons: [],
        canIncreaseRisk: true,
        canReduceRisk: true,
        executionMode: 'TRADING',
        fetching: false,
        refreshSafety: vi.fn(),
        ...contextOverrides,
      }}
    >
      {control}
    </TradingSafetyContext.Provider>
  );
}

describe('ExecutionHealthControl', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useQuery.mockReturnValue([
      {
        data: { accountExecutionSafety: safetySnapshot() },
        error: undefined,
        fetching: false,
      },
      mocks.reexecute,
    ]);
  });

  it('opens the buy health drawer with account truth and plan diagnostics', async () => {
    const user = userEvent.setup();
    renderControl(
      <ExecutionHealthControl
        details={{
          automationPaused: false,
          pendingIntentCount: 1,
          plan: {
            authorizationLabel: '实盘逐笔确认',
            dailyRemainingAmountCny: 21_400,
            hasWorkingOrder: false,
            instrumentCode: '605488.SH',
            instrumentName: '福莱新材',
            lastDecision: '计划条件等待中：最新价尚未进入允许区间',
            maxBuyPrice: 31.8,
            remainingBudgetCny: 62_000,
          },
        }}
        scope="BUY"
      />
    );

    const trigger = screen.getByRole('button', {
      name: '执行健康 · 可增仓',
    });
    await user.click(trigger);

    expect(screen.getByRole('dialog')).toBeVisible();
    expect(screen.getByRole('heading', { name: '执行健康' })).toBeVisible();
    expect(screen.getByText('账户事实链路正常')).toBeVisible();
    expect(screen.getByText('当前买入计划')).toBeVisible();
    expect(screen.getByText('福莱新材')).toBeVisible();
    expect(screen.getByText('¥31.80')).toBeVisible();
    expect(screen.getByText(/最新价尚未进入允许区间/)).toBeVisible();
  });

  it('treats reduce-only as a usable sell capability instead of unhealthy', async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockReturnValue([
      {
        data: {
          accountExecutionSafety: safetySnapshot({
            canIncreaseRisk: false,
            executionMode: 'REDUCE_ONLY',
          }),
        },
        error: undefined,
        fetching: false,
      },
      mocks.reexecute,
    ]);
    renderControl(
      <ExecutionHealthControl
        details={{
          activeExitPlanCount: 1,
          holding: {
            availableVolume: 3200,
            frozenVolume: 800,
            instrumentCode: '605488.SH',
            instrumentName: '福莱新材',
            onRoadVolume: 0,
            t1UnavailableVolume: 1000,
            totalVolume: 5000,
            yesterdayVolume: 4000,
          },
          workingSellOrderCount: 1,
        }}
        scope="SELL"
      />,
      { canIncreaseRisk: false, executionMode: 'REDUCE_ONLY' }
    );

    await user.click(screen.getByRole('button', { name: '执行健康 · 仅减仓' }));

    expect(screen.getByText('账户事实链路正常')).toBeVisible();
    expect(screen.getByText('交易权限：仅减仓')).toBeVisible();
    expect(
      screen.getByText('当前禁止新增风险，但允许风险降低卖出。')
    ).toBeVisible();
    expect(screen.getByText('T+1 不可卖')).toBeVisible();
    expect(screen.getByText('1,000 股')).toBeVisible();
  });

  it('keeps a returned snapshot visible but closes execution on query error', async () => {
    const user = userEvent.setup();
    mocks.useQuery.mockReturnValue([
      {
        data: { accountExecutionSafety: safetySnapshot() },
        error: new Error('network unavailable'),
        fetching: false,
      },
      mocks.reexecute,
    ]);
    renderControl(
      <ExecutionHealthControl
        details={{ automationPaused: false, pendingIntentCount: 0 }}
        scope="BUY"
      />
    );

    await user.click(screen.getByRole('button', { name: '执行健康 · 可增仓' }));

    expect(screen.getByText('状态未知')).toBeVisible();
    expect(screen.getByText('交易权限：安全关闭')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent(
      '已保留 10:21:31 的最近成功快照'
    );
    expect(screen.getByText('最近成功快照')).toBeVisible();
  });
});
