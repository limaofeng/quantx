import { describe, expect, it, vi } from 'vitest';

import { filterTTradeActivityItems } from './activity';
import {
  canDeleteReplay,
  deleteReplayRunsSequentially,
  mapReplayCyclesToActivityBatches,
  mapReplayCyclesToActivityEvents,
  mapReplayCyclesToPositionBatches,
  mapReplayExecutionsToActivityEvents,
  replayDecisionInstrumentCode,
  replayDecisionReason,
  replayDecisionTraceItems,
  replayProjectionActivityItems,
  replayStatusAfterDelete,
  replayStatusAfterDeleteMany,
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

  it('selects the first remaining record after deleting a range', () => {
    expect(
      replayStatusAfterDeleteMany(
        ['run-1', 'run-2', 'run-3', 'run-4'],
        ['run-1', 'run-2', 'run-3'],
        'run-2'
      )
    ).toBe('run-4');
  });

  it('keeps an active record outside the deleted range', () => {
    expect(
      replayStatusAfterDeleteMany(
        ['run-1', 'run-2', 'run-3'],
        ['run-2', 'run-3'],
        'run-1'
      )
    ).toBe('run-1');
  });

  it('deletes a batch sequentially and reports partial failures', async () => {
    const deleteRun = vi.fn(async (runId: string) => {
      if (runId === 'run-2') throw new Error('failed');
    });

    await expect(
      deleteReplayRunsSequentially(['run-1', 'run-2', 'run-3'], deleteRun)
    ).resolves.toEqual({
      deletedRunIds: ['run-1', 'run-3'],
      failedRunIds: ['run-2'],
    });
    expect(deleteRun.mock.calls.map(([runId]) => runId)).toEqual([
      'run-1',
      'run-2',
      'run-3',
    ]);
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
  it('shows the evaluated instrument and useful reason for a no-intent decision', () => {
    const decision = {
      id: 'decision-no-intent',
      instanceId: 'run-replay',
      decidedAt: '2026-08-29T08:25:28+08:00',
      inputSummary: { instrument_code: '600519.SH' },
      outputSummary: {},
      tradeIntents: [],
      statePatch: {},
      decisionTrace: [
        'strategy_output',
        'MINIMUM_COVERAGE_NOT_REACHED',
        'opportunity_observed',
      ],
    };

    expect(replayDecisionInstrumentCode(decision)).toBe('600519.SH');
    expect(replayDecisionReason(decision)).toBe('MINIMUM_COVERAGE_NOT_REACHED');
    expect(replayDecisionTraceItems(decision)).toEqual([
      'MINIMUM_COVERAGE_NOT_REACHED',
      'opportunity_observed',
    ]);
  });

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
      status: 'CLOSED',
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

  it('keeps an unclosed replay cycle without an authoritative end mark incomplete', () => {
    const openCycle: ReplayCycleLike = {
      ...completedCycle,
      batchId: 'cycle-open',
      status: 'OPEN',
      exitTime: null,
      exitVolume: 0,
      exitAvgPrice: 0,
      openVolume: 100,
      netProfit: 280,
      netReturnPct: 0.18,
    };

    const [batch] = mapReplayCyclesToPositionBatches([openCycle], 'run-replay');
    const [activityBatch] = mapReplayCyclesToActivityBatches(
      [openCycle],
      'run-replay'
    );

    expect(batch).toMatchObject({
      status: 'ACTIVE',
      lastPrice: null,
      priceAsOf: null,
      priceQuality: 'MISSING',
      netProfit: null,
      peakNetProfitPct: null,
      terminalAt: null,
      closedAt: null,
      metrics: {
        metricBasis: 'INCOMPLETE',
        realizedNetProfitCny: null,
        markToMarketNetProfitCny: null,
        netReturnPct: null,
      },
    });
    expect(activityBatch.lastPrice).toBeNull();
    expect(activityBatch.peakNetProfitPct).toBeNull();
  });

  it('projects rejected execution facts into activity data without creating signals', () => {
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
    expect(
      filterTTradeActivityItems(items, {
        includeDiagnostics: false,
        kind: 'ALL',
        stockCode: 'ALL',
        search: '',
      })
    ).toHaveLength(2);
    expect(items).toContainEqual(
      expect.objectContaining({
        eventType: 'INSUFFICIENT_READY_TIME',
        title: '回放报告结论',
        tone: 'amber',
      })
    );
  });
});
