import { describe, expect, it } from 'vitest';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import {
  buildAttentionRows,
  classifyFreshness,
  conditionProgress,
  evaluationReasonLabel,
  type EvaluationTelemetry,
  type MonitorConfig,
  type MonitorHolding,
} from './monitoring';
import { sampleQuoteHistory } from './useLiveQuoteHistory';

const config: MonitorConfig = {
  signalLookbackSeconds: 300,
  stabilizationSeconds: 15,
  pullbackThresholdPct: 0.8,
  reboundThresholdPct: 0.2,
  maxSpreadTicks: 3,
  momentumEnabled: true,
  momentumWindowSeconds: 60,
  momentumMinRisePct: 0.8,
  momentumMinMoveSeconds: 15,
  momentumBaselineSeconds: 300,
  momentumMinAmountVelocityRatio: 2,
  momentumMinVwapPremiumPct: 2,
  momentumMaxVwapPremiumPct: 3.5,
  momentumMaxSpreadTicks: 10,
  momentumMaxSpreadPct: 0.3,
};

const evaluation: EvaluationTelemetry = {
  phase: 'ENTRY_SCAN',
  lastTickAt: '2026-08-13T10:00:00+08:00',
  processedTickCount: 100,
  windowSampleCount: 30,
  windowCoverageSeconds: 60,
  triggered: false,
  reason: 'WAITING_REBOUND',
  signalType: 'NONE',
  signalPrice: 10,
  pullbackPct: 0.72,
  reboundPct: 0.1,
  momentumRisePct: 0.72,
  momentumMoveSeconds: 12,
  momentumAmountVelocityRatio: 1.8,
};

function holding(
  stockCode: string,
  overrides: Partial<MonitorHolding> = {}
): MonitorHolding {
  return {
    stockCode,
    instrumentName: stockCode,
    volume: 1000,
    availableVolume: 1000,
    ignored: false,
    eligible: true,
    status: 'MONITORED',
    reason: '',
    session: {
      runId: `run-${stockCode}`,
      runStatus: 'RUNNING',
      status: 'OBSERVING',
      mode: 'paper',
      entryOrderStatus: '',
      exitOrderStatus: '',
      entryFilledVolume: 0,
      entryAvgPrice: 0,
      exitFilledVolume: 0,
      exitAvgPrice: 0,
      activeVolume: 0,
      lastNetProfitPct: 0,
      peakNetProfitPct: 0,
      profitArmed: false,
      lastExitReason: '',
      completedCycles: 0,
      canCancel: true,
      latestEvaluation: evaluation,
    },
    ...overrides,
  };
}

function quote(stockCode: string, time: string, price = 10): LiveMarketQuote {
  return {
    stockCode,
    currentPrice: price,
    high: price,
    low: price,
    open: price,
    volume: 100,
    time,
  };
}

describe('T trade live monitoring helpers', () => {
  it('classifies quote and heartbeat freshness only during trading hours', () => {
    const trading = new Date('2026-08-13T10:00:20+08:00');
    expect(
      classifyFreshness('2026-08-13T10:00:16+08:00', trading, 'QUOTE').level
    ).toBe('LIVE');
    expect(
      classifyFreshness('2026-08-13T10:00:10+08:00', trading, 'QUOTE').level
    ).toBe('DELAYED');
    expect(
      classifyFreshness('2026-08-13T09:59:00+08:00', trading, 'HEARTBEAT')
        .level
    ).toBe('STALE');
    expect(
      classifyFreshness(
        '2026-08-13T10:00:00+08:00',
        new Date('2026-08-13T12:00:00+08:00'),
        'QUOTE'
      ).level
    ).toBe('CLOSED');
    expect(
      classifyFreshness(
        '2026-10-01T10:00:00+08:00',
        new Date('2026-10-01T10:00:01+08:00'),
        'QUOTE',
        false
      ).level
    ).toBe('CLOSED');
  });

  it('uses the conservative minimum for each condition path', () => {
    expect(conditionProgress(evaluation, config)).toBeCloseTo(0.8);
    expect(
      conditionProgress(
        { ...evaluation, pullbackPct: 0.8, reboundPct: 0.04 },
        { ...config, momentumEnabled: false }
      )
    ).toBeCloseTo(0.2);
  });

  it('sorts abnormal, pending, stale, active, near-threshold, then normal', () => {
    const now = new Date('2026-08-13T10:00:20+08:00');
    const rows = [
      holding('600006.SH', { session: null }),
      holding('600005.SH'),
      holding('600004.SH', {
        session: { ...holding('600004.SH').session!, activeVolume: 100 },
      }),
      holding('600003.SH'),
      holding('600002.SH', {
        session: {
          ...holding('600002.SH').session!,
          pendingEntryIntentId: 'intent-1',
        },
      }),
      holding('600001.SH', {
        session: {
          ...holding('600001.SH').session!,
          errorMessage: 'projection failed',
        },
      }),
    ];
    const quotes = new Map([
      ['600001.SH', quote('600001.SH', '2026-08-13T10:00:19+08:00')],
      ['600002.SH', quote('600002.SH', '2026-08-13T10:00:19+08:00')],
      ['600004.SH', quote('600004.SH', '2026-08-13T10:00:19+08:00')],
      ['600005.SH', quote('600005.SH', '2026-08-13T10:00:19+08:00')],
      ['600006.SH', quote('600006.SH', '2026-08-13T10:00:19+08:00')],
    ]);

    expect(
      buildAttentionRows(rows, [], quotes, config, now).map(
        row => row.holding.stockCode
      )
    ).toEqual([
      '600001.SH',
      '600002.SH',
      '600003.SH',
      '600004.SH',
      '600005.SH',
      '600006.SH',
    ]);
  });

  it('samples only new quote updates and keeps a two-minute 120-point window', () => {
    const now = new Date('2026-08-13T10:00:00+08:00').getTime();
    const quotes = new Map([
      ['600000.SH', quote('600000.SH', '2026-08-13T10:00:00+08:00')],
    ]);
    const first = sampleQuoteHistory(new Map(), quotes, now);
    const duplicate = sampleQuoteHistory(first, quotes, now + 1000);
    expect(first.get('600000.SH')).toHaveLength(1);
    expect(duplicate.get('600000.SH')).toHaveLength(1);

    const oldPoints = Array.from({ length: 120 }, (_, index) => ({
      at: now - 119_000 + index * 1000,
      price: 10 + index / 100,
      sourceAt: now - 119_000 + index * 1000,
    }));
    const latest = sampleQuoteHistory(
      new Map([['600000.SH', oldPoints]]),
      new Map([
        [
          '600000.SH',
          quote('600000.SH', '2026-08-13T10:00:01+08:00', 11.2),
        ],
      ]),
      now + 1000
    );
    expect(latest.get('600000.SH')).toHaveLength(120);
    expect(latest.get('600000.SH')?.at(-1)?.price).toBe(11.2);
  });

  it('localizes evaluation reasons and degrades missing telemetry clearly', () => {
    expect(evaluationReasonLabel('WAITING_REBOUND')).toBe('等待企稳反弹');
    expect(evaluationReasonLabel()).toBe('等待首个有效 Tick');
  });
});
