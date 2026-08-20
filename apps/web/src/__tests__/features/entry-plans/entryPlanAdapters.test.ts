import {
  defaultEntryPlanDraft,
  type EntryPlanDraft,
} from '@/features/entry-plans';
import {
  buildEntryPlanConfiguration,
  mapEntryPlanWorkspace,
  type EntryPlanProjection,
} from '@/features/entry-plans/model/adapters';

function makePlan(
  overrides: Partial<EntryPlanProjection> = {}
): EntryPlanProjection {
  return {
    planId: 'plan-1',
    configVersion: 3,
    instrumentCode: '605499.SH',
    instrumentName: '东鹏饮料',
    bucket: 'core',
    phase: 'ARMED',
    runStatus: 'RUNNING',
    environment: 'LIVE',
    authorizationMode: 'MANUAL_CONFIRM',
    authorizationState: 'MANUAL_CONFIRM',
    targetMode: 'TARGET_POSITION_PCT',
    targetPositionPct: 0.2,
    incrementalAmountCny: 0,
    additionalVolume: 0,
    maxTotalAmountCny: 20_000,
    maxPositionPct: 0.25,
    currentPositionVolume: 200,
    currentMarketValueCny: 25_000,
    filledAmountCny: 12_000,
    maxSingleIntentAmountCny: 5_000,
    maxDailyFilledAmountCny: 10_000,
    maxBuyPrice: 128,
    ruleTypes: ['TREND_PULLBACK_CONFIRMATION'],
    lastReasonCode: 'ENTRY_PENDING_LOCKED',
    pendingIntentId: '',
    hasWorkingOrder: false,
    hasExitProtection: true,
    ...overrides,
  };
}

describe('entry plan GraphQL adapters', () => {
  it('only counts authoritative filled events as today real purchases', () => {
    const occurredAt = new Date().toISOString();
    const view = mapEntryPlanWorkspace(
      {
        currentAccount: {
          id: 'account-1',
          cash: 100_000,
          totalAsset: 500_000,
          updateTime: occurredAt,
        },
        positions: [
          {
            stockCode: '605499.SH',
            instrumentName: '东鹏饮料',
            volume: 200,
            lastPrice: 125,
            marketValue: 25_000,
          },
        ],
        entryPlans: [makePlan()],
        pendingEntryIntents: [],
        entryAutomationStatus: { paused: false, reason: '' },
        entryPlanCapabilities: { version: 'managed-entry-v1' },
      },
      [
        {
          eventId: 'ack-like-intent',
          planId: 'plan-1',
          eventType: 'ENTRY_INTENT_CREATED',
          occurredAt,
          reasonCode: 'COMMAND_ACK',
          message: '意图已投递',
          details: { requested_amount_cny: 99_999 },
        },
        {
          eventId: 'real-fill',
          planId: 'plan-1',
          eventType: 'ENTRY_ORDER_FILLED',
          occurredAt,
          reasonCode: 'TRADE_REPORT_RECONCILED',
          message: '真实成交已收敛',
          details: { executed_price: 10, executed_volume: 100 },
        },
      ]
    );

    expect(view.todayFilledAmountCny).toBe(1_000);
    expect(view.plans[0]).toMatchObject({
      configVersion: 3,
      currentPositionPct: 5,
      dailyRemainingAmountCny: 9_000,
      executionScenario: 'LIVE_MANUAL',
      targetPositionPct: 20,
    });
    expect(view.events.find(event => event.id === 'real-fill')).toMatchObject({
      amountCny: 1_000,
      kind: 'TRADE_FILLED',
      volume: 100,
    });
  });

  it('builds a LIVE AUTO input with normalized percentages and concrete exit rules', () => {
    const draft: EntryPlanDraft = {
      ...defaultEntryPlanDraft,
      instrumentCode: '605499.SH',
      instrumentName: '东鹏饮料',
      executionScenario: 'LIVE_AUTO',
      targetMode: 'TARGET_POSITION_PCT',
      targetPositionPct: 20,
      maxPositionPct: 25,
      maxBuyPrice: 128,
    };

    const input = buildEntryPlanConfiguration(draft);

    expect(input.targetPolicy).toMatchObject({
      mode: 'TARGET_POSITION_PCT',
      targetPositionPct: 0.2,
      incrementalAmountCny: null,
      additionalVolume: null,
      maxPositionPct: 0.25,
    });
    expect(input.executionPolicy).toMatchObject({
      environment: 'LIVE',
      authorizationMode: 'AUTO',
    });
    expect(input.pacingPolicy.cashBufferPct).toBe(0.2);
    expect(input.exitProtection).toMatchObject({
      enabled: true,
      grossTakeProfitPct: 10,
      trailingArmProfitPct: 8,
      trailingDrawdownPct: 3,
    });
  });

  it('preserves hidden execution, completion and secondary-rule settings on update', () => {
    const source = makePlan({
      triggerRules: [
        {
          ruleId: 'trend-primary',
          ruleType: 'TREND_PULLBACK_CONFIRMATION',
          priority: 900,
          enabled: true,
          once: false,
          presetId: 'BALANCED',
          minPullbackPct: 2,
          maxPullbackPct: 5,
          reboundConfirmationPct: 0.8,
          fastEmaPeriod: 10,
          slowEmaPeriod: 30,
          ladderLevels: [],
        },
        {
          ruleId: 'manual-fallback',
          ruleType: 'MANUAL_TRIGGER',
          priority: 100,
          enabled: true,
          once: false,
          presetId: '',
          manualTriggerSequence: 7,
          ladderLevels: [],
        },
      ],
      pacingPolicy: {
        trancheCount: 4,
        maxSingleIntentAmountCny: 5000,
        maxDailyFilledAmountCny: 10000,
        maxOrdersPerDay: 2,
        minIntervalSeconds: 1800,
        cooldownAfterRejectSeconds: 240,
        trendAdjustmentEnabled: false,
        cashBufferPct: 0.25,
      },
      executionPolicy: {
        priceReference: 'LATEST_PROTECTED_LIMIT',
        maxSlippageBps: 15,
        maxPriceDeviationBps: 12,
        approvalTtlMs: 20_000,
      },
      completionPolicy: {
        expireAtMs: 1_800_000_000_000,
        maxBuyPrice: 128,
        stopWhenTargetReached: false,
        stopWhenBudgetExhausted: true,
        cancelUnsubmittedOnExpiry: false,
      },
    });
    const draft: EntryPlanDraft = {
      ...defaultEntryPlanDraft,
      instrumentCode: source.instrumentCode,
      instrumentName: source.instrumentName,
      maxBuyPrice: 128,
      fastEmaPeriod: 12,
      cashBufferPct: 25,
    };

    const input = buildEntryPlanConfiguration(draft, source);

    expect(input.triggerRules).toHaveLength(2);
    expect(input.triggerRules[0]).toMatchObject({
      ruleId: 'trend-primary',
      fastEmaPeriod: 12,
      maxPullbackPct: 5,
    });
    expect(input.triggerRules[1]).toMatchObject({
      ruleId: 'manual-fallback',
      manualTriggerSequence: 7,
    });
    expect(input.pacingPolicy).toMatchObject({
      maxOrdersPerDay: 2,
      cooldownAfterRejectSeconds: 240,
      trendAdjustmentEnabled: false,
      cashBufferPct: 0.25,
    });
    expect(input.executionPolicy).toMatchObject({
      priceReference: 'LATEST_PROTECTED_LIMIT',
      maxSlippageBps: 15,
      maxPriceDeviationBps: 12,
      approvalTtlMs: 20_000,
    });
    expect(input.completionPolicy).toMatchObject({
      expireAtMs: 1_800_000_000_000,
      stopWhenTargetReached: false,
      cancelUnsubmittedOnExpiry: false,
    });
  });
});
