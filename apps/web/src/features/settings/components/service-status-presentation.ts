import type {
  MonitorRange,
  MonitorStatus,
  MonitorTargetSummary,
} from '@/features/system/monitor-api';

export const ranges: Array<{ value: MonitorRange; label: string }> = [
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: '90d', label: '90 天' },
  { value: '1y', label: '1 年' },
];

export const statusLabel: Record<MonitorStatus, string> = {
  healthy: '正常',
  degraded: '降级',
  unavailable: '不可用',
  unknown: '未知',
  disabled: '未启用',
};

export const statusPriority: Record<MonitorStatus, number> = {
  healthy: 1,
  disabled: 2,
  unknown: 3,
  degraded: 4,
  unavailable: 5,
};

export const historyTone: Record<MonitorStatus, string> = {
  healthy: 'bg-emerald-400',
  degraded: 'bg-amber-400',
  unavailable: 'bg-rose-400',
  unknown: 'bg-amber-300/60',
  disabled: 'bg-slate-600/70',
};

export function metric(value: number | null, suffix = '') {
  return value === null ? 'N/A' : `${value.toFixed(2)}${suffix}`;
}

export function formatTime(value: string | null) {
  if (!value) return '尚无记录';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function probeExplanation(target: MonitorTargetSummary) {
  return target.id === 'account-safety-observer'
    ? '该状态只表示 Monitor 能持续采集脱敏准入快照；QMT、行情与交易门禁的实际结论请在“交易安全”中查看。'
    : target.id === 'market-gateway'
      ? '状态检查 QMT 行情连接、快照与数据新鲜度，不包含交易能力或 Engine 消费状态；延迟是网关健康接口的 HTTP RTT，不是行情传输延迟。'
      : target.probeKind === 'derived'
        ? '该组件来自语义快照，不生成虚假的独立延迟。'
        : target.probeKind === 'composite'
          ? '状态综合 Windows 健康端点与服务端会话/对账语义；延迟为 Monitor 到 Windows Agent 的健康探测 RTT。'
          : '延迟来自 Monitor 到目标服务的主动健康探测。';
}

export function serviceHistoryPath(
  targetId: string,
  range: MonitorRange,
  page = 1,
  pageSize = 20
) {
  const query = new URLSearchParams({
    range,
    page: String(page),
    pageSize: String(pageSize),
  });
  return `/settings/status/${encodeURIComponent(targetId)}/history?${query}`;
}
