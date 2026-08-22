import {
  accountExecutionModeLabel,
  accountHealthLabel,
  accountSafetySummary,
} from '@/features/trading-safety/presentation';

describe('account execution safety presentation', () => {
  it('uses user-facing capability labels instead of assistant readiness terms', () => {
    expect(accountHealthLabel('HEALTHY')).toBe('正常');
    expect(accountExecutionModeLabel('OBSERVE_ONLY')).toBe('仅观察');
    expect(accountExecutionModeLabel('REDUCE_ONLY')).toBe('仅减仓');
    expect(accountExecutionModeLabel('TRADING')).toBe('可交易');
    expect(accountHealthLabel('KILLED')).toBe('紧急停止');
  });

  it('keeps account facts and the first recovery reason concise', () => {
    expect(
      accountSafetySummary({
        reconcileStatus: 'READY',
        blockedReasons: ['尚未建立账户实盘窗口'],
      })
    ).toBe('账户已对账 · 尚未建立账户实盘窗口');
  });
});
