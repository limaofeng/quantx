import { LOW_SIGNAL_GRID_REASONS } from './constants';

export function statusLabel(status: string) {
  const labels: Record<string, string> = {
    DISABLED: '已禁用',
    PLANNED: '计划中',
    MONITORING: '监控中',
    WAIT_REARM: '等待重穿',
    PENDING: '待成交',
    PARTIAL_FILLED: '部分成交',
    FILLED: '已成交',
    REJECTED: '已拒单',
    CANCELLED: '已撤销',
  };
  return labels[status] || status;
}

export function statusClass(status: string) {
  if (status === 'FILLED')
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400';
  if (status === 'PENDING' || status === 'PARTIAL_FILLED')
    return 'border-amber-500/30 bg-amber-500/10 text-amber-400';
  if (status === 'MONITORING')
    return 'border-blue-500/30 bg-blue-500/10 text-blue-400';
  if (status === 'WAIT_REARM')
    return 'border-violet-500/30 bg-violet-500/10 text-violet-300';
  if (status === 'REJECTED' || status === 'CANCELLED')
    return 'border-rose-500/30 bg-rose-500/10 text-rose-400';
  if (status === 'DISABLED')
    return 'border-slate-500/30 bg-slate-500/10 text-slate-400';
  return 'border-slate-400/20 bg-slate-400/5 text-slate-300';
}

export function inventoryStatusLabel(status: string) {
  const labels: Record<string, string> = {
    OPEN: '可用',
    RESERVED: '已预留',
    CLOSED: '已用完',
    CANCELLED: '已取消',
  };
  return labels[status] || status;
}

export function inventorySourceLabel(source: string) {
  const labels: Record<string, string> = {
    BUY_FILL: '买入成交',
    INITIAL_SWING: '初始活跃仓',
  };
  return labels[source] || source;
}

export function bucketLabel(bucket: string) {
  const labels: Record<string, string> = {
    swing: '活跃仓',
    core: '核心仓',
    locked_core: '封存仓',
  };
  return labels[bucket] || bucket;
}

export function reasonLabel(reason?: string | null) {
  const labels: Record<string, string> = {
    initialized: '已初始化',
    waiting_swing_inventory: '等待活跃库存',
    grid_book_updated: '计划已更新',
    grid_touch_monitoring: '触网监控中',
    grid_pullback_buy: '回撤确认买入',
    grid_sell: '触发卖出档',
    waiting_price_recross: '等待价格重穿买线',
    order_submitted: '委托已提交',
    order_rejected: '委托被拒绝',
    order_cancelled: '委托已撤销',
    order_expired: '委托已过期',
    order_partial_filled: '部分成交',
    order_filled: '委托已成交',
    trade_filled: '成交确认',
    trade_partial_filled: '部分成交确认',
    sell_waterline_rearmed: '卖出档已复位',
    released_by_sell_waterline: '卖出释放买入档',
    exit_completed_wait_rearm: '卖出完成，等待重穿',
    price_recross_rearmed: '价格已重穿，等待买入',
  };
  return reason ? labels[reason] || reason : '';
}

export function displayReasonLabel(reason?: string | null) {
  if (!reason || LOW_SIGNAL_GRID_REASONS.has(reason)) return '';
  return reasonLabel(reason);
}

export function formatMoney(value?: number | null) {
  return `¥${Number(value || 0).toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
  })}`;
}

export function formatNumber(value?: number | null) {
  return Number(value || 0).toLocaleString('zh-CN', {
    maximumFractionDigits: 4,
  });
}

export function formatSignedPercent(value?: number | null) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return '--';
  return `${percent > 0 ? '+' : ''}${percent.toFixed(2)}%`;
}
