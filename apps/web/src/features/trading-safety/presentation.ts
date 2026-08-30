import { AccountExecutionHealthStatus } from '@/generated/gql/graphql';

const ACCOUNT_SAFETY_REASON_LABELS: Record<string, string> = {
  CONTROL_CONNECTION_OFFLINE: 'QMT Agent 控制链路已断开',
  EMERGENCY_STOP: 'QMT Agent 处于紧急停止状态',
  QMT_ACCOUNT_MISMATCH: 'MiniQMT 账户与授权账户不一致',
  QMT_AGENT_NOT_RECONCILED: 'QMT Agent 尚未完成账户对账',
  QMT_AGENT_OFFLINE: 'QMT Agent 当前离线',
  QMT_AGENT_STALE: 'QMT Agent 心跳已过期',
  QMT_ENROLLMENT_REQUIRED: 'QMT Agent 尚未完成本机登记',
  QMT_LAUNCH_BLOCKED: 'QMT Agent 启动已被阻断',
  QMT_RUNTIME_UNAVAILABLE: '本机 MiniQMT 运行环境不可用',
  XTDATA_UNAVAILABLE: 'MiniQMT 行情连接未就绪',
  XTTRADING_UNAVAILABLE: 'MiniQMT 交易连接未就绪',
};

const INTERNAL_REASON_CODE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g;

export function accountSafetyReason(reason?: string | null) {
  return String(reason || '').replace(
    INTERNAL_REASON_CODE,
    code => ACCOUNT_SAFETY_REASON_LABELS[code] ?? '状态异常'
  );
}

export function accountHealthLabel(status: AccountExecutionHealthStatus) {
  switch (status) {
    case AccountExecutionHealthStatus.Healthy:
      return '正常';
    case AccountExecutionHealthStatus.Killed:
      return '紧急停止';
    case AccountExecutionHealthStatus.Blocked:
      return '阻断';
  }
}

export function accountExecutionModeLabel(mode?: string | null) {
  switch (String(mode || '').toUpperCase()) {
    case 'TRADING':
      return '可交易';
    case 'REDUCE_ONLY':
      return '仅减仓';
    case 'KILLED':
      return '紧急停止';
    default:
      return '仅观察';
  }
}

export function accountSafetySummary(input: {
  blockedReasons?: readonly string[] | null;
  reconcileStatus?: string | null;
}) {
  const facts =
    String(input.reconcileStatus || '').toUpperCase() === 'READY'
      ? '账户已对账'
      : '账户待对账';
  const reason = accountSafetyReason(input.blockedReasons?.[0]);
  return [facts, reason].filter(Boolean).join(' · ');
}
