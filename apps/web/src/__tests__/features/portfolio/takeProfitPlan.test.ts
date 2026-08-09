import { describe, expect, it } from 'vitest';

import type { Position } from '@/features/portfolio/types';
import {
  buildTakeProfitPlanPreview,
  calculateProfitPctFromTargetPrice,
  calculateTargetPrice,
  normalizePreviewSellVolume,
} from '@/features/portfolio/utils/takeProfitPlan';

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

describe('takeProfitPlan utilities', () => {
  it('converts target profit percent and target price from cost basis', () => {
    expect(calculateTargetPrice(100, 15)).toBe(114.99999999999999);
    expect(calculateProfitPctFromTargetPrice(100, 115)).toBe(
      14.999999999999991
    );
  });

  it('normalizes preview sell volume by A-share lot constraints', () => {
    expect(normalizePreviewSellVolume(1900, 'ALL_AVAILABLE')).toBe(1900);
    expect(normalizePreviewSellVolume(1900, 'PERCENT_AVAILABLE', 50)).toBe(900);
    expect(normalizePreviewSellVolume(1900, 'FIXED_VOLUME', null, 420)).toBe(
      400
    );
    expect(normalizePreviewSellVolume(80, 'PERCENT_AVAILABLE', 50)).toBe(0);
  });

  it('builds a profit-mode preview with trigger distance and estimated value', () => {
    const preview = buildTakeProfitPlanPreview({
      holding: makeHolding(),
      sellMode: 'PERCENT_AVAILABLE',
      sellRatioPct: 50,
      targetPrice: null,
      targetProfitPct: 15,
      triggerMode: 'PROFIT',
    });

    expect(preview.targetPrice).toBeCloseTo(83.2255);
    expect(preview.targetProfitPct).toBe(15);
    expect(preview.triggerDistancePct).toBeCloseTo(36.14);
    expect(preview.estimatedSellVolume).toBe(900);
    expect(preview.estimatedOrderValue).toBeCloseTo(51363);
    expect(preview.triggerSummary).toBe('收益率达到 15.00%');
  });

  it('builds a price-mode preview with derived profit target', () => {
    const preview = buildTakeProfitPlanPreview({
      holding: makeHolding({ avgPrice: 50, lastPrice: 60, profitRate: 20 }),
      sellMode: 'ALL_AVAILABLE',
      targetPrice: 75,
      targetProfitPct: null,
      triggerMode: 'PRICE',
    });

    expect(preview.targetProfitPct).toBe(50);
    expect(preview.triggerDistancePct).toBe(25);
    expect(preview.triggerSummary).toBe('目标价达到 75.00');
  });

  it('uses either-condition summary for legacy orders with both targets', () => {
    const preview = buildTakeProfitPlanPreview({
      holding: makeHolding({ lastPrice: 80, profitRate: 10 }),
      sellMode: 'ALL_AVAILABLE',
      targetPrice: 90,
      targetProfitPct: 15,
      triggerMode: 'EITHER',
    });

    expect(preview.triggerDistancePct).toBe(5);
    expect(preview.triggerSummary).toBe(
      '收益率达到 15.00% 或 目标价达到 90.00'
    );
  });
});
