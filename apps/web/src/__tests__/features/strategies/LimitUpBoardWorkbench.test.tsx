import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LimitUpBoardWorkbench from '@/features/strategies/components/LimitUpBoardWorkbench';
import { StrategyRunMode, StrategyRunStatus } from '@/generated/gql/graphql';

const urql = vi.hoisted(() => ({
  approve: vi.fn(),
  refresh: vi.fn(),
  reject: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: urql.useMutation,
  useQuery: urql.useQuery,
}));

describe('LimitUpBoardWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    urql.useQuery.mockReturnValue([
      {
        data: {
          strategyExitPlans: [],
          strategyPendingTradeIntents: [
            {
              approvalExpiresAt: new Date(Date.now() + 60_000).toISOString(),
              bucket: 'swing',
              confidence: 0.86,
              createdAt: new Date().toISOString(),
              distanceToLimitTicks: 1,
              executionMode: 'MANUAL_CONFIRM',
              id: 'intent-1',
              instrumentCode: '000001.SZ',
              limitPriceHint: 11,
              limitUpPrice: 11,
              reason: 'limit_up_board_entry',
              runId: 'run-1',
              side: 'BUY',
              signalPrice: 10.99,
              status: 'AWAITING_APPROVAL',
              targetPositionPct: 0.05,
            },
          ],
          strategyPerformance: null,
        },
        error: null,
        fetching: false,
      },
      urql.refresh,
    ]);
    urql.approve.mockResolvedValue({
      data: {
        approveStrategyTradeIntent: {
          data: null,
          message: '信号已超过确认有效期，请等待新信号',
          success: false,
        },
      },
      error: undefined,
    });
    urql.reject.mockResolvedValue({
      data: {
        rejectStrategyTradeIntent: {
          data: null,
          message: '信号已忽略',
          success: true,
        },
      },
      error: undefined,
    });
    urql.useMutation.mockReturnValue([{}, urql.approve]);
  });

  it('shows an operation failure as an error instead of a successful submission', async () => {
    const user = userEvent.setup();
    render(
      <LimitUpBoardWorkbench
        active
        backtestId={null}
        decisions={[]}
        executions={[]}
        instrumentCode="000001.SZ"
        parameters={{ entry_execution_mode: 'MANUAL_CONFIRM' }}
        runId="run-1"
        runMode={StrategyRunMode.Paper}
        runStatus={StrategyRunStatus.Running}
      />
    );

    await user.click(screen.getByRole('button', { name: '确认买入' }));

    expect(urql.approve).toHaveBeenCalledWith({
      intentId: 'intent-1',
      runId: 'run-1',
    });
    expect(
      await screen.findByText('信号已超过确认有效期，请等待新信号')
    ).toBeInTheDocument();
    expect(screen.queryByText('确认指令已提交')).not.toBeInTheDocument();
  });
});
