import { describe, expect, it } from 'vitest';

import {
  canDeleteReplay,
  mapReplayCyclesToActivityEvents,
  mapReplayCyclesToPositionBatches,
  mapReplayDecisionsToActivityEvaluations,
  mapReplayExecutionsToActivityEvents,
  replayProjectionActivityItems,
  replayStatusAfterDelete,
  type ReplayCycleLike,
} from './replayWorkspace';

describe('replay workspace deletion policy', () => {
  it.each(['COMPLETED', 'ERROR', 'FAILED', 'CANCELLED', 'STOPPED'])(
    'allows terminal status %s',
    status => {
      expect(canDeleteReplay(status)).toBe(true);
    }
  );

  it.each(['PENDING', 'STARTING', 'RUNNING', ''])(
    'blocks status %s',
    status => {
      expect(canDeleteReplay(status)).toBe(false);
    }
  );

  it('selects the next history record after deleting the active replay', () => {
    expect(replayStatusAfterDelete(['run-1', 'run-2'], 'run-1', 'run-1')).toBe(
      'run-2'
    );
  });

  it('keeps the current selection when another replay is deleted', () => {
    expect(replayStatusAfterDelete(['run-1', 'run-2'], 'run-2', 'run-1')).toBe(
      'run-1'
    );
  });
});

const completedCycle: ReplayCycleLike = {
  batchId: 'cycle-1',
  stockCode: '600519.SH',
  status: 'COMPLETED',
  entryTime: '2026-08-25T09:45:00+08:00',
  exitTime: '2026-08-25T10:15:00+08:00',
  entryVolume: 100,
  exitVolume: 100,
  openVolume: 0,
  entryAvgPrice: 1500,
  exitAvgPrice: 1504,
  netProfit: 380,
  netReturnPct: 0.25,
  exitReason: 'trailing_take_profit',
  liquidationStatus: 'COMPLETED',
  forcedExit: false,
};

describe('replay workspace projections', () => {
  it('projects replay cycles without any live account or broker identifier', () => {
    const [batch] = mapReplayCyclesToPositionBatches(
      [completedCycle],
      'run-replay'
    );
    const events = mapReplayCyclesToActivityEvents(
      [completedCycle],
      'run-replay'
    );

    expect(batch).toMatchObject({
      batchId: 'cycle-1',
      strategyRunId: 'run-replay',
      activeVolume: 0,
      netProfit: 380,
      status: 'COMPLETED',
    });
    expect(batch.entryBrokerOrderId).toBeNull();
    expect(events).toHaveLength(2);
    expect(events[0].payload).toMatchObject({
      report: { source: 'BACKTEST_BROKER', direction: 'BUY' },
      metadata: { strategy_run_id: 'run-replay' },
    });
    expect(events[1].payload).toMatchObject({
      report: { source: 'BACKTEST_BROKER', direction: 'SELL' },
    });
  });

  it('projects replay decisions and rejected execution facts into activity data', () => {
    const [evaluation] = mapReplayDecisionsToActivityEvaluations(
      [
        {
          id: 'decision-1',
          instanceId: 'run-replay',
          decidedAt: '2026-08-25T09:50:00+08:00',
          inputSummary: { instrument_code: '600519.SH' },
          outputSummary: {},
          tradeIntents: [],
          statePatch: {},
          decisionTrace: ['INSUFFICIENT_READY_TIME'],
        },
      ],
      'run-replay',
      'account-1'
    );
    const [event] = mapReplayExecutionsToActivityEvents(
      [
        {
          id: 'execution-1',
          intentId: 'intent-1',
          instrumentCode: '600519.SH',
          side: 'BUY',
          riskDecision: 'REJECTED',
          reason: 'INSUFFICIENT_READY_TIME',
          createdAt: '2026-08-25T09:51:00+08:00',
        },
      ],
      '2026-08-25T15:00:00+08:00'
    );

    expect(evaluation).toMatchObject({
      accountId: 'account-1',
      runId: 'run-replay',
      eventType: 'DECISION_RECORDED',
      summary: 'INSUFFICIENT_READY_TIME',
    });
    expect(event).toMatchObject({
      eventType: 'ORDER',
      error: 'INSUFFICIENT_READY_TIME',
    });
  });

  it('keeps an insufficient-ready-time report visible with zero signals', () => {
    const items = replayProjectionActivityItems({
      runId: 'run-replay',
      status: 'COMPLETED',
      progressPct: 100,
      phase: 'COMPLETED',
      phaseMessage: '回放已完成',
      processedUntil: '2026-08-25T15:00:00+08:00',
      startTime: '2026-08-25T09:30:00+08:00',
      endTime: '2026-08-25T15:00:00+08:00',
      updatedAt: '2026-08-25T15:01:00+08:00',
      errorMessage: null,
      dataQuality: 'INSUFFICIENT',
      dataQualityMessage: '有效 READY 时长不足',
      skippedStockCodes: [],
      report: {
        status: 'READY',
        generatedAt: '2026-08-25T15:01:00+08:00',
        conclusionCode: 'INSUFFICIENT_READY_TIME',
        conclusion: '本次回放没有足够 READY 时长，不能评价信号质量',
      },
    });

    expect(items).toHaveLength(2);
    expect(items).toContainEqual(
      expect.objectContaining({
        eventType: 'INSUFFICIENT_READY_TIME',
        title: '回放报告结论',
        tone: 'amber',
      })
    );
  });
});
