import { describe, expect, it } from 'vitest';

import { validateMarketDataSync } from '@/features/system/pages/marketDataSyncValidation';

const validInput = {
  startDate: '2026-07-23',
  endDate: '2026-07-29',
  targetMode: 'sectors' as const,
  stockCount: 0,
  periods: ['1d'],
  skipDownload: false,
  computeDailySignals: true,
};

describe('validateMarketDataSync', () => {
  it('requires 1d when daily indicators are enabled', () => {
    expect(
      validateMarketDataSync({ ...validInput, periods: ['1m'] })
    ).toContain('1d');
  });

  it('limits indicator backfills to 30 days', () => {
    expect(
      validateMarketDataSync({
        ...validInput,
        startDate: '2026-06-01',
      })
    ).toContain('最多 30 天');
  });

  it('allows a longer K-line-only sync', () => {
    expect(
      validateMarketDataSync({
        ...validInput,
        startDate: '2026-06-01',
        computeDailySignals: false,
      })
    ).toBeNull();
  });

  it('accepts indicator-only mode only with indicators and 1d', () => {
    expect(
      validateMarketDataSync({ ...validInput, skipDownload: true })
    ).toBeNull();
    expect(
      validateMarketDataSync({
        ...validInput,
        skipDownload: true,
        computeDailySignals: false,
      })
    ).toContain('仅补算指标');
  });
});
