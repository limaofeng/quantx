import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  formatSignedPercent,
  integerValue,
  numberValue,
  replayDatePreset,
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

  it('does not present a stock code as an instrument name', () => {
    expect(resolveInstrumentName('600000.SH', '600000', '浦发银行')).toBe(
      '浦发银行'
    );
    expect(resolveInstrumentName('600000.SH', '600000.SH', null)).toBe(
      '600000.SH'
    );
  });

  it('builds trading-day presets without counting weekends', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 6, 29, 12, 0, 0));

    expect(replayDatePreset(5)).toEqual({
      start: '2026-07-23',
      end: '2026-07-29',
    });
  });
});
