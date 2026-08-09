import { describe, expect, it } from 'vitest';

import type { LiquidatedStock, Position } from '@/features/portfolio/types';
import {
  buildConditionalMonitorRows,
  buildHoldingRiskAlerts,
  buildLiquidationDashboardMetrics,
  buildLiquidationDashboardSummary,
  type ConditionalLiquidationOrderLike,
} from '@/features/portfolio/utils/liquidationDashboard';

function makeHolding(overrides: Partial<Position>): Position {
  return {
    accountId: '300000013250',
    accountType: 'STOCK',
    avgPrice: 100,
    canUseVolume: 100,
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
    stockCode: '000000.SZ',
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
    lastCheckedAt: null,
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

describe('liquidation dashboard derivation', () => {
  it('derives condition monitor distances and dashboard metrics', () => {
    const holdings = [
      makeHolding({
        instrumentName: '贵州茅台',
        lastPrice: 112,
        marketValue: 11200,
        profitRate: 12,
        stockCode: '600519.SH',
      }),
      makeHolding({
        canUseVolume: 0,
        instrumentName: '冻结股票',
        marketValue: 8000,
        stockCode: '000001.SZ',
      }),
    ];
    const rows = buildConditionalMonitorRows({
      conditionalOrders: [
        makeOrder({
          instrumentName: '贵州茅台',
          stockCode: '600519.SH',
          targetProfitPct: 15,
        }),
        makeOrder({
          id: 'failed-order',
          lastError: '行情缺失',
          status: 'FAILED',
          stockCode: '000001.SZ',
        }),
        makeOrder({
          id: 'submitted-order',
          status: 'SUBMITTED',
          stockCode: '300001.SZ',
          triggeredAt: '2026-06-09T10:10:00+08:00',
        }),
      ],
      holdings,
      now: new Date('2026-06-09T11:00:00+08:00'),
    });
    const summary = buildLiquidationDashboardSummary({
      conditionalRows: rows,
      holdings,
      liquidatedStocks: [{ id: 'report-1' } as LiquidatedStock],
      portfolioMarketValue: 19200,
    });
    const metrics = buildLiquidationDashboardMetrics(summary);

    expect(rows[0].status).toBe('error');
    expect(rows.find(row => row.stockCode === '600519.SH')?.distancePct).toBe(
      3
    );
    expect(summary.enabledConditionalOrders).toBe(2);
    expect(summary.errorOrders).toBe(1);
    expect(summary.triggeredToday).toBe(1);
    expect(metrics.find(metric => metric.id === 'todayTriggers')?.value).toBe(
      '1 / 1'
    );
  });

  it('classifies risk alerts from realtime change, rolling quote drops, and today pnl', () => {
    const alerts = buildHoldingRiskAlerts({
      holdings: [
        makeHolding({
          changePercent: -1,
          instrumentName: '极速股票',
          stockCode: '600519.SH',
          todayProfitLoss: -400,
          todayProfitRate: -1,
        }),
        makeHolding({
          changePercent: -4.5,
          instrumentName: '快速股票',
          stockCode: '000001.SZ',
          todayProfitRate: -4.5,
        }),
        makeHolding({
          changePercent: -2.2,
          instrumentName: '观察股票',
          stockCode: '300001.SZ',
          todayProfitLoss: -220,
          todayProfitRate: -2.2,
        }),
      ],
      tickDropPctByCode: {
        '600519.SH': -3.2,
      },
    });

    expect(alerts.map(alert => [alert.stockCode, alert.severity])).toEqual([
      ['600519.SH', 'critical'],
      ['000001.SZ', 'warning'],
      ['300001.SZ', 'watch'],
    ]);
    expect(alerts[0].reason).toContain('滚动报价 -3.20%');
  });
});
