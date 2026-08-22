export type ConnectionTone = 'ready' | 'degraded' | 'offline';

export interface ConnectionHealthInput {
  status: string;
  mode: string;
  websocketStatus: string;
  xtdataStatus: string;
  xtdataReason?: string | null;
  xttradingStatus: string;
  xttradingReason?: string | null;
  reconciliationStatus: string;
}

export interface ConnectionHealth {
  tone: ConnectionTone;
  label: string;
  title: string;
  description: string;
}

const SAFE_REASON_LABELS: Record<string, string> = {
  TRADING_DISABLED_BY_MODE: '当前运行模式未启用交易连接',
  XTDATA_UNAVAILABLE: 'MiniQMT 行情连接暂不可用',
  XTTRADING_UNAVAILABLE: 'MiniQMT 交易连接暂不可用',
};

export function safeReasonLabel(value?: string | null) {
  const normalized = value?.trim().toUpperCase();
  if (!normalized) return '';
  return SAFE_REASON_LABELS[normalized] ?? '本地连接暂不可用';
}

export function connectionHealth(
  current?: ConnectionHealthInput | null
): ConnectionHealth {
  if (!current) {
    return {
      tone: 'offline',
      label: '未登记',
      title: '尚未建立 QMT 本机连接',
      description: '创建一次性登记码，在运行 MiniQMT 的电脑上完成登记。',
    };
  }

  const status = current.status.toUpperCase();
  if (status === 'OFFLINE' || current.websocketStatus !== 'CONNECTED') {
    return {
      tone: 'offline',
      label: '离线',
      title: 'QMT Agent 当前离线',
      description: 'QuantX 没有收到本机 Agent 的有效心跳，请检查本机进程。',
    };
  }
  if (current.xtdataStatus !== 'CONNECTED') {
    return {
      tone: 'degraded',
      label: '行情异常',
      title: 'MiniQMT 行情连接未就绪',
      description:
        safeReasonLabel(current.xtdataReason) || 'XTData 当前不可用。',
    };
  }
  const tradingExpected = current.mode.toLowerCase() !== 'data-only';
  if (
    tradingExpected &&
    current.xttradingStatus.toUpperCase() !== 'CONNECTED'
  ) {
    return {
      tone: 'degraded',
      label: '交易异常',
      title: 'MiniQMT 交易连接未就绪',
      description:
        safeReasonLabel(current.xttradingReason) ||
        'XTTrading 当前不可用。',
    };
  }
  if (current.reconciliationStatus.toUpperCase() !== 'READY') {
    return {
      tone: 'degraded',
      label: '对账中',
      title: '账户状态尚未收敛',
      description: '完整账户快照完成前，QuantX 不会向该 Agent 下发新交易命令。',
    };
  }
  if (status !== 'READY') {
    return {
      tone: 'degraded',
      label: '恢复中',
      title: 'QMT Agent 正在恢复',
      description: '连接已建立，正在等待行情与账户状态完成收敛。',
    };
  }
  return {
    tone: 'ready',
    label: '运行正常',
    title: 'QMT 本机连接运行正常',
    description: '行情、交易与账户对账链路均已就绪。',
  };
}

export function formatDuration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

export function formatBytes(bytes?: number | null) {
  if (bytes == null || !Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${Math.max(0, Math.round(bytes))} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}
