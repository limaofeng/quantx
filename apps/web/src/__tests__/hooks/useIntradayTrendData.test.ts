import { describe, expect, it } from 'vitest';

import { toChartTimestamp } from '@/components/trading-chart/utils/time-utils';
import { __intradayTrendDataTestUtils } from '@/hooks/useIntradayTrendData';

const { buildIntradayTrendSeries } = __intradayTrendDataTestUtils;

describe('useIntradayTrendData helpers', () => {
  it('builds sparkline data from the same 1m bars used by the trading chart', () => {
    const { lineData } = buildIntradayTrendSeries([
      {
        time: '2026-06-01T09:30:00',
        close: 10,
      },
      {
        time: '2026-06-01T09:31:00',
        close: 10.2,
      },
      {
        time: '2026-06-01T09:32:00',
        lastPrice: 10.3,
      },
    ]);

    expect(lineData).toEqual([
      { time: toChartTimestamp('2026-06-01T09:30:00'), value: 10 },
      { time: toChartTimestamp('2026-06-01T09:31:00'), value: 10.2 },
      { time: toChartTimestamp('2026-06-01T09:32:00'), value: 10.3 },
    ]);
  });

  it('breaks non-lunch missing-minute gaps without breaking the lunch gap', () => {
    const { lineData } = buildIntradayTrendSeries([
      { time: '2026-06-01T10:00:00', close: 10 },
      { time: '2026-06-01T10:05:00', close: 10.5 },
      { time: '2026-06-01T11:30:00', close: 10.8 },
      { time: '2026-06-01T13:00:00', close: 10.9 },
    ]);

    expect(lineData).toContainEqual({
      time: toChartTimestamp('2026-06-01T10:01:00'),
    });
    expect(lineData).not.toContainEqual({
      time: toChartTimestamp('2026-06-01T11:31:00'),
    });
  });

  it('keeps sparse call auction ticks connected and expands the visible range', () => {
    const { lineData, visibleRange } = buildIntradayTrendSeries([
      { time: '2026-06-01T09:15:03', lastPrice: 10 },
      { time: '2026-06-01T09:25:00', lastPrice: 10.2 },
    ]);

    expect(visibleRange.from).toBe(toChartTimestamp('2026-06-01T09:15:00'));
    expect(lineData).toEqual([
      { time: toChartTimestamp('2026-06-01T09:15:03'), value: 10 },
      { time: toChartTimestamp('2026-06-01T09:25:00'), value: 10.2 },
    ]);
  });

  it('adds a previous point when only one valid bar exists', () => {
    const { lineData } = buildIntradayTrendSeries([
      { time: '2026-06-01T09:31:00', close: 10.2 },
    ]);

    expect(lineData).toEqual([
      { time: toChartTimestamp('2026-06-01T09:30:00'), value: 10.2 },
      { time: toChartTimestamp('2026-06-01T09:31:00'), value: 10.2 },
    ]);
  });
});
