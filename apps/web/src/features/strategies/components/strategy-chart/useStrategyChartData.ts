import type { Time } from 'lightweight-charts';
import { useMemo } from 'react';

import { useInfiniteKLines } from '@/hooks';
import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';

import { useBacktestKLines } from '../../hooks/useBacktestKLines';
import { useBacktestTicks } from '../../hooks/useBacktestTicks';
import type { StrategyTickData } from '../../hooks/useStrategyTicks';

import type {
  StrategyChartDataState,
  StrategyChartMode,
  StrategyChartPeriod,
  StrategyChartRange,
} from './types';
import { exactBacktestRange, normalizeBacktestRange } from './utils';

function isCalendarPeriod(period: StrategyChartPeriod) {
  return period === 'DAY_1' || period === 'WEEK_1' || period === 'MONTH_1';
}

function toTime(value: string, period: StrategyChartPeriod): Time {
  if (isCalendarPeriod(period)) return value.split('T')[0] as Time;
  return Math.floor(new Date(value).getTime() / 1000) as Time;
}

export function useStrategyChartData({
  stockCode,
  mode,
  period,
  backtestRange,
  liveTicks = [],
}: {
  stockCode?: string;
  mode: StrategyChartMode;
  period: StrategyChartPeriod;
  backtestRange?: StrategyChartRange | null;
  liveTicks?: StrategyTickData[];
}): StrategyChartDataState {
  const isBacktest = mode === 'backtest';
  const isTickPeriod = period === 'TICK';
  const exactRange = useMemo(
    () => exactBacktestRange(backtestRange),
    [backtestRange]
  );
  const calendarRange = useMemo(
    () => normalizeBacktestRange(backtestRange),
    [backtestRange]
  );
  const hasRange = !!exactRange.startTime && !!exactRange.endTime;
  const klineQueryRange = isCalendarPeriod(period) ? calendarRange : exactRange;

  const {
    data: liveKlines,
    loading: liveLoading,
    loadMore: loadMoreLive,
    hasMore: hasMoreLive,
  } = useInfiniteKLines(
    stockCode || '',
    isTickPeriod ? 'DAY_1' : period,
    !!stockCode && !isBacktest && !isTickPeriod
  );

  const {
    data: backtestKlines,
    loading: backtestKlinesLoading,
    loadMore: loadMoreBacktestKlines,
    hasMore: hasMoreBacktestKlines,
  } = useBacktestKLines({
    stockCode,
    period: isTickPeriod ? 'DAY_1' : period,
    startTime: klineQueryRange.startTime,
    endTime: klineQueryRange.endTime,
    boundaryStartTime: exactRange.startTime,
    boundaryEndTime: exactRange.endTime,
    boundaryMode: isCalendarPeriod(period) ? 'date' : 'timestamp',
    enabled: !!stockCode && hasRange && !isTickPeriod,
    limit: period === 'DAY_1' ? 260 : 500,
  });

  const {
    data: backtestTicks,
    loading: ticksLoading,
    loadMore: loadMoreTicks,
    hasMore: hasMoreTicks,
  } = useBacktestTicks({
    stockCode,
    startTime: exactRange.startTime,
    endTime: exactRange.endTime,
    enabled: !!stockCode && hasRange && isTickPeriod,
    limit: 1000,
  });

  const rawKlines = isBacktest ? backtestKlines : liveKlines;
  const rawTicks = isBacktest ? backtestTicks : liveTicks;

  const klinePriceData = useMemo(
    () =>
      rawKlines
        .map(item => ({
          time: toTime(item.time, period),
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        }))
        .filter(item => item.open > 0 && item.high > 0 && item.low > 0),
    [period, rawKlines]
  );

  const klineVolumeData = useMemo(
    () =>
      rawKlines
        .map(item => ({
          time: toTime(item.time, period),
          value: item.volume || 0,
          color:
            item.close >= item.open
              ? `${FINANCIAL_CHART_COLORS.up}59`
              : `${FINANCIAL_CHART_COLORS.down}59`,
        }))
        .filter(item => item.value >= 0),
    [period, rawKlines]
  );

  const tickPriceData = useMemo(
    () =>
      rawTicks
        .map(tick => ({
          time: Math.floor(new Date(tick.time).getTime() / 1000) as Time,
          value: tick.lastPrice,
        }))
        .filter(item => Number.isFinite(item.time) && item.value > 0),
    [rawTicks]
  );

  const tickVolumeData = useMemo(() => {
    let previousVolume = 0;
    return rawTicks
      .map((tick, index) => {
        const time = Math.floor(new Date(tick.time).getTime() / 1000);
        const currentVolume = Number(tick.volume || 0);
        const intervalVolume =
          index === 0 ? 0 : Math.max(0, currentVolume - previousVolume);
        previousVolume = currentVolume;
        return {
          time: time as Time,
          value: intervalVolume,
          color: 'rgba(56, 189, 248, 0.28)',
        };
      })
      .filter(item => Number.isFinite(item.time) && item.value >= 0);
  }, [rawTicks]);

  return {
    priceData: isTickPeriod ? tickPriceData : klinePriceData,
    volumeData: isTickPeriod ? tickVolumeData : klineVolumeData,
    loading: isTickPeriod
      ? isBacktest
        ? ticksLoading
        : false
      : isBacktest
        ? backtestKlinesLoading
        : liveLoading,
    hasMore: isTickPeriod
      ? isBacktest
        ? hasMoreTicks
        : false
      : isBacktest
        ? hasMoreBacktestKlines
        : hasMoreLive,
    loadMore: isTickPeriod
      ? isBacktest
        ? loadMoreTicks
        : () => {}
      : isBacktest
        ? loadMoreBacktestKlines
        : loadMoreLive,
    hasRange,
    isTickPeriod,
  };
}
