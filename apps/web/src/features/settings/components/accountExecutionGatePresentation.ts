interface AccountExecutionGatePresentation {
  label: string;
  passedDescription: string;
}

export interface AccountExecutionGateFreshness {
  countdownLabel: string;
  progressPercent: number;
  remainingSeconds: number;
  tone: 'fresh' | 'warning' | 'expired';
}

const SNAPSHOT_FRESHNESS_TTL_SECONDS = 90;
const SNAPSHOT_WARNING_SECONDS = 30;
const BACKUP_FRESHNESS_TTL_SECONDS = 24 * 60 * 60;
const BACKUP_WARNING_SECONDS = 2 * 60 * 60;

export const accountExecutionGatePresentation = {
  SERVER_REAL_TRADING_ENABLED: {
    label: '服务端实盘开关',
    passedDescription: '服务端已开启真实下单总开关。',
  },
  ACCOUNT_ALLOWLISTED: {
    label: '账户实盘白名单',
    passedDescription: '当前资金账户已获准进入实盘。',
  },
  ENGINE_READY: {
    label: '交易引擎就绪',
    passedDescription: '交易引擎心跳正常且状态新鲜。',
  },
  LIVE_AGENT_READY: {
    label: 'QMT 实盘代理就绪',
    passedDescription: 'QMT 实盘代理在线并可用。',
  },
  AGENT_MODE_LIVE: {
    label: '代理运行模式',
    passedDescription: 'QMT 代理已明确运行在实盘模式。',
  },
  PROTOCOL_1_1: {
    label: '通信协议版本',
    passedDescription: 'QMT 代理使用兼容的 1.1 协议。',
  },
  EXECUTION_CONTROL_CONFIGURED: {
    label: '账户执行控制',
    passedDescription: '账户已有独立的执行控制配置。',
  },
  SNAPSHOT_RECONCILED: {
    label: '账户快照对账',
    passedDescription: '资金、持仓、委托和成交已完成对账。',
  },
  SNAPSHOT_FRESH: {
    label: '账户快照时效',
    passedDescription: '账户完整快照仍在 90 秒有效期内。',
  },
  SNAPSHOT_ACTIVITY_CLASSIFIED: {
    label: '交易活动分类',
    passedDescription: '最新快照中的手工与外部交易已完成分类。',
  },
  RECENT_BACKUP: {
    label: '最近备份',
    passedDescription: '最近 24 小时内已有成功备份。',
  },
  NO_CRITICAL_ALERTS: {
    label: '严重运行告警',
    passedDescription: '当前没有未解决的严重运行告警。',
  },
  NO_DEAD_LETTERS: {
    label: '报告死信',
    passedDescription: '当前没有待处理的 Agent 报告死信。',
  },
  CONTROLLED_WINDOW_ACTIVE: {
    label: '账户实盘窗口',
    passedDescription: '已基于最新快照建立账户实盘窗口。',
  },
  NO_EXTERNAL_BROKER_ACTIVITY: {
    label: '外部交易活动',
    passedDescription: '实盘窗口建立后没有新的外部交易或活动委托。',
  },
  KILL_SWITCH_CLEAR: {
    label: '紧急停止状态',
    passedDescription: '账户紧急停止当前未触发。',
  },
  ACCOUNT_RISK_INCREASE_AUTHORIZED: {
    label: '新增风险授权',
    passedDescription: '账户已授权策略新增风险。',
  },
} as const satisfies Record<string, AccountExecutionGatePresentation>;

export function getAccountExecutionGatePresentation(
  code: string
): AccountExecutionGatePresentation {
  return (
    accountExecutionGatePresentation[
      code as keyof typeof accountExecutionGatePresentation
    ] ?? {
      label: '未识别的安全检查',
      passedDescription: '系统返回了尚未收录的门禁项。',
    }
  );
}

function parseUtcTimestamp(value?: string | null) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    ? value
    : `${value}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function formatCountdown(seconds: number) {
  const totalSeconds = Math.max(0, Math.ceil(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;
  const clock = [minutes, remainingSeconds]
    .map(value => String(value).padStart(2, '0'))
    .join(':');
  return hours > 0 ? `${String(hours).padStart(2, '0')}:${clock}` : clock;
}

function buildFreshness(
  remainingSeconds: number,
  ttlSeconds: number,
  warningSeconds: number
): AccountExecutionGateFreshness {
  const clampedRemaining = Math.max(0, remainingSeconds);
  return {
    countdownLabel:
      clampedRemaining > 0
        ? `距过期 ${formatCountdown(clampedRemaining)}`
        : '已过期',
    progressPercent: Math.min(
      100,
      Math.max(0, (clampedRemaining / ttlSeconds) * 100)
    ),
    remainingSeconds: Math.ceil(clampedRemaining),
    tone:
      clampedRemaining <= 0
        ? 'expired'
        : clampedRemaining <= warningSeconds
          ? 'warning'
          : 'fresh',
  };
}

export function getSnapshotFreshness(
  reconciliationAgeSeconds: number | null | undefined,
  checkedAt: string | null | undefined,
  now: number
) {
  if (
    reconciliationAgeSeconds == null ||
    !Number.isFinite(reconciliationAgeSeconds) ||
    reconciliationAgeSeconds < 0
  ) {
    return null;
  }
  const checkedAtTimestamp = parseUtcTimestamp(checkedAt);
  const elapsedSinceCheck =
    checkedAtTimestamp == null
      ? 0
      : Math.max(0, (now - checkedAtTimestamp) / 1000);
  return buildFreshness(
    SNAPSHOT_FRESHNESS_TTL_SECONDS -
      reconciliationAgeSeconds -
      elapsedSinceCheck,
    SNAPSHOT_FRESHNESS_TTL_SECONDS,
    SNAPSHOT_WARNING_SECONDS
  );
}

export function getBackupFreshness(
  lastBackupAt: string | null | undefined,
  now: number
) {
  const lastBackupTimestamp = parseUtcTimestamp(lastBackupAt);
  if (lastBackupTimestamp == null) return null;
  return buildFreshness(
    BACKUP_FRESHNESS_TTL_SECONDS - (now - lastBackupTimestamp) / 1000,
    BACKUP_FRESHNESS_TTL_SECONDS,
    BACKUP_WARNING_SECONDS
  );
}
