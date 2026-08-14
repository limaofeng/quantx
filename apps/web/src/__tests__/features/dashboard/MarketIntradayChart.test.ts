import { describe, expect, it } from 'vitest';

import { selectCurrentShanghaiMarketBars } from '@/features/dashboard/marketIntradayData';

describe('MarketIntradayChart current-day selection', () => {
  it('keeps only the current Shanghai trading date', () => {
    const result = selectCurrentShanghaiMarketBars(
      [
        { time: '2026-08-12 15:00:00', close: 3920 },
        { time: '2026-08-13 09:30:00', close: 3930 },
        { time: 'invalid', close: 3940 },
      ],
      new Date('2026-08-13T06:00:00.000Z')
    );

    expect(result).toEqual([
      { time: '2026-08-13 09:30:00', close: 3930 },
    ]);
  });

  it('returns no bars when QMT only has a previous trading day', () => {
    const result = selectCurrentShanghaiMarketBars(
      [{ time: '2026-08-12 15:00:00', close: 3920 }],
      new Date('2026-08-13T06:00:00.000Z')
    );

    expect(result).toEqual([]);
  });
});
