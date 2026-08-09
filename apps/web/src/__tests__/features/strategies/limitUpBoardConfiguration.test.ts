import { describe, expect, it } from 'vitest';

import { toBacktestBoundaryIso } from '@/features/strategies/domain/backtestRange';
import { validateLimitUpBoardConfiguration } from '@/features/strategies/domain/limitUpBoardConfiguration';
import { StrategyRunMode } from '@/generated/gql/graphql';

describe('limit-up board creation safeguards', () => {
  it('serializes Shanghai trading dates without a UTC date shift', () => {
    const selected = new Date(2026, 6, 31, 0, 0, 0);

    expect(toBacktestBoundaryIso(selected, 'start')).toBe(
      '2026-07-31T00:00:00.000'
    );
    expect(toBacktestBoundaryIso(selected, 'end')).toBe(
      '2026-07-31T23:59:59.999'
    );
  });

  it('rejects malformed trading times before submitting', () => {
    const errors = validateLimitUpBoardConfiguration(
      {
        entry_start_time: '09:70',
        entry_end_time: '14:50',
        target_position_pct: 0.05,
      },
      StrategyRunMode.Paper
    );

    expect(errors).toContain('最早入场时间格式无效');
  });

  it('requires a bound account for live instances', () => {
    const errors = validateLimitUpBoardConfiguration(
      {
        entry_start_time: '09:30',
        entry_end_time: '14:50',
        target_position_pct: 0.05,
      },
      StrategyRunMode.Live
    );

    expect(errors).toContain('实盘模式必须绑定当前交易账户');
  });

  it('accepts a strict backtest configuration', () => {
    const errors = validateLimitUpBoardConfiguration(
      {
        entry_start_time: '09:30',
        entry_end_time: '14:50',
        target_position_pct: 0.05,
        initial_capital: 1_000_000,
        strict_market_data: true,
        strict_limit_data: true,
      },
      StrategyRunMode.Backtest
    );

    expect(errors).toEqual([]);
  });
});
