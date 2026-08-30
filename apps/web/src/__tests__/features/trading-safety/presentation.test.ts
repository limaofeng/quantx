import {
  accountExecutionModeLabel,
  accountHealthLabel,
  accountSafetyReason,
  accountSafetySummary,
} from '@/features/trading-safety/presentation';
import { AccountExecutionHealthStatus } from '@/generated/gql/graphql';

describe('account execution safety presentation', () => {
  it('uses user-facing capability labels instead of assistant readiness terms', () => {
    expect(accountHealthLabel(AccountExecutionHealthStatus.Healthy)).toBe(
      '正常'
    );
    expect(accountExecutionModeLabel('OBSERVE_ONLY')).toBe('仅观察');
    expect(accountExecutionModeLabel('REDUCE_ONLY')).toBe('仅减仓');
    expect(accountExecutionModeLabel('TRADING')).toBe('可交易');
    expect(accountHealthLabel(AccountExecutionHealthStatus.Killed)).toBe(
      '紧急停止'
    );
  });

  it('keeps account facts and the first recovery reason concise', () => {
    expect(
      accountSafetySummary({
        reconcileStatus: 'READY',
        blockedReasons: ['尚未建立账户实盘窗口'],
      })
    ).toBe('账户已对账 · 尚未建立账户实盘窗口');
  });

  it('replaces internal reason codes in user-facing safety messages', () => {
    expect(
      accountSafetyReason(
        '本机 QMT Agent 当前不可用于实盘（XTTRADING_UNAVAILABLE）'
      )
    ).toBe('本机 QMT Agent 当前不可用于实盘（MiniQMT 交易连接未就绪）');
    expect(accountSafetyReason('实盘阻断（BROKER_PRIVATE_FAILURE）')).toBe(
      '实盘阻断（状态异常）'
    );
  });
});
