import { describe, expect, it } from 'vitest';

import { getTickDateRange } from '@/components/trading-chart/utils/time-utils';
import { __intradayKLineTestUtils } from '@/hooks/useIntradayKLines';

const {
  buildCallAuctionTickBars,
  buildTickMinuteBar,
  getFallbackTradingDateRange,
  getIntradayQueryDateRange,
  getMinuteMs,
  isKLineBaseReadyForTick,
  mergeKLines,
  resolveTickMinuteMetric,
} = __intradayKLineTestUtils;

describe('useIntradayKLines helpers', () => {
  it('builds call auction bars directly from tick data before 1m klines exist', () => {
    const bars = buildCallAuctionTickBars([
      {
        stockCode: '688552.SH',
        time: '2026-06-03T09:15:03',
        lastPrice: 29.46,
        preClose: 29.46,
        volume: 100,
        amount: 2946,
      },
      {
        stockCode: '688552.SH',
        time: '2026-06-03T09:15:06',
        lastPrice: 29.5,
        preClose: 29.46,
        volume: 104,
        amount: 3064,
      },
      {
        stockCode: '688552.SH',
        time: '2026-06-03T09:26:00',
        lastPrice: 29.52,
        volume: 120,
        amount: 3542,
      },
    ]);

    expect(bars).toHaveLength(2);
    expect(getMinuteMs(bars[0].time)).toBe(getMinuteMs('2026-06-03T09:15:00'));
    expect(bars[0]).toMatchObject({
      close: 29.46,
      isAuction: true,
      period: 'tick',
      volume: 100,
    });
    expect(bars[1]).toMatchObject({
      close: 29.5,
      open: 29.46,
      volume: 4,
      amount: 118,
    });
  });

  it('builds only the current minute bar from cumulative tick totals', () => {
    const baseBars = [
      {
        time: '2026-06-01T09:30:00',
        open: 10,
        high: 10,
        low: 10,
        close: 10,
        preClose: 9.8,
        volume: 100,
        amount: 1000,
      },
      {
        time: '2026-06-01T09:31:00',
        open: 10.1,
        high: 10.2,
        low: 10.1,
        close: 10.2,
        preClose: 10,
        volume: 50,
        amount: 510,
      },
    ];

    const bar = buildTickMinuteBar(baseBars, {
      stockCode: '002594.SZ',
      time: '2026-06-01T09:32:12',
      lastPrice: 10.3,
      preClose: 9.8,
      volume: 180,
      amount: 1830,
    });

    expect(getMinuteMs(bar?.time)).toBe(getMinuteMs('2026-06-01T09:32:00'));
    expect(bar).toMatchObject({
      stockCode: '002594.SZ',
      period: '1m',
      open: 10.2,
      high: 10.3,
      low: 10.3,
      close: 10.3,
      preClose: 9.8,
      volume: 30,
      amount: 320,
      source: 'marketTicks',
      isRealtime: true,
    });
  });

  it('replaces a temporary minute with the real 1m bar when it arrives', () => {
    const minute = '2026-06-01T09:32:00';
    const previous = new Map<number, Record<string, unknown>>([
      [
        getMinuteMs(minute) || 0,
        {
          time: minute,
          close: 10.3,
          volume: 30,
          source: 'marketTicks',
          isRealtime: true,
        },
      ],
    ]);

    const merged = mergeKLines(previous, [
      {
        time: minute,
        open: 10.22,
        high: 10.35,
        low: 10.2,
        close: 10.31,
        volume: 45,
        amount: 463.95,
      },
    ]);
    const replacement = merged.get(getMinuteMs(minute) || 0);

    expect(replacement).toMatchObject({
      close: 10.31,
      volume: 45,
      source: 'marketKlines',
      isRealtime: false,
    });
  });

  it('keeps miniQMT 1m volume when cumulative tick delta is clearly stale', () => {
    expect(resolveTickMinuteMetric(775307, 45587, 5670)).toBe(5670);
  });

  it('uses cumulative tick delta when it matches the current minute', () => {
    expect(resolveTickMinuteMetric(180, 150, 0)).toBe(30);
  });

  it('does not apply tick overlay while 1m backfill is still catching up', () => {
    expect(
      isKLineBaseReadyForTick([{ time: '2026-06-01T09:30:00', close: 53.49 }], {
        time: '2026-06-01T15:00:02',
        lastPrice: 53.85,
      })
    ).toBe(false);
  });

  it('applies tick overlay once 1m bars are complete to the previous minute', () => {
    expect(
      isKLineBaseReadyForTick(
        [
          { time: '2026-06-01T14:58:00', close: 53.84 },
          { time: '2026-06-01T14:59:00', close: 53.84 },
        ],
        { time: '2026-06-01T15:00:02', lastPrice: 53.85 }
      )
    ).toBe(true);
  });
});

describe('intraday query date range', () => {
  const tradingDays = ['2026-05-29', '2026-06-01', '2026-06-02'];

  it('waits for trading calendar before querying intraday data', () => {
    expect(
      getTickDateRange([], '1d', new Date('2026-06-02T00:30:00.000Z'))
    ).toEqual({
      startTime: undefined,
      endTime: undefined,
    });
  });

  it('uses an authoritative target date without waiting for another calendar query', () => {
    expect(
      getIntradayQueryDateRange(
        [],
        '1d',
        new Date('2026-08-19T07:00:00.000Z'),
        '2026-08-19'
      )
    ).toEqual({
      startTime: '2026-08-19 00:00:00',
      endTime: '2026-08-19 23:59:59',
      usesAuthoritativeTarget: true,
    });
  });

  it('keeps calendar-based date selection for multi-day mode', () => {
    expect(
      getIntradayQueryDateRange(
        tradingDays,
        '5d',
        new Date('2026-06-02T07:00:00.000Z'),
        '2026-06-02'
      )
    ).toEqual({
      startTime: '2026-05-29 00:00:00',
      endTime: '2026-06-02 23:59:59',
      usesAuthoritativeTarget: false,
    });
  });

  it('uses the previous trading day at 09:14:59 on a trading day', () => {
    expect(
      getTickDateRange(tradingDays, '1d', new Date('2026-06-02T01:14:59.000Z'))
    ).toEqual({
      startTime: '2026-06-01 00:00:00',
      endTime: '2026-06-01 23:59:59',
    });
  });

  it('uses the current trading day from 09:15 onward', () => {
    expect(
      getTickDateRange(tradingDays, '1d', new Date('2026-06-02T01:15:00.000Z'))
    ).toEqual({
      startTime: '2026-06-02 00:00:00',
      endTime: '2026-06-02 23:59:59',
    });
  });

  it('keeps the current trading day after close', () => {
    expect(
      getTickDateRange(tradingDays, '1d', new Date('2026-06-02T08:30:00.000Z'))
    ).toEqual({
      startTime: '2026-06-02 00:00:00',
      endTime: '2026-06-02 23:59:59',
    });
  });

  it('uses the previous trading day on non-trading days', () => {
    expect(
      getTickDateRange(tradingDays, '1d', new Date('2026-05-31T02:00:00.000Z'))
    ).toEqual({
      startTime: '2026-05-29 00:00:00',
      endTime: '2026-05-29 23:59:59',
    });
  });

  it('falls back to the previous trading day when the current session has no stored bars', () => {
    expect(
      getFallbackTradingDateRange(tradingDays, '2026-06-02 00:00:00')
    ).toEqual({
      startTime: '2026-06-01 00:00:00',
      endTime: '2026-06-01 23:59:59',
    });
  });

  it('does not invent a fallback date before the calendar provides one', () => {
    expect(
      getFallbackTradingDateRange(['2026-06-02'], '2026-06-02 00:00:00')
    ).toEqual({
      startTime: undefined,
      endTime: undefined,
    });
  });
});
