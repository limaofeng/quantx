import type {
  TTradeBatchMetrics,
  TTradeBatchSummary,
  TTradeExecutionMode,
  TTradeMetricBasis,
  TTradePositionBatch,
} from './TTradePositionsView';

export type LiveBatchQueryMetrics = {
  basis: string;
  origin?: string | null;
  quality: string;
  entryCapitalCny?: number | null;
  totalFeesCny?: number | null;
  realizedNetProfitCny?: number | null;
  markToMarketNetProfitCny?: number | null;
  netReturnPct?: number | null;
  holdingHours?: number | null;
  capitalUtilizationPct?: number | null;
};

export type LiveBatchQueryItem = {
  batchId: string;
  accountId: string;
  stockCode: string;
  strategyRunId: string;
  status: string;
  entryIntentId?: string | null;
  exitIntentId?: string | null;
  entryClientOrderId?: string | null;
  exitClientOrderId?: string | null;
  entryBrokerOrderId?: string | null;
  exitBrokerOrderId?: string | null;
  targetVolume: number;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  executionMode?: string | null;
  entryFilledAt?: string | null;
  terminalAt?: string | null;
  closedAt?: string | null;
  lastPrice?: number | null;
  priceAsOf?: string | null;
  priceQuality?: string | null;
  lastNetProfitPct: number;
  peakNetProfitPct: number;
  trailingFloorPct?: number | null;
  exitReason?: string | null;
  exceptionReason?: string | null;
  policyVersion: number;
  version: number;
  createdAt?: string | null;
  updatedAt?: string | null;
  metrics?: LiveBatchQueryMetrics | null;
};

export type LiveBatchQuerySummary = {
  totalCount: number;
  completedCount: number;
  completionRatePct: number;
  winningCount: number;
  winRatePct?: number | null;
  totalFeesCny?: number | null;
  netProfitCny?: number | null;
  averageHoldingHours?: number | null;
  averageCapitalUtilizationPct?: number | null;
  metricsCoveredCount: number;
  metricsTotalCount: number;
  metricsCoveragePct: number;
};

const EXECUTION_MODES = new Set<TTradeExecutionMode>([
  'LIVE',
  'PAPER',
  'BACKTEST',
]);

const COMPLETE_METRIC_BASES = new Set<TTradeMetricBasis>([
  'RULE_ESTIMATE',
  'LEGACY_BACKFILL',
]);

function executionMode(value: string | null | undefined) {
  const normalized = String(value || '').toUpperCase() as TTradeExecutionMode;
  return EXECUTION_MODES.has(normalized) ? normalized : null;
}

function metricBasis(value: LiveBatchQueryMetrics): TTradeMetricBasis {
  if (String(value.quality || '').toUpperCase() !== 'COMPLETE') {
    return 'INCOMPLETE';
  }
  if (String(value.origin || '').toUpperCase() === 'LEGACY_BACKFILL') {
    return 'LEGACY_BACKFILL';
  }
  const normalized = String(
    value.basis || ''
  ).toUpperCase() as TTradeMetricBasis;
  return COMPLETE_METRIC_BASES.has(normalized) ? normalized : 'INCOMPLETE';
}

export function adaptLiveBatchMetrics(
  value: LiveBatchQueryMetrics | null | undefined
): TTradeBatchMetrics {
  if (!value) {
    return {
      metricBasis: 'INCOMPLETE',
      entryCapitalCny: null,
      totalFeesCny: null,
      realizedNetProfitCny: null,
      markToMarketNetProfitCny: null,
      netReturnPct: null,
      holdingHours: null,
      capitalUtilizationPct: null,
    };
  }
  return {
    metricBasis: metricBasis(value),
    entryCapitalCny: value.entryCapitalCny ?? null,
    totalFeesCny: value.totalFeesCny ?? null,
    realizedNetProfitCny: value.realizedNetProfitCny ?? null,
    markToMarketNetProfitCny: value.markToMarketNetProfitCny ?? null,
    netReturnPct: value.netReturnPct ?? null,
    holdingHours: value.holdingHours ?? null,
    capitalUtilizationPct: value.capitalUtilizationPct ?? null,
  };
}

export function adaptLiveBatch(value: LiveBatchQueryItem): TTradePositionBatch {
  return {
    batchId: value.batchId,
    stockCode: value.stockCode,
    strategyRunId: value.strategyRunId,
    status: value.status,
    executionMode: executionMode(value.executionMode),
    entryIntentId: value.entryIntentId ?? null,
    exitIntentId: value.exitIntentId ?? null,
    entryClientOrderId: value.entryClientOrderId ?? null,
    exitClientOrderId: value.exitClientOrderId ?? null,
    entryBrokerOrderId: value.entryBrokerOrderId ?? null,
    exitBrokerOrderId: value.exitBrokerOrderId ?? null,
    targetVolume: value.targetVolume,
    entryFilledVolume: value.entryFilledVolume,
    entryAvgPrice: value.entryAvgPrice,
    exitFilledVolume: value.exitFilledVolume,
    exitAvgPrice: value.exitAvgPrice,
    activeVolume: value.activeVolume,
    lastPrice: value.lastPrice ?? null,
    priceAsOf: value.priceAsOf ?? null,
    priceQuality: value.priceQuality ?? null,
    netProfit: null,
    lastNetProfitPct: value.lastNetProfitPct,
    peakNetProfitPct: value.peakNetProfitPct,
    trailingFloorPct: value.trailingFloorPct ?? null,
    exitReason: value.exitReason ?? null,
    exceptionReason: value.exceptionReason ?? null,
    createdAt: value.createdAt ?? null,
    updatedAt: value.updatedAt ?? null,
    entryFilledAt: value.entryFilledAt ?? null,
    terminalAt: value.terminalAt ?? null,
    closedAt: value.closedAt ?? null,
    metrics: adaptLiveBatchMetrics(value.metrics),
    version: value.version,
  };
}

export function adaptLiveBatchSummary(
  value: LiveBatchQuerySummary
): TTradeBatchSummary {
  return {
    total: value.totalCount,
    completed: value.completedCount,
    completionRate: value.completionRatePct,
    winning: value.winningCount,
    winRate: value.winRatePct ?? null,
    feesCny: value.totalFeesCny ?? null,
    netProfitCny: value.netProfitCny ?? null,
    averageHoldingHours: value.averageHoldingHours ?? null,
    capitalUtilizationPct: value.averageCapitalUtilizationPct ?? null,
    coverage: value.metricsCoveredCount,
  };
}
