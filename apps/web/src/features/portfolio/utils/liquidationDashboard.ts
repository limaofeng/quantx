import type { ConditionalLiquidationOrdersQuery as ConditionalLiquidationOrdersQueryData } from '@/generated/gql/graphql';

import type { LiquidatedStock, Position } from '../types';

export type ConditionalLiquidationOrderLike = NonNullable<
  ConditionalLiquidationOrdersQueryData['conditionalLiquidationOrders']
>[number];

export type DashboardMetricTone =
  'danger' | 'info' | 'neutral' | 'success' | 'warning';

export interface LiquidationDashboardMetric {
  id:
    | 'activeConditions'
    | 'liquidatablePositions'
    | 'quoteStatus'
    | 'sellableMarketValue'
    | 'todayTriggers'
    | 'totalMarketValue';
  label: string;
  subValue?: string;
  tone: DashboardMetricTone;
  value: number | string;
}

export type ConditionalMonitorStatus =
  'cancelled' | 'error' | 'monitoring' | 'paused' | 'submitted' | 'triggered';

export interface ConditionalMonitorRow {
  conditionText: string;
  currentPrice: number | null;
  currentProfitPct: number | null;
  distancePct: number | null;
  holding: Position | null;
  id: string;
  instrumentName: string;
  lastCheckedAt: string | null;
  lastError: string | null;
  order: ConditionalLiquidationOrderLike;
  status: ConditionalMonitorStatus;
  statusLabel: string;
  stockCode: string;
  triggeredToday: boolean;
}

export type HoldingRiskSeverity = 'critical' | 'warning' | 'watch';

export interface HoldingRiskAlert {
  changePercent: number | null;
  instrumentName: string;
  marketValue: number;
  quoteTime: string | null;
  reason: string;
  severity: HoldingRiskSeverity;
  stockCode: string;
  tickDropPct: number | null;
  title: string;
  todayProfitLoss: number | null;
  todayProfitRate: number | null;
}

export interface LiquidationDashboardSummary {
  activeConditionalOrders: number;
  enabledConditionalOrders: number;
  errorOrders: number;
  liquidatablePositions: number;
  quoteStatus: 'empty' | 'live' | 'waiting';
  realtimeQuoteCount: number;
  sellableMarketValue: number;
  todayLiquidatedReports: number;
  totalMarketValue: number;
  totalPositions: number;
  triggeredToday: number;
}

const CANCELLED_STATUS = 'CANCELLED';
const FAILED_STATUS = 'FAILED';
const SUBMITTED_STATUS = 'SUBMITTED';

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function getStockCodePrefix(value: unknown) {
  return normalizeStockCode(value).split('.')[0] || '';
}

function stockCodeMatches(left: unknown, right: unknown) {
  const leftCode = normalizeStockCode(left);
  const rightCode = normalizeStockCode(right);
  if (!leftCode || !rightCode) return false;
  return (
    leftCode === rightCode ||
    getStockCodePrefix(leftCode) === getStockCodePrefix(rightCode)
  );
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getPositiveNumber(...values: unknown[]) {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
}

function getSellableVolume(holding: Position) {
  return Math.max(0, Math.trunc(toFiniteNumber(holding.canUseVolume) ?? 0));
}

function getEstimatedSellValue(holding: Position) {
  const sellableVolume = getSellableVolume(holding);
  if (sellableVolume <= 0) return 0;

  const volume = toFiniteNumber(holding.volume);
  const marketValue = toFiniteNumber(holding.marketValue);
  if (volume !== null && volume > 0 && marketValue !== null) {
    return (marketValue * sellableVolume) / volume;
  }

  const price = getPositiveNumber(holding.lastPrice, holding.avgPrice) ?? 0;
  return sellableVolume * price;
}

function getLocalDayKey(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const date =
    typeof value === 'number'
      ? new Date(value * 1000)
      : new Date(String(value));
  if (Number.isNaN(date.getTime())) return null;
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function isSameLocalDay(value: unknown, now: Date) {
  const valueKey = getLocalDayKey(value);
  return Boolean(valueKey && valueKey === getLocalDayKey(now));
}

function buildConditionText(order: ConditionalLiquidationOrderLike) {
  const parts: string[] = [];
  const targetProfitPct = toFiniteNumber(order.targetProfitPct);
  const targetPrice = toFiniteNumber(order.targetPrice);
  if (targetProfitPct !== null) {
    parts.push(`收益率 >= ${targetProfitPct.toFixed(2)}%`);
  }
  if (targetPrice !== null) {
    parts.push(`目标价 >= ${targetPrice.toFixed(2)}`);
  }
  return parts.length > 0 ? parts.join(' / ') : '未设置触发条件';
}

function getConditionalStatus(
  order: ConditionalLiquidationOrderLike,
  triggeredToday: boolean
): { label: string; status: ConditionalMonitorStatus } {
  const status = String(order.status || '').toUpperCase();
  if (status === CANCELLED_STATUS) {
    return { label: '已取消', status: 'cancelled' };
  }
  if (status === SUBMITTED_STATUS) {
    return { label: '已提交', status: 'submitted' };
  }
  if (status === FAILED_STATUS || order.lastError) {
    return { label: '异常', status: 'error' };
  }
  if (triggeredToday) {
    return { label: '今日触发', status: 'triggered' };
  }
  if (order.enabled) {
    return { label: '监控中', status: 'monitoring' };
  }
  return { label: '已停用', status: 'paused' };
}

function getTriggerDistancePct(
  order: ConditionalLiquidationOrderLike,
  holding: Position | null
) {
  if (!holding) return null;

  const distances: number[] = [];
  const targetProfitPct = toFiniteNumber(order.targetProfitPct);
  const currentProfitPct = toFiniteNumber(holding.profitRate);
  if (targetProfitPct !== null && currentProfitPct !== null) {
    distances.push(targetProfitPct - currentProfitPct);
  }

  const targetPrice = toFiniteNumber(order.targetPrice);
  const currentPrice = getPositiveNumber(holding.lastPrice);
  if (targetPrice !== null && currentPrice !== null) {
    distances.push(((targetPrice - currentPrice) / currentPrice) * 100);
  }

  if (distances.length === 0) return null;
  return distances.reduce((best, distance) => {
    if (best <= 0 || distance <= 0) return Math.min(best, distance);
    return Math.min(best, distance);
  });
}

export function buildConditionalMonitorRows({
  conditionalOrders,
  holdings,
  now = new Date(),
}: {
  conditionalOrders: ConditionalLiquidationOrderLike[];
  holdings: Position[];
  now?: Date;
}): ConditionalMonitorRow[] {
  const rows = conditionalOrders
    .filter(
      order => String(order.status || '').toUpperCase() !== CANCELLED_STATUS
    )
    .map(order => {
      const stockCode = normalizeStockCode(order.stockCode);
      const holding =
        holdings.find(item => stockCodeMatches(item.stockCode, stockCode)) ??
        null;
      const triggeredToday = isSameLocalDay(order.triggeredAt, now);
      const status = getConditionalStatus(order, triggeredToday);

      return {
        conditionText: buildConditionText(order),
        currentPrice: getPositiveNumber(holding?.lastPrice),
        currentProfitPct: toFiniteNumber(holding?.profitRate),
        distancePct: getTriggerDistancePct(order, holding),
        holding,
        id: order.id,
        instrumentName:
          order.instrumentName || holding?.instrumentName || stockCode,
        lastCheckedAt: order.lastCheckedAt || null,
        lastError: order.lastError || null,
        order,
        status: status.status,
        statusLabel: status.label,
        stockCode,
        triggeredToday,
      };
    });

  const statusRank: Record<ConditionalMonitorStatus, number> = {
    error: 0,
    triggered: 1,
    submitted: 2,
    monitoring: 3,
    paused: 4,
    cancelled: 5,
  };

  return rows.sort((left, right) => {
    const rankDelta = statusRank[left.status] - statusRank[right.status];
    if (rankDelta !== 0) return rankDelta;
    const leftDistance = left.distancePct ?? Number.POSITIVE_INFINITY;
    const rightDistance = right.distancePct ?? Number.POSITIVE_INFINITY;
    if (leftDistance !== rightDistance) return leftDistance - rightDistance;
    return left.stockCode.localeCompare(right.stockCode);
  });
}

export function buildHoldingRiskAlerts({
  holdings,
  tickDropPctByCode = {},
}: {
  holdings: Position[];
  tickDropPctByCode?: Record<string, number>;
}): HoldingRiskAlert[] {
  const alerts = holdings
    .map(holding => {
      const stockCode = normalizeStockCode(holding.stockCode);
      const tickDropPct =
        toFiniteNumber(tickDropPctByCode[stockCode]) ??
        toFiniteNumber(tickDropPctByCode[getStockCodePrefix(stockCode)]);
      const changePercent =
        toFiniteNumber(holding.changePercent) ??
        toFiniteNumber(holding.todayProfitRate);
      const todayProfitRate = toFiniteNumber(holding.todayProfitRate);
      const todayProfitLoss = toFiniteNumber(holding.todayProfitLoss);
      const marketValue = toFiniteNumber(holding.marketValue) ?? 0;

      let severity: HoldingRiskSeverity | null = null;
      let title = '';
      if (
        (tickDropPct !== null && tickDropPct <= -2.5) ||
        (changePercent !== null && changePercent <= -7) ||
        (todayProfitRate !== null && todayProfitRate <= -7)
      ) {
        severity = 'critical';
        title = '极速下跌';
      } else if (
        (tickDropPct !== null && tickDropPct <= -1.5) ||
        (changePercent !== null && changePercent <= -4) ||
        (todayProfitRate !== null && todayProfitRate <= -4)
      ) {
        severity = 'warning';
        title = '快速下跌';
      } else if (
        (changePercent !== null && changePercent <= -2) ||
        (todayProfitRate !== null && todayProfitRate <= -2) ||
        (todayProfitLoss !== null &&
          todayProfitLoss < 0 &&
          marketValue > 0 &&
          (Math.abs(todayProfitLoss) / marketValue) * 100 >= 2)
      ) {
        severity = 'watch';
        title = '走弱观察';
      }

      if (!severity) return null;

      const reasons: string[] = [];
      if (tickDropPct !== null && tickDropPct <= -1.5) {
        reasons.push(`滚动报价 ${tickDropPct.toFixed(2)}%`);
      }
      if (changePercent !== null && changePercent <= -2) {
        reasons.push(`实时涨跌 ${changePercent.toFixed(2)}%`);
      }
      if (todayProfitRate !== null && todayProfitRate <= -2) {
        reasons.push(`今日盈亏率 ${todayProfitRate.toFixed(2)}%`);
      }
      if (todayProfitLoss !== null && todayProfitLoss < 0) {
        reasons.push(`今日亏损 ${Math.abs(todayProfitLoss).toFixed(2)}`);
      }

      return {
        changePercent,
        instrumentName: holding.instrumentName || stockCode,
        marketValue,
        quoteTime: holding.quoteTime || null,
        reason: reasons.join(' · ') || '持仓价格走弱',
        severity,
        stockCode,
        tickDropPct,
        title,
        todayProfitLoss,
        todayProfitRate,
      };
    })
    .filter((alert): alert is HoldingRiskAlert => Boolean(alert));

  const severityRank: Record<HoldingRiskSeverity, number> = {
    critical: 0,
    warning: 1,
    watch: 2,
  };

  return alerts.sort((left, right) => {
    const rankDelta =
      severityRank[left.severity] - severityRank[right.severity];
    if (rankDelta !== 0) return rankDelta;
    const leftMove = Math.min(
      left.tickDropPct ?? 0,
      left.changePercent ?? 0,
      left.todayProfitRate ?? 0
    );
    const rightMove = Math.min(
      right.tickDropPct ?? 0,
      right.changePercent ?? 0,
      right.todayProfitRate ?? 0
    );
    if (leftMove !== rightMove) return leftMove - rightMove;
    return right.marketValue - left.marketValue;
  });
}

export function buildLiquidationDashboardSummary({
  conditionalRows,
  holdings,
  liquidatedStocks,
  portfolioMarketValue,
}: {
  conditionalRows: ConditionalMonitorRow[];
  holdings: Position[];
  liquidatedStocks: LiquidatedStock[];
  portfolioMarketValue?: number | null;
}): LiquidationDashboardSummary {
  const totalMarketValue =
    portfolioMarketValue ??
    holdings.reduce(
      (sum, holding) => sum + (toFiniteNumber(holding.marketValue) ?? 0),
      0
    );
  const realtimeQuoteCount = holdings.filter(
    holding =>
      holding.quoteTime ||
      toFiniteNumber(holding.changePercent) !== null ||
      getPositiveNumber(holding.lastPrice) !== null
  ).length;

  return {
    activeConditionalOrders: conditionalRows.filter(row =>
      ['monitoring', 'triggered', 'error'].includes(row.status)
    ).length,
    enabledConditionalOrders: conditionalRows.filter(
      row =>
        row.order.enabled &&
        row.status !== 'submitted' &&
        row.status !== 'cancelled'
    ).length,
    errorOrders: conditionalRows.filter(row => row.status === 'error').length,
    liquidatablePositions: holdings.filter(getSellableVolume).length,
    quoteStatus:
      holdings.length === 0
        ? 'empty'
        : realtimeQuoteCount > 0
          ? 'live'
          : 'waiting',
    realtimeQuoteCount,
    sellableMarketValue: holdings.reduce(
      (sum, holding) => sum + getEstimatedSellValue(holding),
      0
    ),
    todayLiquidatedReports: liquidatedStocks.length,
    totalMarketValue,
    totalPositions: holdings.length,
    triggeredToday: conditionalRows.filter(row => row.triggeredToday).length,
  };
}

export function buildLiquidationDashboardMetrics(
  summary: LiquidationDashboardSummary
): LiquidationDashboardMetric[] {
  const quoteLabel =
    summary.quoteStatus === 'live'
      ? '实时更新'
      : summary.quoteStatus === 'waiting'
        ? '等待行情'
        : '无持仓';

  return [
    {
      id: 'totalMarketValue',
      label: '持仓市值',
      subValue: `${summary.totalPositions} 只持仓`,
      tone: 'neutral',
      value: summary.totalMarketValue,
    },
    {
      id: 'sellableMarketValue',
      label: '可清仓市值',
      subValue: `${summary.liquidatablePositions} 只可清仓`,
      tone: 'info',
      value: summary.sellableMarketValue,
    },
    {
      id: 'liquidatablePositions',
      label: '可清仓标的',
      subValue: `总持仓 ${summary.totalPositions}`,
      tone: summary.liquidatablePositions > 0 ? 'warning' : 'neutral',
      value: summary.liquidatablePositions,
    },
    {
      id: 'activeConditions',
      label: '启用条件单',
      subValue: `活跃 ${summary.activeConditionalOrders}`,
      tone: summary.enabledConditionalOrders > 0 ? 'danger' : 'neutral',
      value: summary.enabledConditionalOrders,
    },
    {
      id: 'todayTriggers',
      label: '今日触发 / 异常',
      subValue: `真实回报 ${summary.todayLiquidatedReports} 笔`,
      tone: summary.errorOrders > 0 ? 'danger' : 'success',
      value: `${summary.triggeredToday} / ${summary.errorOrders}`,
    },
    {
      id: 'quoteStatus',
      label: '实时行情',
      subValue: `${summary.realtimeQuoteCount} 只收到报价`,
      tone: summary.quoteStatus === 'live' ? 'success' : 'warning',
      value: quoteLabel,
    },
  ];
}
