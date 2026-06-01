import type { Time } from 'lightweight-charts';

import type {
  ExecutionTraceView,
  StrategyDecision,
  TradeIntentView,
} from '../../domain/types';

import type {
  StrategyChartMarkerDetail,
  StrategyChartMarker,
  StrategyChartPeriod,
  StrategyChartRange,
} from './types';

export const BACKTEST_COMMON_PERIODS = [
  { value: 'DAY_1', label: '日K' },
  { value: 'WEEK_1', label: '周K' },
  { value: 'MONTH_1', label: '月K' },
] as const;

export const BACKTEST_MINUTE_PERIODS = [
  { value: 'MIN_1', label: '1分' },
  { value: 'MIN_5', label: '5分' },
  { value: 'MIN_15', label: '15分' },
  { value: 'MIN_30', label: '30分' },
  { value: 'MIN_60', label: '60分' },
] as const;

export const BACKTEST_PERIODS = [
  ...BACKTEST_COMMON_PERIODS,
  ...BACKTEST_MINUTE_PERIODS,
] as const;

export function normalizeBacktestBoundary(
  value?: string | null,
  edge: 'start' | 'end' = 'start'
) {
  if (!value) return null;
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  if (!match) return value;
  return edge === 'start'
    ? `${match[1]}T00:00:00.000`
    : `${match[1]}T23:59:59.999`;
}

export function normalizeBacktestRange(
  range?: StrategyChartRange | null
): Required<StrategyChartRange> {
  return {
    startTime: normalizeBacktestBoundary(range?.startTime, 'start'),
    endTime: normalizeBacktestBoundary(range?.endTime, 'end'),
  };
}

export function exactBacktestRange(
  range?: StrategyChartRange | null
): Required<StrategyChartRange> {
  return {
    startTime: range?.startTime?.trim() || null,
    endTime: range?.endTime?.trim() || null,
  };
}

export function formatDateLabel(value?: string | null) {
  if (!value) return null;
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (match) {
    return `${match[1]}/${Number(match[2])}/${Number(match[3])}`;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('zh-CN');
}

export function formatRangeLabel(range?: StrategyChartRange | null) {
  const start = formatDateLabel(range?.startTime);
  const end = formatDateLabel(range?.endTime);
  if (!start || !end) return null;
  return `${start} - ${end}`;
}

export function toChartTime(
  value: string,
  period: StrategyChartPeriod,
  isTickPeriod: boolean
): Time | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  if (
    !isTickPeriod &&
    (period === 'DAY_1' || period === 'WEEK_1' || period === 'MONTH_1')
  ) {
    return value.split('T')[0] as Time;
  }
  if (!isTickPeriod) {
    const match = period.match(/^MIN_(\d+)$/);
    const intervalMinutes = match ? Number(match[1]) : 1;
    const intervalMs = Math.max(1, intervalMinutes) * 60 * 1000;
    return (Math.floor(date.getTime() / intervalMs) *
      (intervalMs / 1000)) as Time;
  }
  return Math.floor(date.getTime() / 1000) as Time;
}

export function formatHoverTime(value: unknown) {
  if (typeof value === 'number') {
    return new Date(value * 1000).toLocaleString('zh-CN', {
      hour12: false,
    });
  }
  if (
    value &&
    typeof value === 'object' &&
    'year' in value &&
    'month' in value &&
    'day' in value
  ) {
    const businessDay = value as { year: number; month: number; day: number };
    return `${businessDay.year}-${String(businessDay.month).padStart(2, '0')}-${String(businessDay.day).padStart(2, '0')}`;
  }
  return String(value);
}

export function chartTimeKey(value: unknown) {
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') return value;
  if (
    value &&
    typeof value === 'object' &&
    'year' in value &&
    'month' in value &&
    'day' in value
  ) {
    const businessDay = value as { year: number; month: number; day: number };
    return `${businessDay.year}-${String(businessDay.month).padStart(2, '0')}-${String(businessDay.day).padStart(2, '0')}`;
  }
  return String(value);
}

function readNumber(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (
    typeof value === 'string' &&
    value.trim() &&
    !Number.isNaN(Number(value))
  ) {
    return Number(value);
  }
  return undefined;
}

function normalizeSide(side?: string | null) {
  const value = (side || '').toUpperCase();
  if (value.includes('BUY') || value.includes('买')) return 'BUY';
  if (value.includes('SELL') || value.includes('卖')) return 'SELL';
  return 'UNKNOWN';
}

function sideLabel(side: string) {
  if (side === 'SELL') return '卖出';
  if (side === 'BUY') return '买入';
  return '交易';
}

function sideMarkerLabel(side: string): 'B' | 'S' | '?' {
  if (side === 'SELL') return 'S';
  if (side === 'BUY') return 'B';
  return '?';
}

function isFilledTrace(trace?: ExecutionTraceView) {
  const executedVolume = readNumber(trace?.executedVolume);
  if (executedVolume && executedVolume > 0) {
    return true;
  }

  const combined = [trace?.fillStatus, trace?.orderStatus, trace?.riskDecision]
    .filter(Boolean)
    .join(' ')
    .toUpperCase();

  return /(FILLED|DEAL|成交|SUCCESS|PART_SUCC|ALL_TRADED|TRADED)/.test(
    combined
  );
}

function isRejectedTrace(trace?: ExecutionTraceView) {
  const combined = [
    trace?.riskDecision,
    trace?.orderStatus,
    trace?.fillStatus,
    trace?.reason,
  ]
    .filter(Boolean)
    .join(' ')
    .toUpperCase();

  return /(REJECT|DENY|REFUSE|FAILED|ERROR|拒|否决|失败)/.test(combined);
}

function hasExecutionTrace(trace?: ExecutionTraceView) {
  if (!trace) return false;
  return Boolean(
    trace?.orderId ||
      trace?.orderStatus ||
      trace?.fillStatus ||
      trace?.riskDecision ||
      (trace.executedPrice !== null && trace.executedPrice !== undefined) ||
      (trace.executedVolume !== null && trace.executedVolume !== undefined) ||
      trace?.executedTime
  );
}

function markerPosition(side: string, price?: number) {
  if (price) return 'atPriceMiddle';
  return side === 'SELL' ? 'aboveBar' : 'belowBar';
}

function executionEventTime(
  trace: ExecutionTraceView,
  fallbackTime?: string | null
) {
  return (
    trace.executedTime ||
    trace.updatedAt ||
    trace.createdAt ||
    fallbackTime ||
    null
  );
}

function executionEventPrice(
  trace: ExecutionTraceView,
  intent?: TradeIntentView
) {
  return readNumber(trace.executedPrice) ?? readNumber(intent?.priceIntent);
}

function resultMarkerColor(trace: ExecutionTraceView, side: string) {
  if (isRejectedTrace(trace)) return '#f59e0b';
  return side === 'SELL' ? '#3b82f6' : '#ef4444';
}

function signalMarkerColor(side: string) {
  if (side === 'SELL') return '#93c5fd';
  if (side === 'BUY') return '#fca5a5';
  return '#94a3b8';
}

function sameIntentId(left?: string | null, right?: string | null) {
  return !!left && !!right && left === right;
}

function traceKey(trace: ExecutionTraceView) {
  return trace.intentId || trace.traceId || trace.id;
}

function signalKey(decision: StrategyDecision, intent: TradeIntentView) {
  return `${decision.id}-${intent.id}-signal`;
}

function resultKey(trace: ExecutionTraceView) {
  return `${traceKey(trace)}-result`;
}

function findTraceByIntent(
  traceByIntent: Map<string, ExecutionTraceView>,
  intent: TradeIntentView
) {
  return (
    traceByIntent.get(intent.id) ||
    (intent.traceId
      ? [...traceByIntent.values()].find(trace =>
          sameIntentId(trace.traceId, intent.traceId)
        )
      : undefined)
  );
}

function intentMarkerPrice(intent: TradeIntentView) {
  return readNumber(intent.priceIntent);
}

function traceSide(trace: ExecutionTraceView, intent?: TradeIntentView) {
  return normalizeSide(trace.side || intent?.side);
}

function intentSide(intent: TradeIntentView, trace?: ExecutionTraceView) {
  return normalizeSide(intent.side || trace?.side);
}

function markerTimeSortValue(value: Time) {
  return typeof value === 'number' ? value : String(value);
}

function compareMarkerTime(left: Time, right: Time) {
  const leftValue = markerTimeSortValue(left);
  const rightValue = markerTimeSortValue(right);
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    return leftValue - rightValue;
  }
  return String(leftValue).localeCompare(String(rightValue));
}

function compareOptionalEventTime(left?: string | null, right?: string | null) {
  const leftMs = left ? new Date(left).getTime() : 0;
  const rightMs = right ? new Date(right).getTime() : 0;
  return (
    (Number.isFinite(leftMs) ? leftMs : 0) -
    (Number.isFinite(rightMs) ? rightMs : 0)
  );
}

function shouldRenderResultTrace(trace: ExecutionTraceView) {
  return (
    isFilledTrace(trace) ||
    isRejectedTrace(trace) ||
    Boolean(trace.orderStatus || trace.fillStatus || trace.orderId)
  );
}

function formatTradeNumber(value?: number | null, digits = 2) {
  if (value === undefined || value === null || !Number.isFinite(value)) {
    return undefined;
  }
  return value.toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  });
}

function compactText(value?: string | number | null) {
  if (value === undefined || value === null || value === '') return undefined;
  return String(value);
}

function formatEventTime(value?: string | null) {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

function buildDetailRows(
  rows: Array<StrategyChartMarkerDetail | false | null | undefined>
): StrategyChartMarkerDetail[] {
  return rows.filter((row): row is StrategyChartMarkerDetail => Boolean(row));
}

function signalEventTitle(side: string) {
  return `${sideLabel(side)}信号`;
}

function resultEventType(trace: ExecutionTraceView) {
  if (isRejectedTrace(trace)) return 'rejected';
  if (isFilledTrace(trace)) return 'filled';
  return 'order';
}

function resultEventTitle(trace: ExecutionTraceView, side: string) {
  if (isRejectedTrace(trace)) return `${sideLabel(side)}受限`;
  if (isFilledTrace(trace)) return `${sideLabel(side)}成交`;
  return `${sideLabel(side)}下单`;
}

function signalDetailRows(
  decision: StrategyDecision,
  intent: TradeIntentView,
  side: string,
  price?: number
) {
  const quantity = readNumber(intent.quantityIntent);
  const targetBucket = compactText(intent.targetBucket);
  const status = compactText(intent.status);
  const reason = compactText(intent.reason);
  return buildDetailRows([
    { label: '类型', value: '策略信号', tone: 'muted' },
    formatEventTime(decision.decidedAt)
      ? { label: '时间', value: formatEventTime(decision.decidedAt) || '' }
      : null,
    price !== undefined && {
      label: '意图价',
      value: formatTradeNumber(price, 4) || String(price),
      tone: side === 'SELL' ? 'sell' : 'buy',
    },
    quantity !== undefined && {
      label: '数量',
      value: formatTradeNumber(quantity, 0) || String(quantity),
    },
    targetBucket
      ? {
          label: '仓位',
          value: targetBucket,
        }
      : null,
    status
      ? {
          label: '状态',
          value: status,
          tone: 'success',
        }
      : null,
    reason
      ? {
          label: '原因',
          value: reason,
        }
      : null,
  ]);
}

function resultDetailRows(
  trace: ExecutionTraceView,
  intent: TradeIntentView | undefined,
  side: string,
  eventTime?: string | null,
  price?: number
) {
  const volume = readNumber(trace.executedVolume);
  const orderState = compactText(trace.orderStatus || trace.fillStatus);
  const riskDecision = compactText(trace.riskDecision);
  const reason = compactText(trace.reason || intent?.reason);
  return buildDetailRows([
    {
      label: '类型',
      value: resultEventTitle(trace, side),
      tone: resultEventType(trace) === 'rejected' ? 'warning' : 'success',
    },
    eventTime
      ? {
          label: '时间',
          value: formatEventTime(eventTime) || eventTime,
        }
      : null,
    price !== undefined && {
      label: '价格',
      value: formatTradeNumber(price, 4) || String(price),
      tone: side === 'SELL' ? 'sell' : 'buy',
    },
    volume !== undefined && {
      label: '成交量',
      value: formatTradeNumber(volume, 0) || String(volume),
    },
    orderState
      ? {
          label: '订单',
          value: orderState,
          tone: 'success',
        }
      : null,
    riskDecision
      ? {
          label: '风控',
          value: riskDecision,
        }
      : null,
    reason
      ? {
          label: '原因',
          value: reason,
        }
      : null,
  ]);
}

function markerGroupKey(marker: StrategyChartMarker) {
  return [
    chartTimeKey(marker.time),
    marker.tradeSide,
    marker.eventType,
    marker.position,
  ].join('|');
}

function groupedMarkerPrice(markers: StrategyChartMarker[]) {
  const prices = markers
    .map(marker => marker.priceValue)
    .filter((value): value is number => typeof value === 'number');
  if (prices.length === 0) return undefined;
  if (markers[0]?.tradeSide === 'SELL') return Math.max(...prices);
  if (markers[0]?.tradeSide === 'BUY') return Math.min(...prices);
  return prices.reduce((sum, value) => sum + value, 0) / prices.length;
}

function compactMarkerStacks(markers: StrategyChartMarker[]) {
  const markerGroups = new Map<string, StrategyChartMarker[]>();
  markers.forEach(marker => {
    const key = markerGroupKey(marker);
    const group = markerGroups.get(key) || [];
    group.push(marker);
    markerGroups.set(key, group);
  });

  return [...markerGroups.values()]
    .map(group => {
      if (group.length === 1) return group[0];
      const sortedGroup = [...group].sort((left, right) =>
        compareOptionalEventTime(left.eventTime, right.eventTime)
      );
      const representative = sortedGroup[0];
      const price = groupedMarkerPrice(sortedGroup);
      return {
        ...representative,
        id: `${representative.id || markerGroupKey(representative)}-group`,
        price,
        priceValue: price ?? null,
        text: representative.label,
        eventTitle: `${representative.eventTitle} x${sortedGroup.length}`,
        detailRows: [
          {
            label: '合计',
            value: `${sortedGroup.length} 条`,
            tone: 'muted',
          },
        ],
        groupCount: sortedGroup.length,
        childMarkers: sortedGroup,
      } as StrategyChartMarker;
    })
    .sort((a, b) => compareMarkerTime(a.time, b.time));
}

function addSignalMarker(
  markers: StrategyChartMarker[],
  decision: StrategyDecision,
  intent: TradeIntentView,
  trace: ExecutionTraceView | undefined,
  period: StrategyChartPeriod,
  isTickPeriod: boolean
) {
  const time = toChartTime(decision.decidedAt, period, isTickPeriod);
  if (!time) return;
  const side = intentSide(intent, trace);
  const price = intentMarkerPrice(intent);
  markers.push({
    id: signalKey(decision, intent),
    time,
    position: markerPosition(side, price),
    price,
    color: signalMarkerColor(side),
    shape: 'square',
    text: sideMarkerLabel(side),
    size: 0.58,
    label: sideMarkerLabel(side),
    tradeSide: side,
    eventType: 'signal',
    eventTitle: signalEventTitle(side),
    eventTime: decision.decidedAt,
    priceValue: price ?? null,
    quantityValue: readNumber(intent.quantityIntent) ?? null,
    detailRows: signalDetailRows(decision, intent, side, price),
  } as StrategyChartMarker);
}

function addResultMarker(
  markers: StrategyChartMarker[],
  trace: ExecutionTraceView,
  decisionTime: string | null | undefined,
  intent: TradeIntentView | undefined,
  period: StrategyChartPeriod,
  isTickPeriod: boolean
) {
  if (!shouldRenderResultTrace(trace)) return;
  const eventTime = executionEventTime(trace, decisionTime);
  if (!eventTime) return;
  const time = toChartTime(eventTime, period, isTickPeriod);
  if (!time) return;

  const side = traceSide(trace, intent);
  const price = executionEventPrice(trace, intent);
  markers.push({
    id: resultKey(trace),
    time,
    position: markerPosition(side, price),
    price,
    color: resultMarkerColor(trace, side),
    shape: 'square',
    text: sideMarkerLabel(side),
    size: 0.68,
    label: sideMarkerLabel(side),
    tradeSide: side,
    eventType: resultEventType(trace),
    eventTitle: resultEventTitle(trace, side),
    eventTime,
    priceValue: price ?? null,
    quantityValue: readNumber(trace.executedVolume) ?? null,
    detailRows: resultDetailRows(trace, intent, side, eventTime, price),
  } as StrategyChartMarker);
}

export function buildTradeMarkers({
  period,
  isTickPeriod,
  decisions = [],
  executions = [],
}: {
  period: StrategyChartPeriod;
  isTickPeriod: boolean;
  decisions?: StrategyDecision[];
  executions?: ExecutionTraceView[];
}): StrategyChartMarker[] {
  const traceByIntent = new Map(
    executions
      .filter(trace => trace.intentId)
      .map(trace => [trace.intentId, trace])
  );
  const decisionByIntent = new Map<
    string,
    { decision: StrategyDecision; intent: TradeIntentView }
  >();
  const signalIntentIds = new Set<string>();
  const markers: StrategyChartMarker[] = [];

  decisions.forEach(decision => {
    decision.tradeIntents.forEach(intent => {
      if (signalIntentIds.has(intent.id)) return;
      signalIntentIds.add(intent.id);
      if (!decisionByIntent.has(intent.id)) {
        decisionByIntent.set(intent.id, { decision, intent });
      }
      const trace = findTraceByIntent(traceByIntent, intent);
      if (!hasExecutionTrace(trace)) {
        addSignalMarker(markers, decision, intent, trace, period, isTickPeriod);
      }
    });
  });

  executions.forEach(trace => {
    const matched = decisionByIntent.get(trace.intentId);
    addResultMarker(
      markers,
      trace,
      matched?.decision.decidedAt,
      matched?.intent,
      period,
      isTickPeriod
    );
  });

  return compactMarkerStacks(
    markers.sort((a, b) => compareMarkerTime(a.time, b.time))
  );
}
