import {
  formatMarketSessionMinute,
  toMarketSessionMinute,
} from '@/features/dashboard/marketIntradayAxis';

describe('market intraday trading-minute axis', () => {
  it.each([
    ['2026-08-13T01:30:00.000Z', 0],
    ['2026-08-13T02:30:00.000Z', 60],
    ['2026-08-13T03:30:00.000Z', 120],
    ['2026-08-13T05:00:00.000Z', 120],
    ['2026-08-13T06:00:00.000Z', 180],
    ['2026-08-13T07:00:00.000Z', 240],
  ])('compresses %s to trading minute %i', (timestamp, expected) => {
    expect(toMarketSessionMinute(new Date(timestamp))).toBe(expected);
  });

  it('removes every timestamp inside the lunch break', () => {
    expect(
      toMarketSessionMinute(new Date('2026-08-13T04:35:00.000Z'))
    ).toBeNull();
  });

  it('formats the compressed split point as both session boundaries', () => {
    expect(formatMarketSessionMinute(0)).toBe('09:30');
    expect(formatMarketSessionMinute(120)).toBe('11:30 / 13:00');
    expect(formatMarketSessionMinute(240)).toBe('15:00');
  });
});
