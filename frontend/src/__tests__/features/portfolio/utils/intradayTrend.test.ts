import type { UTCTimestamp } from 'lightweight-charts';
import { describe, expect, it } from 'vitest';

import {
  COMPRESSED_TRADING_RANGE,
  getCompressedTradingMinute,
  normalizeTicksToIntradayTrend,
  resolveIntradayAnchorDate,
} from '@/features/portfolio/utils/intradayTrend';

describe('intradayTrend', () => {
  const tradingDays = ['2026-05-08', '2026-05-11', '2026-05-12'];

  it('anchors to today after the A-share open', () => {
    const anchor = resolveIntradayAnchorDate(
      tradingDays,
      new Date('2026-05-12T02:30:00.000Z')
    );

    expect(anchor).toEqual({ date: '2026-05-12', isToday: true });
    expect(getCompressedTradingMinute(10 * 60 + 30)).toBe(60);
  });

  it('does not advance during the lunch break', () => {
    expect(getCompressedTradingMinute(11 * 60 + 30)).toBe(120);
    expect(getCompressedTradingMinute(12 * 60)).toBeNull();
    expect(getCompressedTradingMinute(13 * 60)).toBe(120);
  });

  it('compresses afternoon trading into the second half of the session', () => {
    expect(getCompressedTradingMinute(14 * 60)).toBe(180);
    expect(getCompressedTradingMinute(15 * 60)).toBe(240);
  });

  it('anchors to the previous trading day on non-trading days', () => {
    const anchor = resolveIntradayAnchorDate(
      tradingDays,
      new Date('2026-05-10T03:00:00.000Z')
    );

    expect(anchor).toEqual({ date: '2026-05-08', isToday: false });
  });

  it('anchors to the previous trading day before the open', () => {
    const anchor = resolveIntradayAnchorDate(
      tradingDays,
      new Date('2026-05-12T00:30:00.000Z')
    );

    expect(anchor).toEqual({ date: '2026-05-11', isToday: false });
  });

  it('sorts ticks and filters non-session points', () => {
    const points = normalizeTicksToIntradayTrend(
      [
        { time: '2026-05-12T13:00:00', lastPrice: 11 },
        { time: '2026-05-12T09:20:00', lastPrice: 10 },
        { time: '2026-05-12T12:00:00', lastPrice: 12 },
        { time: '2026-05-12T10:30:00', lastPrice: 10.5 },
        { time: '2026-05-11T10:30:00', lastPrice: 9.5 },
      ],
      '2026-05-12'
    );

    expect(points).toEqual([
      {
        time: ((COMPRESSED_TRADING_RANGE.from as number) +
          60 * 60) as UTCTimestamp,
        value: 10.5,
      },
      {
        time: ((COMPRESSED_TRADING_RANGE.from as number) +
          120 * 60) as UTCTimestamp,
        value: 11,
      },
    ]);
  });
});
