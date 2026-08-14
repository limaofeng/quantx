import type { Position } from '../types';

export type TakeProfitStrategyId =
  | 'ATR_TRAILING'
  | 'IMMEDIATE'
  | 'MA_BREAK'
  | 'MOMENTUM_EXHAUSTION'
  | 'PARTIAL_TRAILING'
  | 'SURGE_PULLBACK'
  | 'TIME_EXIT'
  | 'TRAILING_DRAWDOWN'
  | 'VWAP_BREAK';

export type TakeProfitTriggerMode = 'EITHER' | 'PRICE' | 'PROFIT';

export type TakeProfitSellMode =
  'ALL_AVAILABLE' | 'FIXED_VOLUME' | 'PERCENT_AVAILABLE';

export interface TakeProfitStrategyTemplate {
  description: string;
  id: TakeProfitStrategyId;
  label: string;
  status: 'preview' | 'supported';
  summary: string;
}

export interface TakeProfitPreviewInput {
  holding?: Position | null;
  sellMode: TakeProfitSellMode | string;
  sellRatioPct?: number | null;
  sellVolume?: number | null;
  targetPrice?: number | null;
  targetProfitPct?: number | null;
  triggerMode: TakeProfitTriggerMode;
}

export interface TakeProfitPlanPreview {
  currentPrice: number | null;
  currentProfitPct: number | null;
  estimatedOrderValue: number;
  estimatedSellVolume: number;
  targetPrice: number | null;
  targetProfitPct: number | null;
  triggerDistancePct: number | null;
  triggerSummary: string;
}

const LOT_SIZE = 100;

export const takeProfitStrategyTemplates: TakeProfitStrategyTemplate[] = [
  {
    description: '达到目标收益率或目标价后，立即提交卖出委托。',
    id: 'IMMEDIATE',
    label: '到价即止盈',
    status: 'supported',
    summary: '适合明确目标价、希望快速兑现的持仓。',
  },
  {
    description: '达到目标后观察实时量价，强势跟涨，转弱时卖出固定部分。',
    id: 'PARTIAL_TRAILING',
    label: '分段止盈 + 追踪剩余',
    status: 'supported',
    summary: '平衡型评分：回撤、短线斜率、量速和五档盘口共同确认。',
  },
  {
    description: '进入止盈区后记录 tick/1m 最高点，回撤后卖出。',
    id: 'TRAILING_DRAWDOWN',
    label: '追踪回撤止盈',
    status: 'preview',
    summary: '需要后端维护最高点和回撤状态机。',
  },
  {
    description: '放量拉升后从高点快速回落，触发部分或全部止盈。',
    id: 'SURGE_PULLBACK',
    label: '冲高回落止盈',
    status: 'preview',
    summary: '需要 tick/1m 成交量与回撤监控。',
  },
  {
    description: '止盈线随近期最高价和 ATR 波动动态调整。',
    id: 'ATR_TRAILING',
    label: 'ATR 波动止盈',
    status: 'preview',
    summary: '需要 ATR 指标和波动状态。',
  },
  {
    description: '达到止盈区后，跌破当日 VWAP 或无法站回则止盈。',
    id: 'VWAP_BREAK',
    label: 'VWAP 失守止盈',
    status: 'preview',
    summary: '需要日内 VWAP 与站回确认。',
  },
  {
    description: '达到止盈区后，跌破指定均线或 EMA 后卖出。',
    id: 'MA_BREAK',
    label: '均线跌破止盈',
    status: 'preview',
    summary: '需要均线/EMA 指标流。',
  },
  {
    description: 'RSI、MACD 或价格斜率转弱后执行止盈。',
    id: 'MOMENTUM_EXHAUSTION',
    label: '动能衰竭止盈',
    status: 'preview',
    summary: '需要动能指标和反转确认。',
  },
  {
    description: '达到止盈区后，指定时间或 K 线内未创新高则卖出。',
    id: 'TIME_EXIT',
    label: '时间止盈',
    status: 'preview',
    summary: '需要激活时间和新高状态追踪。',
  },
];

export function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function calculateTargetPrice(
  avgPrice: unknown,
  targetProfitPct: unknown
) {
  const cost = toFiniteNumber(avgPrice);
  const profitPct = toFiniteNumber(targetProfitPct);
  if (cost === null || cost <= 0 || profitPct === null) return null;
  return cost * (1 + profitPct / 100);
}

export function calculateProfitPctFromTargetPrice(
  avgPrice: unknown,
  targetPrice: unknown
) {
  const cost = toFiniteNumber(avgPrice);
  const price = toFiniteNumber(targetPrice);
  if (cost === null || cost <= 0 || price === null || price <= 0) return null;
  return (price / cost - 1) * 100;
}

export function getSellableVolume(holding?: Position | null) {
  if (!holding) return 0;
  return Math.max(0, Math.trunc(toFiniteNumber(holding.canUseVolume) ?? 0));
}

export function normalizePreviewSellVolume(
  availableVolume: unknown,
  sellMode: TakeProfitSellMode | string,
  sellRatioPct?: unknown,
  sellVolume?: unknown
) {
  const available = Math.max(
    0,
    Math.trunc(toFiniteNumber(availableVolume) ?? 0)
  );
  if (available <= 0) return 0;

  if (sellMode === 'ALL_AVAILABLE') return available;

  let requested = 0;
  if (sellMode === 'PERCENT_AVAILABLE') {
    const ratio = Math.max(0, Math.min(toFiniteNumber(sellRatioPct) ?? 0, 100));
    requested = Math.floor((available * ratio) / 100);
  } else {
    requested = Math.trunc(toFiniteNumber(sellVolume) ?? 0);
  }

  requested = Math.min(Math.max(0, requested), available);
  if (requested >= available) return available;
  if (requested < LOT_SIZE) return 0;
  return Math.floor(requested / LOT_SIZE) * LOT_SIZE;
}

export function getEstimatedSellValue(
  holding?: Position | null,
  sellVolume?: number
) {
  if (!holding || !sellVolume || sellVolume <= 0) return 0;

  const volume = toFiniteNumber(holding.volume);
  const marketValue = toFiniteNumber(holding.marketValue);
  if (volume !== null && volume > 0 && marketValue !== null) {
    return (marketValue * sellVolume) / volume;
  }

  const price =
    toFiniteNumber(holding.lastPrice) ?? toFiniteNumber(holding.avgPrice) ?? 0;
  return sellVolume * price;
}

export function getCurrentProfitPct(holding?: Position | null) {
  const fromHolding = toFiniteNumber(holding?.profitRate);
  if (fromHolding !== null) return fromHolding;
  return calculateProfitPctFromTargetPrice(
    holding?.avgPrice,
    holding?.lastPrice
  );
}

export function getTriggerDistancePct(input: TakeProfitPreviewInput) {
  const currentPrice = toFiniteNumber(input.holding?.lastPrice);
  const currentProfitPct = getCurrentProfitPct(input.holding);
  const distances: number[] = [];

  if (
    input.triggerMode !== 'PRICE' &&
    input.targetProfitPct !== null &&
    input.targetProfitPct !== undefined &&
    currentProfitPct !== null
  ) {
    distances.push(Number(input.targetProfitPct) - currentProfitPct);
  }

  if (
    input.triggerMode !== 'PROFIT' &&
    input.targetPrice !== null &&
    input.targetPrice !== undefined &&
    currentPrice !== null &&
    currentPrice > 0
  ) {
    distances.push(
      ((Number(input.targetPrice) - currentPrice) / currentPrice) * 100
    );
  }

  if (distances.length === 0) return null;
  return distances.reduce((best, item) => Math.min(best, item));
}

export function buildTriggerSummary(input: TakeProfitPreviewInput) {
  const parts: string[] = [];
  if (input.triggerMode !== 'PRICE' && input.targetProfitPct !== null) {
    parts.push(`收益率达到 ${Number(input.targetProfitPct).toFixed(2)}%`);
  }
  if (input.triggerMode !== 'PROFIT' && input.targetPrice !== null) {
    parts.push(`目标价达到 ${Number(input.targetPrice).toFixed(2)}`);
  }
  if (parts.length === 0) return '尚未设置止盈触发条件';
  return input.triggerMode === 'EITHER' ? parts.join(' 或 ') : parts[0];
}

export function buildTakeProfitPlanPreview(
  input: TakeProfitPreviewInput
): TakeProfitPlanPreview {
  const targetPrice =
    input.triggerMode === 'PROFIT'
      ? calculateTargetPrice(input.holding?.avgPrice, input.targetProfitPct)
      : toFiniteNumber(input.targetPrice);
  const targetProfitPct =
    input.triggerMode === 'PRICE'
      ? calculateProfitPctFromTargetPrice(
          input.holding?.avgPrice,
          input.targetPrice
        )
      : toFiniteNumber(input.targetProfitPct);
  const estimatedSellVolume = normalizePreviewSellVolume(
    input.holding?.canUseVolume,
    input.sellMode,
    input.sellRatioPct,
    input.sellVolume
  );

  return {
    currentPrice: toFiniteNumber(input.holding?.lastPrice),
    currentProfitPct: getCurrentProfitPct(input.holding),
    estimatedOrderValue: getEstimatedSellValue(
      input.holding,
      estimatedSellVolume
    ),
    estimatedSellVolume,
    targetPrice,
    targetProfitPct,
    triggerDistancePct: getTriggerDistancePct({
      ...input,
      targetPrice,
      targetProfitPct,
    }),
    triggerSummary: buildTriggerSummary({
      ...input,
      targetPrice,
      targetProfitPct,
    }),
  };
}
