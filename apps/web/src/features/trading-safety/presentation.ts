import { AccountExecutionHealthStatus } from '@/generated/gql/graphql';

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
  const reason = input.blockedReasons?.[0];
  return [facts, reason].filter(Boolean).join(' · ');
}
