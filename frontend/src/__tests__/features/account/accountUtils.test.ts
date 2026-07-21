import {
  calculateIntradayReference,
  formatMoney,
  formatPercent,
} from '@/features/account/utils';

describe('account center data policy', () => {
  it('formats positive and negative PnL without replacing missing data with zero', () => {
    expect(formatMoney(125.5, true)).toContain('+¥125.50');
    expect(formatMoney(-20, true)).toContain('-¥20.00');
    expect(formatMoney(null)).toBe('--');
    expect(formatPercent(null)).toBe('--');
  });

  it('calculates intraday reference PnL only from covered live quotes', () => {
    const result = calculateIntradayReference(
      [
        {
          volume: 100,
          quote: {
            lastPrice: 11,
            preClose: 10,
            time: '2026-07-21T10:00:00',
          },
        },
        { volume: 200, quote: null },
      ],
      [],
      '2026-07-21'
    );

    expect(result.source).toBe('REALTIME_QUOTE');
    expect(result.value).toBe(100);
    expect(result.covered).toBe(1);
    expect(result.total).toBe(2);
  });

  it('falls back only to a snapshot from the same Shanghai trading date', () => {
    const oldSnapshot = calculateIntradayReference(
      [{ volume: 100, quote: null }],
      [
        {
          tradeDate: '2026-07-20',
          snapshotAt: '2026-07-20T15:01:00',
          dailyPnlCny: 888,
          dailyReturnPct: 1,
        },
      ],
      '2026-07-21'
    );
    const sameDay = calculateIntradayReference(
      [{ volume: 100, quote: null }],
      [
        {
          tradeDate: '2026-07-21',
          snapshotAt: '2026-07-21T15:01:00',
          dailyPnlCny: -66,
          dailyReturnPct: -0.2,
        },
      ],
      '2026-07-21'
    );

    expect(oldSnapshot.source).toBe('UNAVAILABLE');
    expect(oldSnapshot.value).toBeNull();
    expect(sameDay.source).toBe('SAME_DAY_SNAPSHOT');
    expect(sameDay.value).toBe(-66);
  });
});
