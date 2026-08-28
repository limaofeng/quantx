import type {
  ExecutionTraceView,
  StrategyDecision,
} from '@/features/strategies/domain';

import type {
  ActivityBatch,
  ActivityBatchEvent,
  ActivitySignalEvaluation,
  TTradeActivityItem,
} from './activity';
import type { TTradePositionBatch } from './TTradePositionsView';

const DELETABLE_REPLAY_STATUSES = new Set([
  'COMPLETED',
  'ERROR',
  'FAILED',
  'CANCELLED',
  'STOPPED',
]);

export function canDeleteReplay(status: string | null | undefined) {
  return DELETABLE_REPLAY_STATUSES.has(String(status || '').toUpperCase());
}

export function replayStatusAfterDelete(
  historyRunIds: readonly string[],
  deletedRunId: string,
  activeRunId: string
) {
  if (activeRunId !== deletedRunId) return activeRunId;
  return historyRunIds.find(runId => runId !== deletedRunId) || '';
}

export type ReplayCycleLike = {
  batchId: string;
  stockCode: string;
  status: string;
  entryTime?: string | null;
  exitTime?: string | null;
  entryVolume: number;
  exitVolume: number;
  openVolume: number;
  entryAvgPrice: number;
  exitAvgPrice: number;
  netProfit: number;
  netReturnPct: number;
  exitReason?: string | null;
  liquidationStatus?: string | null;
  forcedExit?: boolean;
};

type ReplayReportLike = {
  status: string;
  generatedAt?: string | null;
  conclusionCode: string;
  conclusion: string;
};

export type ReplayActivityProjection = {
  runId: string;
  status: string;
  progressPct: number;
  phase: string;
  phaseMessage: string;
  processedUntil?: string | null;
  startTime: string;
  endTime: string;
  updatedAt?: string | null;
  errorMessage?: string | null;
  dataQuality: string;
  dataQualityMessage: string;
  skippedStockCodes: readonly string[];
  report?: ReplayReportLike | null;
};

function timestampOrFallback(
  value: string | null | undefined,
  fallback: string
) {
  return value && Number.isFinite(Date.parse(value)) ? value : fallback;
}

function compactDecisionSummary(decision: StrategyDecision) {
  const trace = decision.decisionTrace.find(item => Boolean(item.trim()));
  if (trace) return trace;
  const output = decision.outputSummary;
  for (const key of ['reason', 'result_summary', 'summary', 'status']) {
    const value = output[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return decision.tradeIntents.length
    ? `产生 ${decision.tradeIntents.length} 个 TradeIntent`
    : '本次决策未产生 TradeIntent';
}

export function mapReplayCyclesToPositionBatches(
  cycles: readonly ReplayCycleLike[],
  runId: string
): TTradePositionBatch[] {
  return cycles.map(cycle => ({
    batchId: cycle.batchId,
    stockCode: cycle.stockCode.toUpperCase(),
    strategyRunId: runId,
    status: cycle.status || (cycle.openVolume > 0 ? 'OPEN' : 'COMPLETED'),
    entryClientOrderId: null,
    exitClientOrderId: null,
    entryBrokerOrderId: null,
    exitBrokerOrderId: null,
    targetVolume: cycle.entryVolume,
    entryFilledVolume: cycle.entryVolume,
    entryAvgPrice: cycle.entryAvgPrice,
    exitFilledVolume: cycle.exitVolume,
    exitAvgPrice: cycle.exitAvgPrice,
    activeVolume: cycle.openVolume,
    lastPrice: cycle.exitAvgPrice || cycle.entryAvgPrice,
    netProfit: cycle.netProfit,
    lastNetProfitPct: cycle.netReturnPct,
    peakNetProfitPct: cycle.netReturnPct,
    trailingFloorPct: null,
    exitReason: cycle.exitReason || null,
    exceptionReason:
      cycle.liquidationStatus &&
      !['COMPLETED', 'SUCCESS', 'NONE'].includes(
        cycle.liquidationStatus.toUpperCase()
      )
        ? cycle.liquidationStatus
        : cycle.forcedExit
          ? '期末强制清算'
          : null,
  }));
}

export function mapReplayCyclesToActivityBatches(
  cycles: readonly ReplayCycleLike[],
  runId: string
): ActivityBatch[] {
  return mapReplayCyclesToPositionBatches(cycles, runId).map(batch => ({
    batchId: batch.batchId,
    stockCode: batch.stockCode,
    strategyRunId: batch.strategyRunId,
    status: batch.status,
    entryIntentId: null,
    exitIntentId: null,
    entryFilledVolume: batch.entryFilledVolume,
    entryAvgPrice: batch.entryAvgPrice,
    exitFilledVolume: batch.exitFilledVolume,
    exitAvgPrice: batch.exitAvgPrice,
    activeVolume: batch.activeVolume,
    lastPrice: batch.lastPrice,
    lastNetProfitPct: batch.lastNetProfitPct,
    peakNetProfitPct: batch.peakNetProfitPct,
    trailingFloorPct: null,
    exitReason: batch.exitReason,
    exceptionReason: batch.exceptionReason,
    version: 1,
    updatedAt: null,
  }));
}

function replayTradeEvent(
  cycle: ReplayCycleLike,
  runId: string,
  role: 'ENTRY' | 'EXIT'
): ActivityBatchEvent | null {
  const entry = role === 'ENTRY';
  const volume = entry ? cycle.entryVolume : cycle.exitVolume;
  const occurredAt = entry ? cycle.entryTime : cycle.exitTime;
  if (volume <= 0 || !occurredAt) return null;
  const direction = entry ? 'BUY' : 'SELL';
  const price = entry ? cycle.entryAvgPrice : cycle.exitAvgPrice;
  return {
    eventId: `replay:${cycle.batchId}:${role.toLowerCase()}`,
    batchId: cycle.batchId,
    eventType: 'TRADE',
    status: 'APPLIED',
    clientOrderId: `backtest:${cycle.batchId}:${role.toLowerCase()}`,
    brokerOrderId: null,
    payload: {
      report: {
        source: 'BACKTEST_BROKER',
        instrument_code: cycle.stockCode,
        direction,
        filled_volume: volume,
        filled_price: price,
        reported_at: occurredAt,
        execution_id: `backtest:${cycle.batchId}:${role.toLowerCase()}`,
      },
      metadata: {
        t_trade_role: role,
        strategy_run_id: runId,
      },
    },
    createdAt: occurredAt,
    appliedAt: occurredAt,
    error: null,
  };
}

export function mapReplayCyclesToActivityEvents(
  cycles: readonly ReplayCycleLike[],
  runId: string
): ActivityBatchEvent[] {
  return cycles.flatMap(cycle =>
    [
      replayTradeEvent(cycle, runId, 'ENTRY'),
      replayTradeEvent(cycle, runId, 'EXIT'),
    ].filter((event): event is ActivityBatchEvent => event !== null)
  );
}

export function mapReplayDecisionsToActivityEvaluations(
  decisions: readonly StrategyDecision[],
  runId: string,
  accountId: string
): ActivitySignalEvaluation[] {
  return decisions.map(decision => {
    const firstIntent = decision.tradeIntents[0];
    const inputCode = decision.inputSummary.instrument_code;
    const stockCode = String(
      firstIntent?.instrumentCode ||
        (typeof inputCode === 'string' ? inputCode : '')
    ).toUpperCase();
    const hasIntent = decision.tradeIntents.length > 0;
    return {
      id: `replay-decision:${decision.id}`,
      accountId,
      runId,
      stockCode,
      eventKind: 'MATERIAL',
      eventType: hasIntent ? 'INTENT_LINKED' : 'DECISION_RECORDED',
      evaluatedAt: decision.decidedAt,
      coalescedCount: 1,
      policyVersion: 'replay',
      title: hasIntent ? '回放交易意图' : '回放决策',
      summary: compactDecisionSummary(decision),
      signalSnapshot: null,
    };
  });
}

function executionFailed(execution: ExecutionTraceView) {
  return [
    execution.riskDecision,
    execution.orderStatus,
    execution.fillStatus,
  ].some(value => /REJECT|ERROR|FAILED|RECONCILE_REQUIRED/i.test(value || ''));
}

export function mapReplayExecutionsToActivityEvents(
  executions: readonly ExecutionTraceView[],
  fallbackTime: string
): ActivityBatchEvent[] {
  return executions.map(execution => {
    const occurredAt = timestampOrFallback(
      execution.updatedAt || execution.createdAt || execution.executedTime,
      fallbackTime
    );
    const status =
      execution.orderStatus ||
      execution.fillStatus ||
      execution.riskDecision ||
      'RECORDED';
    return {
      eventId: `replay-execution:${execution.id}`,
      batchId: execution.intentId || execution.traceId || execution.id,
      eventType: 'ORDER',
      status,
      clientOrderId: execution.orderId || execution.intentId || execution.id,
      brokerOrderId: execution.orderId || null,
      payload: {
        report: {
          source: 'BACKTEST_BROKER',
          instrument_code: execution.instrumentCode,
          direction: execution.side,
          effective_order_status: status,
          order_volume: execution.executedVolume,
          order_price: execution.executedPrice,
          reported_at: occurredAt,
        },
        metadata: {
          role: execution.side.toUpperCase() === 'SELL' ? 'EXIT' : 'ENTRY',
          risk_decision: execution.riskDecision,
          sizing_result: execution.sizingResult,
          fill_status: execution.fillStatus,
          trace_id: execution.traceId,
          reason: execution.reason,
        },
      },
      createdAt: occurredAt,
      appliedAt: occurredAt,
      error: executionFailed(execution) ? execution.reason || status : null,
    };
  });
}

export function replayProjectionActivityItems(
  replay: ReplayActivityProjection
): TTradeActivityItem[] {
  const occurredAt = timestampOrFallback(
    replay.updatedAt || replay.processedUntil,
    replay.endTime || replay.startTime
  );
  const normalizedStatus = replay.status.toUpperCase();
  const failed = ['ERROR', 'FAILED'].includes(normalizedStatus);
  const statusSummary = [
    replay.phaseMessage,
    `${replay.progressPct.toFixed(1)}%`,
    replay.dataQualityMessage,
    replay.errorMessage,
  ]
    .filter(Boolean)
    .join(' · ');
  const items: TTradeActivityItem[] = [
    {
      id: `replay-status:${replay.runId}:${normalizedStatus}`,
      occurredAt,
      stockCode: '',
      kind: failed ? 'ERROR' : 'DIAGNOSTIC',
      tone: failed
        ? 'rose'
        : normalizedStatus === 'COMPLETED'
          ? 'emerald'
          : 'blue',
      eventType: `REPLAY_${normalizedStatus}`,
      title: failed
        ? '回放执行失败'
        : `回放${normalizedStatus === 'COMPLETED' ? '已完成' : '状态'}`,
      summary: statusSummary || replay.phase,
      searchableText: [
        replay.runId,
        replay.status,
        replay.phase,
        replay.phaseMessage,
        replay.dataQuality,
        replay.dataQualityMessage,
        replay.errorMessage,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase(),
    },
  ];
  if (replay.report) {
    const report = replay.report;
    const healthyConclusion = new Set([
      'READY',
      'OK',
      'COMPLETED',
      'READY_FOR_EVALUATION',
    ]).has(report.conclusionCode.toUpperCase());
    items.push({
      id: `replay-report:${replay.runId}:${report.conclusionCode}`,
      occurredAt: timestampOrFallback(report.generatedAt, occurredAt),
      stockCode: '',
      kind: 'DIAGNOSTIC',
      tone: healthyConclusion ? 'emerald' : 'amber',
      eventType: report.conclusionCode,
      title: '回放报告结论',
      summary: report.conclusion || report.conclusionCode,
      searchableText: [report.status, report.conclusionCode, report.conclusion]
        .filter(Boolean)
        .join(' ')
        .toLowerCase(),
    });
  }
  if (replay.skippedStockCodes.length > 0) {
    items.push({
      id: `replay-skipped:${replay.runId}`,
      occurredAt,
      stockCode: '',
      kind: 'DIAGNOSTIC',
      tone: 'amber',
      eventType: 'REPLAY_INSTRUMENTS_SKIPPED',
      title: '回放跳过标的',
      summary: replay.skippedStockCodes.join('、'),
      searchableText: replay.skippedStockCodes.join(' ').toLowerCase(),
    });
  }
  return items;
}
