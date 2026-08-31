import { describe, expect, it } from 'vitest';

import {
  buildTTradeActivityItems,
  filterTTradeActivityItems,
  type ActivityBatch,
  type ActivityBatchEvent,
  type ActivitySignalEvaluation,
} from './activity';
import type { SignalSnapshot } from './monitoring';

function signalSnapshot(
  instrumentCode: string,
  opportunityScore: number
): SignalSnapshot {
  return {
    instrumentCode,
    opportunityScore,
    candidateThreshold: 70,
    previewThreshold: 55,
    revalidateThreshold: 64,
    rearmThreshold: 46,
    topBlockers: [],
    candidateId: `candidate-${opportunityScore}`,
    pendingEntryIntentId: null,
  } as SignalSnapshot;
}

function evaluation(
  id: string,
  evaluatedAt: string,
  eventKind = 'MATERIAL',
  score = 60
): ActivitySignalEvaluation {
  return {
    id,
    accountId: 'account-1',
    runId: 'run-1',
    stockCode: '600519.SH',
    eventKind,
    eventType:
      eventKind === 'COALESCED_DIAGNOSTIC'
        ? 'COALESCED_DIAGNOSTIC'
        : 'FSM_TRANSITION',
    evaluatedAt,
    coalescedCount: 1,
    policyVersion: 'policy-1',
    signalSnapshot: signalSnapshot('600519.SH', score),
  };
}

const batch: ActivityBatch = {
  batchId: 'batch-1',
  stockCode: '000001.SZ',
  strategyRunId: 'run-1',
  status: 'ENTERED',
  entryFilledVolume: 100,
  entryAvgPrice: 10.12,
  exitFilledVolume: 0,
  exitAvgPrice: 0,
  activeVolume: 100,
  lastPrice: 10.2,
  lastNetProfitPct: 0.7,
  peakNetProfitPct: 0.9,
  version: 2,
};

describe('buildTTradeActivityItems', () => {
  it('orders China-naive and offset-aware evidence by the same timeline', () => {
    const items = buildTTradeActivityItems(
      [
        evaluation('older-china', '2026-08-25T09:30:00'),
        evaluation('newer-utc', '2026-08-25T01:31:00Z'),
      ],
      [],
      []
    );
    expect(items.map(item => item.id)).toEqual([
      'signal:newer-utc',
      'signal:older-china',
    ]);
  });

  it('merges signal and broker events in reverse chronological order', () => {
    const orderEvent: ActivityBatchEvent = {
      eventId: 'event-order',
      batchId: batch.batchId,
      eventType: 'ORDER',
      status: 'APPLIED',
      clientOrderId: 'client-1',
      brokerOrderId: 'broker-1',
      createdAt: '2026-08-25T10:01:00+08:00',
      payload: {
        report: {
          stock_code: '000001.SZ',
          order_type: 23,
          order_status: 'PARTIAL_FILLED',
          order_volume: 100,
          traded_volume: 40,
          price: 10.12,
        },
        metadata: { t_trade_role: 'ENTRY' },
      },
    };
    const tradeEvent: ActivityBatchEvent = {
      ...orderEvent,
      eventId: 'event-trade',
      eventType: 'TRADE',
      createdAt: '2026-08-25T10:02:00+08:00',
      payload: {
        report: {
          stock_code: '000001.SZ',
          order_type: 23,
          execution_id: 'execution-1',
          traded_volume: 40,
          traded_price: 10.11,
        },
        metadata: { t_trade_role: 'ENTRY' },
      },
    };

    const items = buildTTradeActivityItems(
      [evaluation('signal-1', '2026-08-25T10:00:00+08:00')],
      [orderEvent, tradeEvent],
      [batch]
    );

    expect(items.map(item => item.kind)).toEqual(['TRADE', 'ORDER', 'SIGNAL']);
    expect(items[0].executionSnapshot).toMatchObject({
      direction: 'BUY',
      executionId: 'execution-1',
      tradedPrice: 10.11,
      tradedVolume: 40,
    });
    expect(items[1].title).toBe('委托状态');
    expect(items[1].title).not.toBe('真实成交');
  });

  it('attaches the preceding persisted snapshot for historical comparison', () => {
    const older = evaluation(
      'signal-old',
      '2026-08-25T09:45:00+08:00',
      'MATERIAL',
      48
    );
    const newer = evaluation(
      'signal-new',
      '2026-08-25T09:46:00+08:00',
      'MATERIAL',
      72
    );

    const [newest] = buildTTradeActivityItems([newer, older], [], []);

    expect(newest.signalEvaluation?.id).toBe('signal-new');
    expect(newest.previousSignalSnapshot?.opportunityScore).toBe(48);
  });

  it('does not compare snapshots across different strategy runs', () => {
    const older = evaluation(
      'signal-old-run',
      '2026-08-25T09:45:00+08:00',
      'MATERIAL',
      48
    );
    const newer = {
      ...evaluation(
        'signal-new-run',
        '2026-08-25T09:46:00+08:00',
        'MATERIAL',
        72
      ),
      runId: 'run-2',
    };

    const [newest] = buildTTradeActivityItems([newer, older], [], []);

    expect(newest.previousSignalSnapshot).toBeNull();
  });

  it('labels backtest broker fills as simulated and keeps real signal summaries', () => {
    const replayEvaluation: ActivitySignalEvaluation = {
      ...evaluation('replay-signal', '2026-08-25T09:45:00+08:00'),
      eventType: 'CANDIDATE_SUPPRESSED',
      summary: '准备时长不足，候选已抑制',
    };
    const replayTrade: ActivityBatchEvent = {
      eventId: 'replay-trade',
      batchId: batch.batchId,
      eventType: 'TRADE',
      status: 'APPLIED',
      clientOrderId: 'backtest-order',
      createdAt: '2026-08-25T09:46:00+08:00',
      payload: {
        report: {
          instrument_code: batch.stockCode,
          direction: 'BUY',
          filled_volume: 100,
          filled_price: 10.12,
        },
        metadata: { role: 'ENTRY' },
      },
    };

    const items = buildTTradeActivityItems(
      [replayEvaluation],
      [replayTrade],
      [batch],
      'REPLAY'
    );

    expect(items[0].title).toBe('模拟成交');
    expect(items[1]).toMatchObject({
      kind: 'CANDIDATE',
      title: '候选已抑制',
      summary: '准备时长不足，候选已抑制',
    });
  });
});

describe('filterTTradeActivityItems', () => {
  it('keeps policy and continuity changes visible when diagnostics are hidden', () => {
    const items = buildTTradeActivityItems(
      [
        'POLICY_CHANGED',
        'PROFILE_CHANGED',
        'CONTINUITY_GENERATION_CHANGED',
      ].map(eventType => ({
        ...evaluation(eventType, '2026-08-25T10:00:00+08:00'),
        eventType,
      })),
      [],
      []
    );
    expect(
      filterTTradeActivityItems(items, {
        includeDiagnostics: false,
        kind: 'ALL',
        stockCode: 'ALL',
        search: '',
      }).map(item => item.kind)
    ).toEqual(['CONTEXT', 'CONTEXT', 'CONTEXT']);
  });

  it('hides diagnostic observations by default and searches persisted IDs', () => {
    const items = buildTTradeActivityItems(
      [
        evaluation('material-1', '2026-08-25T10:00:00+08:00'),
        evaluation(
          'diagnostic-1',
          '2026-08-25T10:01:00+08:00',
          'COALESCED_DIAGNOSTIC'
        ),
      ],
      [],
      []
    );

    expect(
      filterTTradeActivityItems(items, {
        includeDiagnostics: false,
        kind: 'ALL',
        stockCode: 'ALL',
        search: '',
      }).map(item => item.kind)
    ).toEqual(['SIGNAL']);

    expect(
      filterTTradeActivityItems(items, {
        includeDiagnostics: true,
        kind: 'ALL',
        stockCode: '600519.SH',
        search: 'candidate-60',
      })
    ).toHaveLength(2);
  });
});
