import type {
  EntryExecutionScenario,
  EntryPlanDraft,
  EntryPlanCapabilitiesView,
  EntryPlanEventKind,
  EntryPlanEventView,
  EntryPlanStatus,
  EntryPlanStrategy,
  EntryPlanView,
  EntryPlanWorkspaceView,
  PendingEntryIntentView,
} from './types';

type UnknownRecord = Record<string, unknown>;

export interface EntryPlanAccountSnapshot {
  id: string;
  totalAsset: number;
  cash: number;
  updateTime?: string | null;
}

export interface EntryPlanPositionSnapshot {
  stockCode: string;
  instrumentName?: string | null;
  volume: number;
  lastPrice?: number | null;
  marketValue?: number | null;
}

export interface EntryPlanProjection {
  planId: string;
  configVersion: number;
  instrumentCode: string;
  instrumentName: string;
  bucket: string;
  phase: string;
  runStatus: string;
  environment: string;
  authorizationMode: string;
  authorizationState: string;
  targetMode: string;
  targetPositionPct: number;
  incrementalAmountCny: number;
  additionalVolume: number;
  maxTotalAmountCny: number;
  maxPositionPct: number;
  currentPositionVolume: number;
  currentMarketValueCny: number;
  filledAmountCny: number;
  maxSingleIntentAmountCny: number;
  maxDailyFilledAmountCny: number;
  maxBuyPrice: number;
  ruleTypes: string[];
  triggerRules?: Array<{
    ruleId: string;
    ruleType: string;
    priority: number;
    enabled: boolean;
    once: boolean;
    presetId: string;
    minPullbackPct?: number | null;
    maxPullbackPct?: number | null;
    reboundConfirmationPct?: number | null;
    fastEmaPeriod?: number | null;
    slowEmaPeriod?: number | null;
    manualTriggerSequence?: number | null;
    ladderLevels?: Array<{
      levelId: string;
      triggerPrice: number;
      trancheAmountCny?: number | null;
      trancheVolume?: number | null;
      priority: number;
    }> | null;
  }> | null;
  pacingPolicy?: {
    trancheCount: number;
    maxSingleIntentAmountCny: number;
    maxDailyFilledAmountCny: number;
    maxOrdersPerDay: number;
    minIntervalSeconds: number;
    cooldownAfterRejectSeconds: number;
    trendAdjustmentEnabled: boolean;
    cashBufferPct?: number | null;
  } | null;
  executionPolicy?: {
    priceReference: string;
    maxSlippageBps: number;
    maxPriceDeviationBps: number;
    approvalTtlMs: number;
  } | null;
  completionPolicy?: {
    expireAtMs?: number | null;
    maxBuyPrice: number;
    stopWhenTargetReached: boolean;
    stopWhenBudgetExhausted: boolean;
    cancelUnsubmittedOnExpiry: boolean;
  } | null;
  exitProtection?: {
    enabled: boolean;
    stopPrice?: number | null;
    grossTakeProfitPct?: number | null;
    trailingArmProfitPct?: number | null;
    trailingDrawdownPct?: number | null;
    maxHoldingDays?: number | null;
  } | null;
  entryEnabled?: boolean;
  note?: string;
  lastReasonCode: string;
  pendingIntentId: string;
  hasWorkingOrder: boolean;
  nextEligibleAt?: number | null;
  expireAt?: number | null;
  hasExitProtection: boolean;
  updatedAt?: string | null;
}

export interface EntryIntentProjection {
  intentId: string;
  planId: string;
  instrumentCode: string;
  bucket: string;
  reasonCode: string;
  targetAmountCny: number;
  targetVolume: number;
  signalPrice: number;
  currentPrice: number;
  expiresAtMs: number;
  riskAction: string;
  createdAt?: string | null;
}

export interface EntryEventProjection {
  eventId: string;
  planId: string;
  eventType: string;
  occurredAt?: string | null;
  reasonCode: string;
  message: string;
  details: unknown;
}

export interface EntryPlanWorkspaceProjection {
  currentAccount?: EntryPlanAccountSnapshot | null;
  positions?: EntryPlanPositionSnapshot[] | null;
  entryPlans?: EntryPlanProjection[] | null;
  pendingEntryIntents?: EntryIntentProjection[] | null;
  entryAutomationStatus?: {
    paused: boolean;
    reason: string;
    updatedAt?: string | null;
  } | null;
  entryPlanCapabilities?: {
    version: string;
    targetModes?: Array<{
      value: string;
      label: string;
      description: string;
    }> | null;
    ruleTypes?: Array<{
      ruleType: string;
      label: string;
      category: string;
      description: string;
      suitableFor: string;
      warning: string;
      fields?: Array<{
        key: string;
        label: string;
        type: string;
        unit?: string | null;
        required?: boolean;
        min?: number | null;
        max?: number | null;
        step?: number | null;
        helpText?: string | null;
        advanced?: boolean;
      }> | null;
      presets?: Array<{
        presetId: string;
        label: string;
        summary: string;
        parameters?: unknown;
      }> | null;
    }> | null;
  } | null;
}

const statusValues = new Set<EntryPlanStatus>([
  'ARMED',
  'ACCUMULATING',
  'AWAITING_APPROVAL',
  'ENTRY_PENDING',
  'PAUSED',
  'DRAINING',
  'COMPLETED',
  'EXPIRED',
  'CANCELLED',
  'ERROR',
]);

const strategyValues = new Set<EntryPlanStrategy>([
  'TREND_PULLBACK_CONFIRMATION',
  'PRICE_LADDER',
  'MANUAL_TRIGGER',
]);

const reasonLabels: Record<string, string> = {
  ENTRY_PLAN_EVALUATED: '已按最新行情与账户风控检查',
  ENTRY_TARGET_REACHED: '真实持仓已达到目标',
  ENTRY_BUDGET_EXHAUSTED: '累计预算已用完',
  ENTRY_MAX_BUY_PRICE_EXCEEDED: '当前价格高于最高可买价',
  ENTRY_PENDING_LOCKED: '等待上一笔委托与成交回报收敛',
  ENTRY_RULE_NOT_TRIGGERED: '规则尚未触发，继续监控',
  ENTRY_AUTOMATION_PAUSED: '账户自动买入安全门已暂停',
};

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function epochToIso(value: unknown): string | null {
  const parsed = numberValue(value);
  if (parsed <= 0) return null;
  const date = new Date(parsed);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function objectValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function normalizeStatus(phase: string, runStatus: string): EntryPlanStatus {
  const candidate = phase.toUpperCase() as EntryPlanStatus;
  if (statusValues.has(candidate)) return candidate;
  if (phase.toUpperCase() === 'RECONCILE_REQUIRED') return 'ERROR';
  if (runStatus.toUpperCase() === 'PAUSED') return 'PAUSED';
  if (runStatus.toUpperCase() === 'ERROR') return 'ERROR';
  return 'ARMED';
}

function normalizeStrategy(ruleTypes: string[]): EntryPlanStrategy {
  const candidate = String(
    ruleTypes[0] ?? ''
  ).toUpperCase() as EntryPlanStrategy;
  return strategyValues.has(candidate)
    ? candidate
    : 'TREND_PULLBACK_CONFIRMATION';
}

function executionScenario(
  environment: string,
  authorizationMode: string
): EntryExecutionScenario {
  if (environment.toUpperCase() !== 'LIVE') return 'PAPER_AUTO';
  return authorizationMode.toUpperCase() === 'AUTO'
    ? 'LIVE_AUTO'
    : 'LIVE_MANUAL';
}

function authorizationLabel(plan: EntryPlanProjection): string {
  if (plan.environment.toUpperCase() !== 'LIVE') return '模拟自动执行';
  if (plan.authorizationMode.toUpperCase() !== 'AUTO') return '实盘逐笔确认';
  const labels: Record<string, string> = {
    AUTHORIZED: '实盘自动授权有效',
    EXPIRED: '自动授权已过期',
    INVALID: '自动授权已失效',
    PAUSED: '自动买入安全门暂停',
    REQUIRED: '等待实盘自动授权',
    REVOKED: '自动授权已撤销',
    STALE: '配置变化，需重新授权',
  };
  return labels[plan.authorizationState.toUpperCase()] ?? '等待实盘自动授权';
}

function eventKind(eventType: string): EntryPlanEventKind {
  const value = eventType.toUpperCase();
  if (value.includes('FILLED')) return 'TRADE_FILLED';
  if (value.includes('AWAITING_APPROVAL')) return 'APPROVAL_REQUIRED';
  if (value.includes('REJECTED')) return 'REJECTED';
  if (value.includes('APPROVED')) return 'APPROVED';
  if (value.includes('SUBMITTED') || value.includes('CREATED')) {
    return value.includes('INTENT') ? 'TRIGGERED' : 'EVALUATED';
  }
  if (value.includes('PAUSED')) return 'PAUSED';
  if (value.includes('RESUMED')) return 'RESUMED';
  if (value.includes('AUTHORIZATION')) return 'AUTHORIZATION_CHANGED';
  return 'EVALUATED';
}

function shanghaiDay(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

function filledAmount(event: EntryEventProjection): number {
  if (!event.eventType.toUpperCase().includes('FILLED')) return 0;
  const details = objectValue(event.details);
  return (
    numberValue(details.executed_volume) * numberValue(details.executed_price)
  );
}

export function mapEntryPlanEvents(
  events: EntryEventProjection[],
  planById: ReadonlyMap<string, EntryPlanProjection>
): EntryPlanEventView[] {
  return events
    .map(event => {
      const plan = planById.get(event.planId);
      const details = objectValue(event.details);
      const amountCny = filledAmount(event);
      return {
        id: event.eventId,
        occurredAt: event.occurredAt ?? '',
        instrumentCode: plan?.instrumentCode ?? '',
        instrumentName: plan?.instrumentName ?? plan?.instrumentCode ?? '',
        kind: eventKind(event.eventType),
        title: event.message || event.reasonCode || '计划状态更新',
        description:
          event.reasonCode && event.reasonCode !== event.message
            ? `${event.message}（${event.reasonCode}）`
            : event.message,
        amountCny: amountCny > 0 ? amountCny : null,
        volume: numberValue(details.executed_volume) || null,
        traceId: String(details.trace_id ?? '') || null,
      };
    })
    .sort((left, right) => right.occurredAt.localeCompare(left.occurredAt));
}

export function mapEntryPlanWorkspace(
  data: EntryPlanWorkspaceProjection | null | undefined,
  rawEvents: EntryEventProjection[],
  runtimeError?: string | null
): EntryPlanWorkspaceView {
  const account = data?.currentAccount;
  const plans = [...(data?.entryPlans ?? [])];
  const positions = new Map(
    (data?.positions ?? []).map(position => [position.stockCode, position])
  );
  const planById = new Map(plans.map(plan => [plan.planId, plan]));
  const events = mapEntryPlanEvents(rawEvents, planById);
  const today = shanghaiDay(new Date().toISOString());
  const dailyFilledByPlan = new Map<string, number>();
  rawEvents.forEach(event => {
    if (shanghaiDay(event.occurredAt) !== today) return;
    const amount = filledAmount(event);
    dailyFilledByPlan.set(
      event.planId,
      (dailyFilledByPlan.get(event.planId) ?? 0) + amount
    );
  });

  const planViews: EntryPlanView[] = plans.map(plan => {
    const position = positions.get(plan.instrumentCode);
    const totalAsset = numberValue(account?.totalAsset);
    const currentMarketValue = numberValue(plan.currentMarketValueCny);
    const dailyFilled = dailyFilledByPlan.get(plan.planId) ?? 0;
    const primaryRule = [...(plan.triggerRules ?? [])]
      .filter(rule => rule.enabled)
      .sort((left, right) => right.priority - left.priority)[0];
    const resolvedStrategy = normalizeStrategy(
      primaryRule ? [primaryRule.ruleType] : plan.ruleTypes
    );
    const resolvedPreset =
      primaryRule?.presetId === 'CONSERVATIVE' ||
      primaryRule?.presetId === 'ACTIVE'
        ? primaryRule.presetId
        : 'BALANCED';
    const pacing = plan.pacingPolicy;
    const protection = plan.exitProtection;
    const resolvedExecutionScenario = executionScenario(
      plan.environment,
      plan.authorizationMode
    );
    return {
      id: plan.planId,
      configVersion: Math.max(1, numberValue(plan.configVersion)),
      instrumentCode: plan.instrumentCode,
      instrumentName: plan.instrumentName || plan.instrumentCode,
      bucket: plan.bucket === 'swing' ? 'swing' : 'core',
      status: normalizeStatus(plan.phase, plan.runStatus),
      strategy: resolvedStrategy,
      primaryRuleId: primaryRule?.ruleId,
      currentPositionPct:
        totalAsset > 0 ? (currentMarketValue / totalAsset) * 100 : 0,
      currentPositionVolume: numberValue(plan.currentPositionVolume),
      latestPrice: position?.lastPrice ?? null,
      targetMode:
        plan.targetMode === 'INCREMENTAL_AMOUNT_CNY' ||
        plan.targetMode === 'ADDITIONAL_VOLUME'
          ? plan.targetMode
          : 'TARGET_POSITION_PCT',
      targetPositionPct:
        plan.targetMode === 'TARGET_POSITION_PCT'
          ? numberValue(plan.targetPositionPct) * 100
          : null,
      incrementalAmountCny: numberValue(plan.incrementalAmountCny),
      additionalVolume: numberValue(plan.additionalVolume),
      filledAmountCny: numberValue(plan.filledAmountCny),
      maxTotalAmountCny: numberValue(plan.maxTotalAmountCny),
      maxPositionPct: numberValue(plan.maxPositionPct) * 100,
      maxSingleIntentAmountCny: numberValue(plan.maxSingleIntentAmountCny),
      maxDailyFilledAmountCny: numberValue(plan.maxDailyFilledAmountCny),
      dailyRemainingAmountCny: Math.max(
        0,
        numberValue(plan.maxDailyFilledAmountCny) - dailyFilled
      ),
      maxBuyPrice: numberValue(plan.maxBuyPrice),
      executionScenario: resolvedExecutionScenario,
      authorizationLabel: authorizationLabel(plan),
      lastDecision:
        reasonLabels[plan.lastReasonCode] ||
        plan.lastReasonCode ||
        '等待下一次评估',
      nextEvaluationAt: epochToIso(plan.nextEligibleAt),
      expiresAt: epochToIso(plan.expireAt),
      exitProtectionEnabled: Boolean(plan.hasExitProtection),
      hasWorkingOrder: Boolean(plan.hasWorkingOrder),
      hasPendingApproval: Boolean(plan.pendingIntentId),
      editableDraft: {
        planId: plan.planId,
        configVersion: Math.max(1, numberValue(plan.configVersion)),
        instrumentCode: plan.instrumentCode,
        instrumentName: plan.instrumentName || plan.instrumentCode,
        bucket: plan.bucket === 'swing' ? 'swing' : 'core',
        targetMode:
          plan.targetMode === 'INCREMENTAL_AMOUNT_CNY' ||
          plan.targetMode === 'ADDITIONAL_VOLUME'
            ? plan.targetMode
            : 'TARGET_POSITION_PCT',
        targetPositionPct: numberValue(plan.targetPositionPct) * 100,
        incrementalAmountCny: numberValue(plan.incrementalAmountCny),
        additionalVolume: numberValue(plan.additionalVolume),
        maxTotalAmountCny: numberValue(plan.maxTotalAmountCny),
        maxPositionPct: numberValue(plan.maxPositionPct) * 100,
        maxBuyPrice: numberValue(
          plan.completionPolicy?.maxBuyPrice ?? plan.maxBuyPrice
        ),
        strategy: resolvedStrategy,
        preset: resolvedPreset,
        priceLadderLevels: (primaryRule?.ladderLevels ?? []).map(level => ({
          levelId: level.levelId,
          triggerPrice: numberValue(level.triggerPrice),
          trancheMode:
            numberValue(level.trancheVolume) > 0 ? 'VOLUME' : 'AMOUNT',
          trancheAmountCny: numberValue(level.trancheAmountCny),
          trancheVolume: numberValue(level.trancheVolume),
        })),
        trancheCount: Math.max(1, numberValue(pacing?.trancheCount)),
        maxSingleIntentAmountCny: numberValue(
          pacing?.maxSingleIntentAmountCny ?? plan.maxSingleIntentAmountCny
        ),
        maxDailyFilledAmountCny: numberValue(
          pacing?.maxDailyFilledAmountCny ?? plan.maxDailyFilledAmountCny
        ),
        minIntervalMinutes: numberValue(pacing?.minIntervalSeconds) / 60,
        cashBufferPct:
          pacing?.cashBufferPct === null || pacing?.cashBufferPct === undefined
            ? 20
            : numberValue(pacing.cashBufferPct) * 100,
        executionScenario: resolvedExecutionScenario,
        exitProtectionEnabled: Boolean(protection?.enabled),
        exitStopPrice: numberValue(protection?.stopPrice),
        exitGrossTakeProfitPct: numberValue(protection?.grossTakeProfitPct),
        exitTrailingArmProfitPct: numberValue(protection?.trailingArmProfitPct),
        exitTrailingDrawdownPct: numberValue(protection?.trailingDrawdownPct),
        exitMaxHoldingDays: numberValue(protection?.maxHoldingDays),
        fastEmaPeriod: numberValue(primaryRule?.fastEmaPeriod) || 10,
        slowEmaPeriod: numberValue(primaryRule?.slowEmaPeriod) || 30,
        pullbackPct: numberValue(primaryRule?.minPullbackPct) || 2,
        reboundPct: numberValue(primaryRule?.reboundConfirmationPct) || 0.8,
      },
    };
  });

  const pendingIntents: PendingEntryIntentView[] = (
    data?.pendingEntryIntents ?? []
  ).map(intent => {
    const plan = planById.get(intent.planId);
    const position = positions.get(intent.instrumentCode);
    return {
      id: intent.intentId,
      planId: intent.planId,
      instrumentCode: intent.instrumentCode,
      instrumentName:
        plan?.instrumentName ??
        position?.instrumentName ??
        intent.instrumentCode,
      bucket: intent.bucket === 'swing' ? 'swing' : 'core',
      strategy: normalizeStrategy(plan?.ruleTypes ?? []),
      signalAt: intent.createdAt ?? '',
      expiresAt: epochToIso(intent.expiresAtMs) ?? '',
      referencePrice: numberValue(intent.signalPrice),
      currentAskPrice:
        numberValue(intent.currentPrice) ||
        numberValue(position?.lastPrice) ||
        numberValue(intent.signalPrice),
      expectedAmountCny: numberValue(intent.targetAmountCny),
      candidateVolume: numberValue(intent.targetVolume),
      riskAction: intent.riskAction || '确认时重新执行实时风控',
      planFilledAmountCny: numberValue(plan?.filledAmountCny),
      dailyFilledAmountCny: dailyFilledByPlan.get(intent.planId) ?? 0,
      cashBufferPct: -1,
    };
  });

  const capabilityVersion = data?.entryPlanCapabilities?.version;
  const rawCapabilities = data?.entryPlanCapabilities;
  const capabilities: EntryPlanCapabilitiesView | undefined = rawCapabilities
    ? {
        version: rawCapabilities.version,
        targetModes: [...(rawCapabilities.targetModes ?? [])],
        ruleTypes: (rawCapabilities.ruleTypes ?? []).map(rule => ({
          ...rule,
          fields: [...(rule.fields ?? [])],
          presets: (rule.presets ?? []).map(preset => ({
            ...preset,
            parameters: recordFromUnknown(preset.parameters),
          })),
        })),
      }
    : undefined;
  const runtimeMessage = runtimeError
    ? `数据读取失败 · ${runtimeError}`
    : account
      ? `账户快照已同步${capabilityVersion ? ` · ${capabilityVersion}` : ''}`
      : '正在加载账户与买入计划';

  return {
    availableCashCny: numberValue(account?.cash),
    todayFilledAmountCny: Array.from(dailyFilledByPlan.values()).reduce(
      (total, value) => total + value,
      0
    ),
    globalAutoEntryPaused: Boolean(data?.entryAutomationStatus?.paused),
    plans: planViews,
    pendingIntents,
    events,
    dataUpdatedAt:
      data?.entryAutomationStatus?.updatedAt ?? account?.updateTime ?? null,
    runtimeMessage,
    capabilities,
  };
}

function targetFieldValues(draft: EntryPlanDraft) {
  return {
    targetPositionPct:
      draft.targetMode === 'TARGET_POSITION_PCT'
        ? draft.targetPositionPct / 100
        : null,
    incrementalAmountCny:
      draft.targetMode === 'INCREMENTAL_AMOUNT_CNY'
        ? draft.incrementalAmountCny
        : null,
    additionalVolume:
      draft.targetMode === 'ADDITIONAL_VOLUME' ? draft.additionalVolume : null,
  };
}

function entryRuleInput(
  draft: EntryPlanDraft,
  source?: NonNullable<EntryPlanProjection['triggerRules']>[number]
) {
  if (draft.strategy === 'PRICE_LADDER') {
    return {
      ruleId:
        source?.ruleType === draft.strategy ? source.ruleId : 'price-ladder',
      ruleType: draft.strategy,
      priority: source?.priority ?? 100,
      enabled: source?.enabled ?? true,
      once: source?.once ?? false,
      presetId: draft.preset,
      ladderLevels: draft.priceLadderLevels.map((level, index) => ({
        levelId: level.levelId,
        triggerPrice: level.triggerPrice,
        trancheAmountCny:
          level.trancheMode === 'AMOUNT' ? level.trancheAmountCny : null,
        trancheVolume:
          level.trancheMode === 'VOLUME' ? level.trancheVolume : null,
        priority: draft.priceLadderLevels.length - index,
      })),
    };
  }
  return {
    ruleId:
      source?.ruleType === draft.strategy
        ? source.ruleId
        : draft.strategy === 'MANUAL_TRIGGER'
          ? 'manual-trigger'
          : 'trend-pullback-confirmation',
    ruleType: draft.strategy,
    priority: source?.priority ?? 100,
    enabled: source?.enabled ?? true,
    once: source?.once ?? false,
    presetId: draft.preset,
    minPullbackPct:
      draft.strategy === 'TREND_PULLBACK_CONFIRMATION'
        ? draft.pullbackPct
        : null,
    maxPullbackPct:
      draft.strategy === 'TREND_PULLBACK_CONFIRMATION'
        ? (source?.maxPullbackPct ?? draft.pullbackPct)
        : null,
    reboundConfirmationPct:
      draft.strategy === 'TREND_PULLBACK_CONFIRMATION'
        ? draft.reboundPct
        : null,
    fastEmaPeriod:
      draft.strategy === 'TREND_PULLBACK_CONFIRMATION'
        ? draft.fastEmaPeriod
        : null,
    slowEmaPeriod:
      draft.strategy === 'TREND_PULLBACK_CONFIRMATION'
        ? draft.slowEmaPeriod
        : null,
    manualTriggerSequence:
      draft.strategy === 'MANUAL_TRIGGER'
        ? (source?.manualTriggerSequence ?? 1)
        : null,
    ladderLevels: [],
  };
}

export function buildEntryPlanConfiguration(
  draft: EntryPlanDraft,
  source?: EntryPlanProjection
) {
  const environment =
    draft.executionScenario === 'PAPER_AUTO' ? 'PAPER' : 'LIVE';
  const authorizationMode =
    draft.executionScenario === 'LIVE_MANUAL' ? 'MANUAL_CONFIRM' : 'AUTO';

  const sourceRules = [...(source?.triggerRules ?? [])];
  const sourcePrimary = sourceRules
    .filter(rule => rule.enabled)
    .sort((left, right) => right.priority - left.priority)[0];
  const primaryRule = entryRuleInput(draft, sourcePrimary);
  const triggerRules = sourcePrimary
    ? sourceRules.map(rule =>
        rule.ruleId === sourcePrimary.ruleId
          ? primaryRule
          : {
              ruleId: rule.ruleId,
              ruleType: rule.ruleType,
              priority: rule.priority,
              enabled: rule.enabled,
              once: rule.once,
              presetId: rule.presetId || null,
              minPullbackPct: rule.minPullbackPct ?? null,
              maxPullbackPct: rule.maxPullbackPct ?? null,
              reboundConfirmationPct: rule.reboundConfirmationPct ?? null,
              fastEmaPeriod: rule.fastEmaPeriod ?? null,
              slowEmaPeriod: rule.slowEmaPeriod ?? null,
              manualTriggerSequence: rule.manualTriggerSequence ?? null,
              ladderLevels: (rule.ladderLevels ?? []).map(level => ({
                levelId: level.levelId,
                triggerPrice: level.triggerPrice,
                trancheAmountCny: level.trancheAmountCny ?? null,
                trancheVolume: level.trancheVolume ?? null,
                priority: level.priority,
              })),
            }
      )
    : [primaryRule];

  return {
    targetPolicy: {
      mode: draft.targetMode,
      ...targetFieldValues(draft),
      maxTotalAmountCny: draft.maxTotalAmountCny,
      maxPositionPct: draft.maxPositionPct / 100,
    },
    triggerRules,
    pacingPolicy: {
      trancheCount: draft.trancheCount,
      maxSingleIntentAmountCny: draft.maxSingleIntentAmountCny,
      maxDailyFilledAmountCny: draft.maxDailyFilledAmountCny,
      maxOrdersPerDay:
        source?.pacingPolicy?.maxOrdersPerDay ??
        Math.max(1, draft.trancheCount),
      cashBufferPct: draft.cashBufferPct / 100,
      minIntervalSeconds: draft.minIntervalMinutes * 60,
      cooldownAfterRejectSeconds:
        source?.pacingPolicy?.cooldownAfterRejectSeconds ?? 60,
      trendAdjustmentEnabled:
        source?.pacingPolicy?.trendAdjustmentEnabled ??
        draft.strategy === 'TREND_PULLBACK_CONFIRMATION',
    },
    executionPolicy: {
      environment,
      authorizationMode,
      priceReference:
        source?.executionPolicy?.priceReference ?? 'ASK1_PROTECTED_LIMIT',
      maxSlippageBps: source?.executionPolicy?.maxSlippageBps ?? 20,
      maxPriceDeviationBps: source?.executionPolicy?.maxPriceDeviationBps ?? 20,
      approvalTtlMs: source?.executionPolicy?.approvalTtlMs ?? 15_000,
    },
    completionPolicy: {
      expireAtMs: source?.completionPolicy?.expireAtMs ?? null,
      maxBuyPrice: draft.maxBuyPrice,
      stopWhenTargetReached:
        source?.completionPolicy?.stopWhenTargetReached ?? true,
      stopWhenBudgetExhausted:
        source?.completionPolicy?.stopWhenBudgetExhausted ?? true,
      cancelUnsubmittedOnExpiry:
        source?.completionPolicy?.cancelUnsubmittedOnExpiry ?? true,
    },
    exitProtection: draft.exitProtectionEnabled
      ? {
          enabled: true,
          stopPrice: draft.exitStopPrice > 0 ? draft.exitStopPrice : null,
          grossTakeProfitPct:
            draft.exitGrossTakeProfitPct > 0
              ? draft.exitGrossTakeProfitPct
              : null,
          trailingArmProfitPct:
            draft.exitTrailingArmProfitPct > 0
              ? draft.exitTrailingArmProfitPct
              : null,
          trailingDrawdownPct:
            draft.exitTrailingDrawdownPct > 0
              ? draft.exitTrailingDrawdownPct
              : null,
          maxHoldingDays:
            draft.exitMaxHoldingDays > 0 ? draft.exitMaxHoldingDays : null,
        }
      : null,
  };
}

function recordFromUnknown(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
