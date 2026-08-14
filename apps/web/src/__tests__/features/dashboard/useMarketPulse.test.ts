import { selectMarketPulseSnapshot } from '@/features/dashboard/hooks/useMarketPulse';

describe('selectMarketPulseSnapshot', () => {
  it('uses a newer same-day intraday snapshot instead of yesterday close', () => {
    expect(
      selectMarketPulseSnapshot({
        dailySnapshotDate: '2026-08-12',
        intradayTotal: 5_243,
        intradayUpdatedAt: '2026-08-13T14:48:30+08:00',
        phase: 'post-close',
        targetTradingDate: '2026-08-13',
      })
    ).toBe('intraday');
  });

  it('does not expose yesterday close as the current trading-day snapshot', () => {
    expect(
      selectMarketPulseSnapshot({
        dailySnapshotDate: '2026-08-12',
        intradayTotal: 0,
        intradayUpdatedAt: null,
        phase: 'post-close',
        targetTradingDate: '2026-08-13',
      })
    ).toBe('unavailable');
  });

  it('uses the latest completed daily snapshot on a non-trading day', () => {
    expect(
      selectMarketPulseSnapshot({
        dailySnapshotDate: '2026-08-14',
        intradayTotal: 5_100,
        intradayUpdatedAt: '2026-08-14T15:00:00+08:00',
        phase: 'closed',
        targetTradingDate: '2026-08-14',
      })
    ).toBe('daily');
  });

  it('prefers current intraday data during continuous trading', () => {
    expect(
      selectMarketPulseSnapshot({
        dailySnapshotDate: '2026-08-13',
        intradayTotal: 5_200,
        intradayUpdatedAt: '2026-08-13T10:30:00+08:00',
        phase: 'morning',
        targetTradingDate: '2026-08-13',
      })
    ).toBe('intraday');
  });
});
