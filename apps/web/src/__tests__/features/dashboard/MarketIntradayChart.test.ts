import { describe, expect, it } from 'vitest';

import { selectShanghaiMarketBarsForTradingDate } from '@/features/dashboard/marketIntradayData';

describe('MarketIntradayChart target trading date selection', () => {
  it('keeps only the requested Shanghai trading date', () => {
    const result = selectShanghaiMarketBarsForTradingDate(
      [
        { time: '2026-08-12 15:00:00', close: 3920 },
        { time: '2026-08-13 09:30:00', close: 3930 },
        { time: 'invalid', close: 3940 },
      ],
      '2026-08-13'
    );

    expect(result).toEqual([{ time: '2026-08-13 09:30:00', close: 3930 }]);
  });

  it('shows the previous completed session when it is the target date', () => {
    const result = selectShanghaiMarketBarsForTradingDate(
      [{ time: '2026-08-12 15:00:00', close: 3920 }],
      '2026-08-12'
    );

    expect(result).toEqual([{ time: '2026-08-12 15:00:00', close: 3920 }]);
  });

  it('does not show the previous session after the target switches to today', () => {
    const result = selectShanghaiMarketBarsForTradingDate(
      [{ time: '2026-08-12 15:00:00', close: 3920 }],
      '2026-08-13'
    );

    expect(result).toEqual([]);
  });

  it('waits for the authoritative calendar before selecting bars', () => {
    expect(
      selectShanghaiMarketBarsForTradingDate(
        [{ time: '2026-08-12 15:00:00', close: 3920 }],
        null
      )
    ).toEqual([]);
  });
});
