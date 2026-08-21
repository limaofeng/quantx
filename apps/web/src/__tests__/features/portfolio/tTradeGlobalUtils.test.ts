import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  formatSignedPercent,
  integerValue,
  numberValue,
  quoteTone,
  replayDatePreset,
  replayIdempotencyKey,
  resolveInstrumentName,
  signalHistoryCategory,
  signalReasonLabel,
  signalStatusPresentation,
} from '@/features/portfolio/pages/t-trade-global/utils';

describe('TTradeGlobal utilities', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('normalizes terminal signal states without losing the audit reason', () => {
    expect(
      signalStatusPresentation('expired', 'price_deviation_exceeded').label
    ).toBe('价格偏离');
    expect(signalHistoryCategory('cancelled')).toBe('IGNORED');
    expect(signalReasonLabel('USER_REJECTED', 'REJECTED')).toBe(
      '人工忽略本次信号'
    );
  });

  it('uses conservative numeric fallbacks and whole-number settings', () => {
    expect(numberValue('not-a-number', 12_000)).toBe(12_000);
    expect(integerValue('3.9', 1)).toBe(3);
    expect(formatSignedPercent(Number.NaN)).toBe('--');
    expect(formatSignedPercent(1.25)).toBe('+1.25%');
  });

  it('uses the holding palette for live T-trade quote declines', () => {
    expect(quoteTone(1.25)).toBe('text-market-up');
    expect(quoteTone(-1.25)).toBe('text-holding-down');
  });

  it('does not present a stock code as an instrument name', () => {
    expect(resolveInstrumentName('600000.SH', '600000', '浦发银行')).toBe(
      '浦发银行'
    );
    expect(resolveInstrumentName('600000.SH', '600000.SH', null)).toBe(
      '600000.SH'
    );
  });

  it('builds presets from completed weekdays without counting weekends', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 29, 12, 0, 0));

    expect(replayDatePreset(5)).toEqual({
      start: '2026-07-22',
      end: '2026-07-28',
    });
  });

  it('uses the server calendar and excludes an unfinished trading day', () => {
    const tradingCalendar = [
      '2026-07-23',
      '2026-07-24',
      '2026-07-27',
      '2026-07-28',
      '2026-07-29',
      '2026-07-30',
      '2026-07-31',
      '2026-08-03',
      '2026-08-04',
      '2026-08-05',
      '2026-08-06',
      '2026-08-07',
      '2026-08-10',
      '2026-08-11',
      '2026-08-12',
      '2026-08-13',
      '2026-08-14',
      '2026-08-17',
      '2026-08-18',
      '2026-08-19',
      '2026-08-20',
    ];

    expect(
      replayDatePreset(
        20,
        tradingCalendar,
        new Date('2026-08-19T23:00:00Z')
      )
    ).toEqual({ start: '2026-07-23', end: '2026-08-19' });
    expect(
      replayDatePreset(
        20,
        tradingCalendar,
        new Date('2026-08-20T07:01:00Z')
      )
    ).toEqual({ start: '2026-07-24', end: '2026-08-20' });
  });

  it('excludes an unfinished weekday when the server calendar is unavailable', () => {
    expect(
      replayDatePreset(5, [], new Date('2026-08-19T23:00:00Z'))
    ).toEqual({ start: '2026-08-13', end: '2026-08-19' });
  });

  it('generates a UUID when randomUUID is unavailable on an HTTP LAN origin', () => {
    const getRandomValues = <T extends ArrayBufferView | null>(array: T): T => {
      if (array instanceof Uint8Array) {
        array.set(Array.from({ length: 16 }, (_, index) => index));
      }
      return array;
    };

    expect(replayIdempotencyKey({ getRandomValues })).toBe(
      '00010203-0405-4607-8809-0a0b0c0d0e0f'
    );
  });
});
