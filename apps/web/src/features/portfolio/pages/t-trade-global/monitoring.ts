import { isTradingHours } from '@/shared/utils/date';

export type EvaluationTelemetry = {
  phase: string;
  lastTickAt: string;
  processedTickCount: number;
  windowSampleCount: number;
  windowCoverageSeconds: number;
  triggered: boolean;
  reason: string;
  signalType: string;
  signalPrice: number;
  windowHigh?: number | null;
  windowLow?: number | null;
  pullbackPct?: number | null;
  reboundPct?: number | null;
  vwap?: number | null;
  vwapPremiumPct?: number | null;
  spreadTicks?: number | null;
  spreadPct?: number | null;
  momentumRisePct?: number | null;
  momentumMoveSeconds?: number | null;
  momentumAmountVelocityRatio?: number | null;
  momentumBaselineCoverageSeconds?: number | null;
};

export type MonitorSession = {
  runId: string;
  stockCode?: string;
  runStatus: string;
  status: string;
  mode: string;
  currentSignal?: unknown;
  pendingEntryIntentId?: string | null;
  pendingExitIntentId?: string | null;
  entryOrderStatus: string;
  exitOrderStatus: string;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  lastPrice?: number;
  lastNetProfitPct: number;
  peakNetProfitPct: number;
  trailingFloorPct?: number | null;
  targetProfitPct?: number;
  profitArmed: boolean;
  lastExitReason: string;
  completedCycles: number;
  canCancel: boolean;
  errorMessage?: string | null;
  latestEvaluation?: EvaluationTelemetry | null;
};

export type MonitorHolding = {
  stockCode: string;
  instrumentName: string;
  volume: number;
  availableVolume: number;
  ignored: boolean;
  eligible: boolean;
  status: string;
  reason: string;
  session?: MonitorSession | null;
};

export type MonitorConfig = {
  signalLookbackSeconds: number;
  stabilizationSeconds: number;
  pullbackThresholdPct: number;
  reboundThresholdPct: number;
  maxSpreadTicks: number;
  momentumEnabled: boolean;
  momentumWindowSeconds: number;
  momentumMinRisePct: number;
  momentumMinMoveSeconds: number;
  momentumBaselineSeconds: number;
  momentumMinAmountVelocityRatio: number;
  momentumMinVwapPremiumPct: number;
  momentumMaxVwapPremiumPct: number;
  momentumMaxSpreadTicks: number;
  momentumMaxSpreadPct: number;
};

export type FreshnessLevel = 'LIVE' | 'DELAYED' | 'STALE' | 'CLOSED' | 'MISSING';

export type Freshness = {
  ageSeconds: number | null;
  label: string;
  level: FreshnessLevel;
};

const evaluationReasonLabels: Record<string, string> = {
  TICK_PROCESSED: '本 Tick 已处理',
  INSUFFICIENT_TICKS: '积累观察样本',
  WAITING_PULLBACK: '等待回撤幅度',
  WAITING_REBOUND: '等待企稳反弹',
  WAITING_STABILIZATION: '等待走势稳定',
  WAITING_MOMENTUM_RISE: '等待动量涨幅',
  WAITING_MOMENTUM_DURATION: '等待动量持续',
  WAITING_AMOUNT_ACCELERATION: '等待成交加速',
  WAITING_VWAP_PREMIUM: '等待 VWAP 溢价',
  VWAP_PREMIUM_TOO_HIGH: 'VWAP 溢价过高',
  SPREAD_TOO_WIDE: '买卖价差过宽',
  INTENT_PENDING: '已有指令等待确认',
  COOLDOWN_ACTIVE: '批次冷却中',
  END_OF_DAY_ENTRY_BLOCKED: '临近收盘，停止新开批次',
  WAITING_FOR_EXIT_PLAN_REGISTRATION: '等待退出计划注册',
  MONITOR_ENGINE_EXIT_PLAN: '持续评估退出计划',
  PULLBACK_REBOUND_TRIGGERED: '回撤反弹机会已触发',
  MOMENTUM_ACCELERATION_TRIGGERED: '动量加速机会已触发',
};

export function evaluationReasonLabel(reason?: string | null) {
  const normalized = String(reason || '').toUpperCase();
  return evaluationReasonLabels[normalized] || reason || '等待首个有效 Tick';
}

export function classifyFreshness(
  value: string | null | undefined,
  now: Date,
  kind: 'QUOTE' | 'HEARTBEAT',
  isCurrentTradingDay?: boolean
): Freshness {
  if (isCurrentTradingDay === false || !isTradingHours(now)) {
    return { ageSeconds: null, label: '休市·最近快照', level: 'CLOSED' };
  }
  if (!value) {
    return { ageSeconds: null, label: '等待数据', level: 'MISSING' };
  }
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) {
    return { ageSeconds: null, label: '时间异常', level: 'STALE' };
  }
  const ageSeconds = Math.max(0, (now.getTime() - timestamp) / 1000);
  const liveLimit = kind === 'QUOTE' ? 5 : 15;
  const delayedLimit = kind === 'QUOTE' ? 15 : 30;
  if (ageSeconds <= liveLimit) {
    return { ageSeconds, label: kind === 'QUOTE' ? '实时' : '心跳正常', level: 'LIVE' };
  }
  if (ageSeconds <= delayedLimit) {
    return { ageSeconds, label: '延迟', level: 'DELAYED' };
  }
  return { ageSeconds, label: '陈旧', level: 'STALE' };
}

function safeRatio(value?: number | null, threshold?: number | null) {
  if (!threshold || threshold <= 0) return 0;
  return Math.max(0, Number(value || 0) / threshold);
}

export function conditionProgress(
  evaluation: EvaluationTelemetry | null | undefined,
  config: MonitorConfig
) {
  if (!evaluation) return 0;
  const pullbackPath = Math.min(
    safeRatio(evaluation.pullbackPct, config.pullbackThresholdPct),
    safeRatio(evaluation.reboundPct, config.reboundThresholdPct)
  );
  const momentumPath = config.momentumEnabled
    ? Math.min(
        safeRatio(evaluation.momentumRisePct, config.momentumMinRisePct),
        safeRatio(
          evaluation.momentumMoveSeconds,
          config.momentumMinMoveSeconds
        ),
        safeRatio(
          evaluation.momentumAmountVelocityRatio,
          config.momentumMinAmountVelocityRatio
        )
      )
    : 0;
  return Math.min(1, Math.max(pullbackPath, momentumPath));
}

export type AttentionRow<TQuote = unknown> = {
  attentionLevel: number;
  conditionProgress: number;
  heartbeatFreshness: Freshness;
  holding: MonitorHolding;
  quote?: TQuote;
  quoteFreshness: Freshness;
  session?: MonitorSession | null;
};

export function buildAttentionRows<TQuote extends { time: string }>(
  holdings: readonly MonitorHolding[],
  sessions: readonly MonitorSession[],
  quotes: ReadonlyMap<string, TQuote>,
  config: MonitorConfig,
  now: Date,
  isCurrentTradingDay?: boolean
): AttentionRow<TQuote>[] {
  const sessionsByCode = new Map(
    sessions
      .filter(session => session.stockCode)
      .map(session => [String(session.stockCode).toUpperCase(), session])
  );
  return holdings
    .map(holding => {
      const code = holding.stockCode.toUpperCase();
      const session = sessionsByCode.get(code) || holding.session;
      const quote = quotes.get(holding.stockCode) || quotes.get(code);
      const quoteFreshness = classifyFreshness(
        quote?.time,
        now,
        'QUOTE',
        isCurrentTradingDay
      );
      const heartbeatFreshness = classifyFreshness(
        session?.latestEvaluation?.lastTickAt,
        now,
        'HEARTBEAT',
        isCurrentTradingDay
      );
      const progress = conditionProgress(session?.latestEvaluation, config);
      const hasError = Boolean(
        session?.errorMessage ||
          ['ERROR', 'RECONCILE_REQUIRED', 'KILL_SWITCHED'].includes(
            String(session?.status || holding.status).toUpperCase()
          )
      );
      const pending = Boolean(
        session?.pendingEntryIntentId || session?.pendingExitIntentId
      );
      const staleDuringTrading =
        quoteFreshness.level === 'STALE' || quoteFreshness.level === 'MISSING';
      const activeOrDraining = Boolean(
        session?.activeVolume || holding.status === 'DRAINING'
      );
      const attentionLevel = hasError
        ? 0
        : pending
          ? 1
          : staleDuringTrading
            ? 2
            : activeOrDraining
              ? 3
              : progress >= 0.7
                ? 4
                : 5;
      return {
        attentionLevel,
        conditionProgress: progress,
        heartbeatFreshness,
        holding,
        quote,
        quoteFreshness,
        session,
      };
    })
    .sort(
      (left, right) =>
        left.attentionLevel - right.attentionLevel ||
        left.holding.stockCode.localeCompare(right.holding.stockCode)
    );
}
