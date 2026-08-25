import { financialToneClass } from '@/shared/utils/financialColors';

import type { SignalHistoryFilter } from './types';

export const statusPresentation: Record<
  string,
  { label: string; className: string }
> = {
  DRAINING: {
    label: '退出中',
    className: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
  },
  IGNORED: {
    label: '已忽略',
    className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
  },
  INELIGIBLE: {
    label: '暂不可用',
    className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
  },
  MONITORED: {
    label: '监控中',
    className: 'border-red-400/25 bg-red-400/10 text-red-200',
  },
  PENDING_START: {
    label: '待启动',
    className: 'border-blue-400/25 bg-blue-400/10 text-blue-200',
  },
  STOPPED: {
    label: '未启动',
    className: 'border-white/10 bg-white/[0.04] text-slate-500',
  },
};

const signalReasonLabels: Record<string, string> = {
  APPROVAL_TTL_EXPIRED: '超过确认有效期，系统自动忽略',
  PRICE_DEVIATION_EXCEEDED: '确认时价格已偏离信号价',
  USER_REJECTED: '人工忽略本次信号',
  GLOBAL_CONFIG_CHANGED: '策略参数变更，原信号自动撤销',
  HOLDING_NOT_ELIGIBLE: '持仓不再满足做 T 条件',
  GLOBAL_MONITOR_STOPPED: '全局监控停止，信号自动撤销',
  T_TRADE_MOMENTUM_ACCELERATION_ENTRY: '快速拉升与成交加速确认后买入',
  T_TRADE_PULLBACK_REBOUND_ENTRY: '回撤企稳并反弹确认后买入',
};

export const batchStatusLabels: Record<string, string> = {
  ENTRY_QUEUED: '买入排队',
  ENTRY_SUBMITTED: '买入已报',
  ENTRY_PARTIAL: '买入部分成交',
  OPEN: '持仓保护中',
  EXIT_TRIGGERED: '待卖出',
  EXIT_SUBMITTED: '卖出已报',
  EXIT_PARTIAL: '卖出部分成交',
  CLOSED: '已完成',
  ENTRY_EXPIRED: '买入已过期',
  ENTRY_REJECTED: '买入失败',
  EXIT_REJECTED: '卖出异常',
  RECONCILE_REQUIRED: '需要对账',
  KILL_SWITCHED: '人工处置',
};

export function signalStatusPresentation(status: string, reason: string) {
  const normalizedStatus = status.toUpperCase();
  const normalizedReason = reason.toUpperCase();
  if (normalizedStatus === 'EXPIRED') {
    return {
      label:
        normalizedReason === 'PRICE_DEVIATION_EXCEEDED'
          ? '价格偏离'
          : '确认超时',
      className: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    };
  }
  if (normalizedStatus === 'REJECTED') {
    return {
      label: normalizedReason === 'USER_REJECTED' ? '已忽略' : '已撤销',
      className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
    };
  }
  if (normalizedStatus === 'CANCELLED') {
    return {
      label: '已撤单',
      className: 'border-slate-400/20 bg-slate-400/10 text-slate-400',
    };
  }
  if (normalizedStatus === 'FILLED') {
    return {
      label: '已成交',
      className: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
    };
  }
  if (normalizedStatus === 'PARTIAL_FILLED') {
    return {
      label: '部分成交',
      className: 'border-cyan-400/25 bg-cyan-400/10 text-cyan-200',
    };
  }
  if (normalizedStatus === 'AWAITING_APPROVAL') {
    return {
      label: '待确认',
      className: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    };
  }
  return {
    label: '已确认',
    className: 'border-blue-400/25 bg-blue-400/10 text-blue-200',
  };
}

export function signalHistoryCategory(
  status: string
): Exclude<SignalHistoryFilter, 'ALL'> {
  const normalized = status.toUpperCase();
  if (normalized === 'EXPIRED') return 'EXPIRED';
  if (['REJECTED', 'CANCELLED'].includes(normalized)) return 'IGNORED';
  return 'CONFIRMED';
}

export function signalReasonLabel(reason: string, status: string) {
  const normalizedReason = reason.toUpperCase();
  if (signalReasonLabels[normalizedReason]) {
    return signalReasonLabels[normalizedReason];
  }
  if (status.toUpperCase() === 'FILLED') return '确认后已完成买入成交';
  if (status.toUpperCase() === 'PARTIAL_FILLED') return '确认后部分成交';
  return reason || '信号已确认并进入执行流程';
}

export function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function integerValue(value: string, fallback: number) {
  return Math.trunc(numberValue(value, fallback));
}

export function formatNumber(value: number, digits = 2) {
  return Number(value || 0).toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatTime(value?: string | null) {
  if (!value) return '尚未同步';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

export function formatQuoteTime(value?: string | null) {
  if (!value) return '行情接收中';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : `更新于 ${date.toLocaleTimeString('zh-CN', { hour12: false })}`;
}

export function formatSignedPercent(value?: number | null) {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${value > 0 ? '+' : ''}${formatNumber(value)}%`;
}

export function quoteTone(value?: number | null) {
  if (value == null || !Number.isFinite(value) || value === 0) {
    return 'text-slate-300';
  }
  return financialToneClass(value, 'holding');
}

export function hasInstrumentName(
  stockCode: string,
  candidate?: string | null
) {
  const value = String(candidate || '').trim();
  const normalizedCode = stockCode.trim().toUpperCase();
  const codeWithoutExchange = normalizedCode.split('.', 1)[0];
  return Boolean(
    value &&
    ![normalizedCode, codeWithoutExchange].includes(value.toUpperCase())
  );
}

export function resolveInstrumentName(
  stockCode: string,
  positionName?: string | null,
  monitorName?: string | null
) {
  if (hasInstrumentName(stockCode, positionName)) {
    return String(positionName).trim();
  }
  if (hasInstrumentName(stockCode, monitorName)) {
    return String(monitorName).trim();
  }
  return stockCode;
}

type ReplayCrypto = {
  randomUUID?: () => string;
  getRandomValues<T extends ArrayBufferView | null>(array: T): T;
};

export function replayIdempotencyKey(
  cryptoApi: ReplayCrypto = globalThis.crypto
) {
  if (typeof cryptoApi.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0'));
  return [
    hex.slice(0, 4).join(''),
    hex.slice(4, 6).join(''),
    hex.slice(6, 8).join(''),
    hex.slice(8, 10).join(''),
    hex.slice(10).join(''),
  ].join('-');
}

function shanghaiClock(value: Date) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find(item => item.type === type)?.value || '';
  const hour = Number(part('hour') === '24' ? '0' : part('hour'));
  return {
    date: `${part('year')}-${part('month')}-${part('day')}`,
    minutes: hour * 60 + Number(part('minute')),
  };
}

export function replayDatePreset(
  tradingDayCount: 1 | 5 | 20,
  tradingCalendar: string[] = [],
  now: Date = new Date()
) {
  const current = shanghaiClock(now);
  const completedTradingDays = Array.from(new Set(tradingCalendar))
    .sort()
    .filter(
      day =>
        day < current.date || (day === current.date && current.minutes >= 900)
    );
  if (completedTradingDays.length > 0) {
    const selected = completedTradingDays.slice(-tradingDayCount);
    return { start: selected[0], end: selected[selected.length - 1] };
  }

  const formatUtcDate = (value: Date) => {
    const year = value.getUTCFullYear();
    const month = String(value.getUTCMonth() + 1).padStart(2, '0');
    const day = String(value.getUTCDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };
  const end = new Date(`${current.date}T00:00:00Z`);
  if (current.minutes < 900) end.setUTCDate(end.getUTCDate() - 1);
  while ([0, 6].includes(end.getUTCDay())) {
    end.setUTCDate(end.getUTCDate() - 1);
  }
  const start = new Date(end);
  let counted = 1;
  while (counted < tradingDayCount) {
    start.setUTCDate(start.getUTCDate() - 1);
    const day = start.getUTCDay();
    if (day !== 0 && day !== 6) counted += 1;
  }
  return { start: formatUtcDate(start), end: formatUtcDate(end) };
}

export function replayStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    CANCELLED: '已取消',
    COMPLETED: '已完成',
    ERROR: '失败',
    PENDING: '等待中',
    RUNNING: '回放中',
    STARTING: '启动中',
    STOPPED: '已停止',
  };
  return labels[String(status || '').toUpperCase()] || status || '未知';
}

export function replayPhaseLabel(phase?: string | null) {
  const labels: Record<string, string> = {
    VALIDATING_PORTFOLIO: '校验初始账户',
    CHECKING_DATA: '检查本地行情',
    DOWNLOADING_DATA: '下载缺失行情',
    VERIFYING_DATA: '复核行情完整性',
    REPLAYING: '执行历史回放',
    FINALIZING: '生成回测结果',
    COMPLETED: '回测已完成',
    FAILED: '回测失败',
    CANCELLED: '回测已取消',
  };
  return labels[String(phase || '').toUpperCase()] || phase || '准备中';
}
