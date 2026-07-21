import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  TakeProfitPlanPanel,
  type ConditionalLiquidationFormPayload,
} from '@/features/portfolio/components/TakeProfitPlanPanel';
import type { Position } from '@/features/portfolio/types';
import type { ConditionalLiquidationOrdersQuery as ConditionalLiquidationOrdersQueryData } from '@/generated/gql/graphql';

type ConditionalOrder = NonNullable<
  ConditionalLiquidationOrdersQueryData['conditionalLiquidationOrders']
>[number];

function makeHolding(overrides: Partial<Position> = {}): Position {
  return {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 72.37,
    canUseVolume: 1900,
    createdAt: '2026-06-09T09:30:00+08:00',
    direction: 1,
    frozenVolume: 0,
    id: 'holding-302132.SZ',
    instrumentName: '中航成飞',
    lastPrice: 57.07,
    marketValue: 108433,
    onRoadVolume: 0,
    openPrice: 57,
    profitLoss: -29066.2,
    profitRate: -21.14,
    stockCode: '302132.SZ',
    updatedAt: '2026-06-09T09:30:00+08:00',
    volume: 1900,
    yesterdayVolume: 1900,
    ...overrides,
  } as Position;
}

function makeOrder(
  overrides: Partial<ConditionalOrder> = {}
): ConditionalOrder {
  return {
    accountId: '300000013250',
    createdAt: '2026-06-09T09:30:00+08:00',
    enabled: true,
    id: 'order-1',
    instrumentName: '中航成飞',
    lastCheckedAt: '2026-06-09T10:00:00+08:00',
    lastError: null,
    remark: null,
    sellMode: 'ALL_AVAILABLE',
    sellRatioPct: null,
    sellVolume: null,
    status: 'ACTIVE',
    stockCode: '302132.SZ',
    submittedOrderId: null,
    submittedVolume: null,
    targetPrice: null,
    targetProfitPct: 15,
    triggeredAt: null,
    triggeredPrice: null,
    triggeredProfitPct: null,
    updatedAt: '2026-06-09T10:00:00+08:00',
    ...overrides,
  } as ConditionalOrder;
}

function renderPanel({
  onSave = vi.fn().mockResolvedValue(undefined),
  order = null,
}: {
  onSave?: (payload: ConditionalLiquidationFormPayload) => Promise<void>;
  order?: ConditionalOrder | null;
} = {}) {
  render(
    <TakeProfitPlanPanel
      accountId="300000013250"
      actionLoading={false}
      holding={makeHolding()}
      isLoading={false}
      onCancel={vi.fn().mockResolvedValue(undefined)}
      onEvaluate={vi.fn().mockResolvedValue(undefined)}
      onSave={onSave}
      onToggleEnabled={vi.fn().mockResolvedValue(undefined)}
      order={order}
      selectedStockCode="302132.SZ"
    />
  );
}

describe('TakeProfitPlanPanel', () => {
  it('defaults new plans to immediate profit target and all-available sell', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);

    renderPanel({ onSave });

    expect(screen.getByTestId('take-profit-plan-panel')).toBeInTheDocument();
    expect(
      screen.getByTestId('take-profit-strategy-selector')
    ).toHaveTextContent('到价即止盈');
    expect(
      screen.queryByTestId('take-profit-template-IMMEDIATE')
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('目标收益率 (%)')).toHaveValue(15);
    expect(screen.getByLabelText('可卖库存')).toHaveValue(1900);
    expect(
      screen.getByText(
        '收益率达到 15.00% 后提交 SELL 委托，成交以券商回报为准。'
      )
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole('button', { name: '保存并启用止盈计划' })
    );

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          enabled: true,
          sellMode: 'ALL_AVAILABLE',
          stockCode: '302132.SZ',
          targetPrice: null,
          targetProfitPct: 15,
        })
      );
    });
  });

  it('updates fields and preview from the standard take-profit preset', async () => {
    const user = userEvent.setup();

    renderPanel();

    await user.click(screen.getByRole('button', { name: /标准止盈/ }));

    expect(
      screen.getByTestId('take-profit-strategy-selector')
    ).toHaveTextContent('分段止盈 + 追踪剩余');
    expect(screen.getByLabelText('目标收益率 (%)')).toHaveValue(15);
    expect(screen.getByLabelText('卖出比例 (%)')).toHaveValue(50);
    expect(
      screen.getByTestId('take-profit-execution-preview')
    ).toHaveTextContent('本次保存首段止盈');
  });

  it('shows advanced strategies as disabled until the monitor engine exists', async () => {
    const user = userEvent.setup();

    renderPanel();

    await user.click(screen.getByRole('button', { name: '展开策略库' }));

    expect(
      screen.getByTestId('take-profit-template-TRAILING_DRAWDOWN')
    ).toBeDisabled();
    expect(screen.getAllByText('待接入监控引擎').length).toBeGreaterThan(0);
  });

  it('keeps legacy dual-condition orders explicit as either-trigger mode', () => {
    renderPanel({
      order: makeOrder({
        sellMode: 'PERCENT_AVAILABLE',
        sellRatioPct: 50,
        targetPrice: 83.23,
        targetProfitPct: 15,
      }),
    });

    expect(screen.getByRole('button', { name: '任一条件触发' })).toHaveClass(
      'text-red-100'
    );
    expect(screen.getByLabelText('目标收益率 (%)')).toHaveValue(15);
    expect(screen.getByLabelText('目标价')).toHaveValue(83.23);
    expect(
      screen.getByText(
        '任一条件满足即触发。历史双条件订单按当前后端 OR 语义执行。'
      )
    ).toBeInTheDocument();
  });
});
