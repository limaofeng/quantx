import { describe, expect, it } from 'vitest';

import {
  mapBucketLedgerFromRun,
  mapDecisionHistoryFromRun,
  mapExecutionTraceFromRun,
  parseSingleInstrumentCode,
} from '@/features/strategies/domain/adapters';
import { StrategyRunMode, StrategyRunStatus } from '@/generated/gql/graphql';

const baseRun = {
  __typename: 'StrategyRun' as const,
  id: 'run-1',
  name: 'A股动态天平-Run',
  instruments: ['600519.SH'],
  parameters: { base_price: 100 },
  mode: StrategyRunMode.Paper,
  status: StrategyRunStatus.Running,
  profitLoss: 0,
  totalTrades: 0,
  metrics: {},
  errorMessage: null,
  createTime: '2026-05-10T09:30:00Z',
  startTime: '2026-05-10T09:31:00Z',
  stopTime: null,
  strategy: {
    __typename: 'Strategy' as const,
    id: 1,
    name: 'ashare_dynamic_balance_dual_bucket',
  },
};

describe('strategy domain adapters', () => {
  it('keeps backend tradeIntents as strategy intents only', () => {
    const decisions = mapDecisionHistoryFromRun({
      ...baseRun,
      metrics: {
        decisionHistory: [
          {
            id: 'decision-1',
            decidedAt: '2026-05-10T10:00:00Z',
            tradeIntents: [
              {
                id: 'intent-1',
                side: 'BUY',
                instrument_code: '600519.SH',
                target_bucket: 'swing',
                price_intent: 1680.5,
                quantity_intent: 100,
                reason: '活跃仓回补',
              },
            ],
          },
        ],
      },
    });

    expect(decisions).toHaveLength(1);
    expect(decisions[0].tradeIntents[0]).toMatchObject({
      id: 'intent-1',
      side: 'BUY',
      instrumentCode: '600519.SH',
      targetBucket: 'swing',
      priceIntent: 1680.5,
      quantityIntent: 100,
    });
    expect(decisions[0].tradeIntents[0]).not.toHaveProperty('fillStatus');
    expect(decisions[0].tradeIntents[0]).not.toHaveProperty('orderStatus');
  });

  it('maps bucket ledger into lockedCore/core/swing view fields', () => {
    const ledger = mapBucketLedgerFromRun({
      ...baseRun,
      metrics: {
        bucket_ledger: {
          locked_core: 300,
          core: 500,
          swing: 200,
        },
      },
    });

    expect(ledger).toEqual({
      lockedCore: 300,
      core: 500,
      swing: 200,
      updatedAt: null,
    });
  });

  it('does not convert risk rejection or sizing into fills', () => {
    const trace = mapExecutionTraceFromRun({
      ...baseRun,
      metrics: {
        executionTrace: [
          {
            intentId: 'intent-1',
            side: 'SELL',
            riskDecision: 'REJECTED_T_PLUS_ONE',
            sizingResult: '0',
            orderStatus: 'NOT_SUBMITTED',
            fillStatus: 'NO_FILL',
          },
        ],
      },
    });

    expect(trace[0]).toMatchObject({
      intentId: 'intent-1',
      riskDecision: 'REJECTED_T_PLUS_ONE',
      orderStatus: 'NOT_SUBMITTED',
      fillStatus: 'NO_FILL',
    });
  });

  it('enforces a single bound instrument in creation input parsing', () => {
    expect(parseSingleInstrumentCode(' 600519.sh ')).toEqual({
      instrumentCode: '600519.SH',
      instruments: ['600519.SH'],
      hasMultiple: false,
    });

    expect(parseSingleInstrumentCode('600519.SH, 000001.SZ')).toMatchObject({
      instrumentCode: '600519.SH',
      hasMultiple: true,
    });
  });
});
