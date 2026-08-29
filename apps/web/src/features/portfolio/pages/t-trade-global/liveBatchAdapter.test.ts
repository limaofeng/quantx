import { describe, expect, it } from 'vitest';

import {
  adaptLiveBatch,
  adaptLiveBatchSummary,
  type LiveBatchQueryItem,
  type LiveBatchQueryMetrics,
} from './liveBatchAdapter';

const completeMetrics: LiveBatchQueryMetrics = {
  basis: 'RULE_ESTIMATE',
  origin: 'RULE_ESTIMATE',
  quality: 'COMPLETE',
  entryCapitalCny: 10_010,
  totalFeesCny: 15,
  realizedNetProfitCny: 85,
  markToMarketNetProfitCny: 85,
  netReturnPct: 0.849,
  holdingHours: 3.5,
  capitalUtilizationPct: 100,
};

const queryItem: LiveBatchQueryItem = {
  batchId: 'batch-live-1',
  accountId: 'account-1',
  stockCode: '688213.SH',
  strategyRunId: 'run-1',
  status: 'CLOSED',
  entryIntentId: 'entry-intent-1',
  exitIntentId: 'exit-intent-1',
  entryClientOrderId: 'entry-order-1',
  exitClientOrderId: 'exit-order-1',
  entryBrokerOrderId: 'entry-broker-1',
  exitBrokerOrderId: 'exit-broker-1',
  targetVolume: 100,
  entryFilledVolume: 100,
  entryAvgPrice: 100,
  exitFilledVolume: 100,
  exitAvgPrice: 101,
  activeVolume: 0,
  executionMode: 'live',
  entryFilledAt: '2026-08-27T10:00:00+08:00',
  terminalAt: '2026-08-27T13:30:00+08:00',
  closedAt: '2026-08-27T13:30:00+08:00',
  lastPrice: null,
  priceAsOf: null,
  priceQuality: 'NOT_REQUIRED',
  lastNetProfitPct: 0,
  peakNetProfitPct: 1,
  trailingFloorPct: null,
  exitReason: 'trailing_take_profit',
  exceptionReason: null,
  policyVersion: 1,
  version: 2,
  createdAt: '2026-08-27T09:59:00+08:00',
  updatedAt: '2026-08-27T13:30:00+08:00',
  metrics: completeMetrics,
};

describe('liveBatchAdapter', () => {
  it('keeps a missing quote nullable instead of inventing a market price', () => {
    const batch = adaptLiveBatch(queryItem);

    expect(batch.lastPrice).toBeNull();
    expect(batch.priceAsOf).toBeNull();
    expect(batch.executionMode).toBe('LIVE');
    expect(batch.netProfit).toBeNull();
    expect(batch.terminalAt).toBe('2026-08-27T13:30:00+08:00');
  });

  it('marks non-complete server metrics as incomplete without deriving profit', () => {
    const batch = adaptLiveBatch({
      ...queryItem,
      metrics: {
        ...completeMetrics,
        quality: 'INCOMPLETE',
        totalFeesCny: null,
        realizedNetProfitCny: null,
        markToMarketNetProfitCny: null,
        netReturnPct: null,
      },
    });

    expect(batch.metrics).toEqual(
      expect.objectContaining({
        metricBasis: 'INCOMPLETE',
        totalFeesCny: null,
        realizedNetProfitCny: null,
        markToMarketNetProfitCny: null,
        netReturnPct: null,
      })
    );
  });

  it('preserves a complete legacy backfill and its explicitly missing fields', () => {
    const batch = adaptLiveBatch({
      ...queryItem,
      metrics: {
        ...completeMetrics,
        origin: 'LEGACY_BACKFILL',
        holdingHours: null,
        capitalUtilizationPct: null,
      },
    });

    expect(batch.metrics).toEqual(
      expect.objectContaining({
        metricBasis: 'LEGACY_BACKFILL',
        realizedNetProfitCny: 85,
        holdingHours: null,
        capitalUtilizationPct: null,
      })
    );
  });

  it('maps the server summary without recomputing financial totals', () => {
    expect(
      adaptLiveBatchSummary({
        totalCount: 18,
        completedCount: 16,
        completionRatePct: 88.9,
        winningCount: 10,
        winRatePct: 62.5,
        totalFeesCny: 268.4,
        netProfitCny: 520.6,
        averageHoldingHours: 2.8,
        averageCapitalUtilizationPct: 91.2,
        metricsCoveredCount: 14,
        metricsTotalCount: 18,
        metricsCoveragePct: 77.8,
      })
    ).toEqual({
      total: 18,
      completed: 16,
      completionRate: 88.9,
      winning: 10,
      winRate: 62.5,
      feesCny: 268.4,
      netProfitCny: 520.6,
      averageHoldingHours: 2.8,
      capitalUtilizationPct: 91.2,
      coverage: 14,
    });
  });

  it('preserves missing aggregate totals when no batch has complete metrics', () => {
    expect(
      adaptLiveBatchSummary({
        totalCount: 3,
        completedCount: 1,
        completionRatePct: 33.3,
        winningCount: 0,
        winRatePct: null,
        totalFeesCny: null,
        netProfitCny: null,
        averageHoldingHours: null,
        averageCapitalUtilizationPct: null,
        metricsCoveredCount: 0,
        metricsTotalCount: 3,
        metricsCoveragePct: 0,
      })
    ).toEqual(
      expect.objectContaining({
        feesCny: null,
        netProfitCny: null,
        coverage: 0,
      })
    );
  });
});
