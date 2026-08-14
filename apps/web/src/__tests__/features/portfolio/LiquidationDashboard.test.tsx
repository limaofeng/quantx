import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { LiquidationDashboard } from '@/features/portfolio/components/LiquidationDashboard';
import type { LiquidatedStock, Position } from '@/features/portfolio/types';
import type { ConditionalLiquidationOrderLike } from '@/features/portfolio/utils/liquidationDashboard';

function makeHolding(overrides: Partial<Position>): Position {
  return {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 100,
    canUseVolume: 100,
    changePercent: 0,
    createdAt: '2026-06-09T09:30:00+08:00',
    direction: 1,
    frozenVolume: 0,
    id: `holding-${overrides.stockCode || '000000.SZ'}`,
    instrumentName: '测试股票',
    lastPrice: 100,
    marketValue: 10000,
    onRoadVolume: 0,
    openPrice: 100,
    profitLoss: 0,
    profitRate: 0,
    quoteTime: '2026-06-09T10:00:00+08:00',
    stockCode: '000000.SZ',
    todayProfitLoss: 0,
    todayProfitRate: 0,
    updatedAt: '2026-06-09T09:30:00+08:00',
    volume: 100,
    yesterdayVolume: 100,
    ...overrides,
  } as Position;
}

function makeOrder(
  overrides: Partial<ConditionalLiquidationOrderLike>
): ConditionalLiquidationOrderLike {
  return {
    accountId: '300000013250',
    createdAt: '2026-06-09T09:30:00+08:00',
    enabled: true,
    id: `order-${overrides.stockCode || '000000.SZ'}`,
    instrumentName: '测试股票',
    lastCheckedAt: '2026-06-09T10:00:00+08:00',
    lastError: null,
    remark: null,
    sellMode: 'ALL_AVAILABLE',
    sellRatioPct: null,
    sellVolume: null,
    status: 'ACTIVE',
    stockCode: '000000.SZ',
    submittedOrderId: null,
    submittedVolume: null,
    targetPrice: null,
    targetProfitPct: 15,
    triggeredAt: null,
    triggeredPrice: null,
    triggeredProfitPct: null,
    updatedAt: '2026-06-09T09:30:00+08:00',
    ...overrides,
  } as ConditionalLiquidationOrderLike;
}

describe('LiquidationDashboard', () => {
  it('renders monitoring dashboard without root liquidation actions', () => {
    render(
      <LiquidationDashboard
        conditionalOrders={[
          makeOrder({
            instrumentName: '贵州茅台',
            stockCode: '600519.SH',
          }),
        ]}
        currentHoldings={[
          makeHolding({
            instrumentName: '贵州茅台',
            profitRate: 12,
            stockCode: '600519.SH',
          }),
        ]}
        liquidatedStocks={[] as LiquidatedStock[]}
        onOpenStock={vi.fn()}
        portfolioMarketValue={10000}
      />
    );

    expect(screen.getByTestId('liquidation-dashboard')).toBeInTheDocument();
    expect(screen.queryByText('一键清仓')).not.toBeInTheDocument();
    expect(screen.queryByText('提交清仓委托')).not.toBeInTheDocument();
    expect(screen.getByText('条件清仓实时触发监控')).toBeInTheDocument();
  });

  it('opens stock workspace from condition and risk rows', async () => {
    const user = userEvent.setup();
    const onOpenStock = vi.fn();

    render(
      <LiquidationDashboard
        conditionalOrders={[
          makeOrder({
            instrumentName: '贵州茅台',
            stockCode: '600519.SH',
          }),
        ]}
        currentHoldings={[
          makeHolding({
            instrumentName: '贵州茅台',
            profitRate: 12,
            stockCode: '600519.SH',
          }),
          makeHolding({
            changePercent: -5,
            instrumentName: '平安银行',
            stockCode: '000001.SZ',
            todayProfitLoss: -500,
            todayProfitRate: -5,
          }),
        ]}
        liquidatedStocks={[] as LiquidatedStock[]}
        onOpenStock={onOpenStock}
        portfolioMarketValue={20000}
      />
    );

    await user.click(screen.getByTestId('conditional-monitor-row-600519.SH'));
    await user.click(screen.getByTestId('holding-risk-alert-000001.SZ'));

    const conditionRow = screen.getByTestId(
      'conditional-monitor-row-600519.SH'
    );
    expect(conditionRow.querySelector('.text-market-up')).not.toBeNull();

    const riskRow = screen.getByTestId('holding-risk-alert-000001.SZ');
    const declineValues = riskRow.querySelectorAll('.text-holding-down');
    expect(declineValues.length).toBeGreaterThanOrEqual(2);
    expect(onOpenStock).toHaveBeenCalledWith('600519.SH', '贵州茅台');
    expect(onOpenStock).toHaveBeenCalledWith('000001.SZ', '平安银行');
  });
});
