import { useEffect, useMemo, useState } from 'react';
import { useSubscription } from 'urql';

import {
  getCallAuctionDateRange,
  getShanghaiDateKey,
  getTickDateRange,
  isCallAuctionTimestamp,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import { useKLines, useTicks } from '@/features/trading/hooks/useTrading';
import { gql } from '@/generated/gql';
import { useTradingDays } from '@/hooks/useTradingDays';

const CALL_AUCTION_TICK_LIMIT = 1200;
const TRADING_DATE_KEY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

interface IntradayKLineOptions {
  targetTradingDate?: string | null;
}

interface IntradayPoint {
  stockCode?: string | null;
  period?: string | null;
  time?: string | number | Date | null;
  sourceTime?: string | number | Date | null;
  lastPrice?: unknown;
  currentPrice?: unknown;
  open?: unknown;
  high?: unknown;
  low?: unknown;
  close?: unknown;
  preClose?: unknown;
  lastClose?: unknown;
  volume?: unknown;
  amount?: unknown;
  source?: unknown;
  isRealtime?: unknown;
}

interface IntradayBar extends IntradayPoint {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

const MarketKLineSubscription = gql(`
  subscription Market_KLines($stockList: [String!]!, $periods: [String!]!) {
    marketKlines(stockList: $stockList, periods: $periods) {
      stockCode
      period
      time
      open
      high
      low
      close
      preClose
      volume
      amount
    }
  }
`);

const MarketTickSubscription = gql(`
  subscription Market_Ticks_ForIntraday($stockList: [String!]!) {
    marketTicks(stockList: $stockList) {
      stockCode
      period
      time
      lastPrice
      open
      high
      low
      preClose
      volume
      amount
    }
  }
`);

const toFiniteNumber = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const toValidPrice = (...values: unknown[]): number | null => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
};

const getTimeMs = (value: unknown): number | null => {
  if (
    value !== null &&
    value !== undefined &&
    typeof value !== 'string' &&
    typeof value !== 'number' &&
    !(value instanceof Date)
  ) {
    return null;
  }
  const date = parseMarketDate(value);
  const time = date?.getTime() ?? NaN;
  return Number.isFinite(time) ? time : null;
};

const getTickTimeMs = (tick: IntradayPoint): number | null => {
  return getTimeMs(tick?.sourceTime ?? tick?.time);
};

const getMinuteMs = (value: unknown): number | null => {
  const time = getTimeMs(value);
  return time === null ? null : Math.floor(time / 60_000) * 60_000;
};

const mergeKLines = (
  previous: Map<number, IntradayPoint>,
  klines: IntradayPoint[]
): Map<number, IntradayPoint> => {
  const next = new Map(previous);

  klines.forEach(kline => {
    const key = getMinuteMs(kline?.time);
    if (key === null) return;
    next.set(key, {
      ...next.get(key),
      ...kline,
      source: 'marketKlines',
      isRealtime: false,
    });
  });

  return next;
};

const mergeTicks = (
  previous: Map<number, IntradayPoint>,
  ticks: IntradayPoint[]
): Map<number, IntradayPoint> => {
  const next = new Map(previous);

  ticks.forEach(tick => {
    const key = getTickTimeMs(tick);
    if (key === null) return;
    next.set(key, tick);
  });

  return next;
};

const resolveTickMinuteMetric = (
  tickValue: number | null,
  cumulativeCompletedValue: number,
  existingMinuteValue: number
) => {
  if (tickValue === null || tickValue < cumulativeCompletedValue) {
    return existingMinuteValue;
  }

  const estimatedMinuteValue = Math.max(
    0,
    tickValue - cumulativeCompletedValue
  );
  if (existingMinuteValue > 0) {
    const excess = estimatedMinuteValue - existingMinuteValue;
    if (
      estimatedMinuteValue > existingMinuteValue * 10 &&
      excess > Math.max(existingMinuteValue * 5, 10_000)
    ) {
      return existingMinuteValue;
    }
    return estimatedMinuteValue;
  }

  if (
    cumulativeCompletedValue > 0 &&
    estimatedMinuteValue > cumulativeCompletedValue * 0.5
  ) {
    return existingMinuteValue;
  }

  return estimatedMinuteValue;
};

const resolveTickDeltaMetric = (
  currentValue: number | null,
  previousValue: number | null
) => {
  if (currentValue === null) return 0;
  if (previousValue === null) return Math.max(0, currentValue);
  if (currentValue < previousValue) return 0;
  return Math.max(0, currentValue - previousValue);
};

const buildCallAuctionTickBars = (ticks: IntradayPoint[]): IntradayBar[] => {
  const auctionTicks = ticks
    .filter(tick => isCallAuctionTimestamp(tick?.sourceTime ?? tick?.time))
    .map(tick => ({
      tick,
      timeMs: getTickTimeMs(tick),
      price: toValidPrice(
        tick?.lastPrice,
        tick?.currentPrice,
        tick?.open,
        tick?.high,
        tick?.low
      ),
    }))
    .filter(
      (
        item
      ): item is {
        price: number;
        tick: IntradayPoint;
        timeMs: number;
      } => item.timeMs !== null && item.price !== null
    )
    .sort((a, b) => a.timeMs - b.timeMs);

  let previousPrice: number | null = null;
  let previousVolume: number | null = null;
  let previousAmount: number | null = null;

  return auctionTicks.map(({ price, tick, timeMs }) => {
    const open = previousPrice || price;
    const high = Math.max(
      toValidPrice(tick?.high, price) || price,
      open,
      price
    );
    const low = Math.min(toValidPrice(tick?.low, price) || price, open, price);
    const currentVolume = toFiniteNumber(tick?.volume);
    const currentAmount = toFiniteNumber(tick?.amount);
    const volume = resolveTickDeltaMetric(currentVolume, previousVolume);
    const amount = resolveTickDeltaMetric(currentAmount, previousAmount);

    previousPrice = price;
    if (currentVolume !== null) previousVolume = currentVolume;
    if (currentAmount !== null) previousAmount = currentAmount;

    return {
      ...tick,
      period: 'tick',
      time: new Date(timeMs).toISOString(),
      open,
      high,
      low,
      close: price,
      preClose: toValidPrice(tick?.preClose, tick?.lastClose),
      volume,
      amount,
      source: typeof tick.source === 'string' ? tick.source : 'ticks',
      isAuction: true,
      isRealtime: Boolean(tick?.isRealtime || tick?.source === 'marketTicks'),
    };
  });
};

const buildTickMinuteBar = (
  baseBars: IntradayPoint[],
  latestTick: IntradayPoint
): IntradayBar | null => {
  const tickMinuteMs = getMinuteMs(latestTick?.time);
  if (tickMinuteMs === null) return null;

  const price = toValidPrice(
    latestTick?.lastPrice,
    latestTick?.currentPrice,
    latestTick?.open,
    latestTick?.high,
    latestTick?.low
  );
  if (price === null) return null;

  const tickDate = new Date(tickMinuteMs);
  const tickDateKey = getShanghaiDateKey(tickDate);
  const completedBars = baseBars.filter(item => {
    const barTime = getMinuteMs(item?.time);
    const barDate = parseMarketDate(item?.time);
    return (
      barTime !== null &&
      barTime < tickMinuteMs &&
      barDate !== null &&
      getShanghaiDateKey(barDate) === tickDateKey
    );
  });

  const existing = baseBars.find(
    item => getMinuteMs(item?.time) === tickMinuteMs
  );
  const previous = [...completedBars].pop();
  const cumulativeVolume = completedBars.reduce(
    (sum, item) => sum + Math.max(0, toFiniteNumber(item?.volume) || 0),
    0
  );
  const cumulativeAmount = completedBars.reduce(
    (sum, item) => sum + Math.max(0, toFiniteNumber(item?.amount) || 0),
    0
  );

  const tickVolume = toFiniteNumber(latestTick?.volume);
  const tickAmount = toFiniteNumber(latestTick?.amount);
  const existingVolume = toFiniteNumber(existing?.volume) || 0;
  const existingAmount = toFiniteNumber(existing?.amount) || 0;
  const minuteVolume = resolveTickMinuteMetric(
    tickVolume,
    cumulativeVolume,
    existingVolume
  );
  const minuteAmount = resolveTickMinuteMetric(
    tickAmount,
    cumulativeAmount,
    existingAmount
  );

  const open = toValidPrice(
    existing?.open,
    previous?.close,
    latestTick?.open,
    price
  );
  const existingHigh = toValidPrice(existing?.high, price) || price;
  const existingLow = toValidPrice(existing?.low, price) || price;

  return {
    ...existing,
    stockCode: latestTick.stockCode || existing?.stockCode || undefined,
    period: '1m',
    time: new Date(tickMinuteMs).toISOString(),
    open: open || price,
    high: Math.max(existingHigh, price),
    low: Math.min(existingLow, price),
    close: price,
    preClose: toValidPrice(
      existing?.preClose,
      latestTick?.preClose,
      previous?.preClose
    ),
    volume: minuteVolume,
    amount: minuteAmount,
    source: 'marketTicks',
    isRealtime: true,
  };
};

const isKLineBaseReadyForTick = (
  baseBars: IntradayPoint[],
  latestTick: IntradayPoint | null
) => {
  const tickMinuteMs = getMinuteMs(latestTick?.time);
  if (tickMinuteMs === null || baseBars.length === 0) return false;

  const latestKLineMinuteMs = baseBars.reduce((latest, item) => {
    const minute = getMinuteMs(item?.time);
    return minute === null ? latest : Math.max(latest, minute);
  }, 0);

  return latestKLineMinuteMs >= tickMinuteMs - 60_000;
};

const getFallbackTradingDateRange = (
  tradingDays: string[],
  currentStartTime: string | undefined
) => {
  const anchorDate = currentStartTime?.slice(0, 10);
  if (!anchorDate) {
    return { startTime: undefined, endTime: undefined };
  }

  const previousTradingDay = tradingDays
    .filter(day => day < anchorDate)
    .sort()
    .at(-1);
  if (!previousTradingDay) {
    return { startTime: undefined, endTime: undefined };
  }

  return {
    startTime: `${previousTradingDay} 00:00:00`,
    endTime: `${previousTradingDay} 23:59:59`,
  };
};

const getIntradayQueryDateRange = (
  tradingDays: string[],
  mode: '1d' | '5d',
  now: Date,
  targetTradingDate?: string | null
) => {
  if (
    mode === '1d' &&
    targetTradingDate &&
    TRADING_DATE_KEY_PATTERN.test(targetTradingDate)
  ) {
    return {
      startTime: `${targetTradingDate} 00:00:00`,
      endTime: `${targetTradingDate} 23:59:59`,
      usesAuthoritativeTarget: true,
    };
  }

  return {
    ...getTickDateRange(tradingDays, mode, now),
    usesAuthoritativeTarget: false,
  };
};

const filterIntradayPointsForRange = (
  points: IntradayPoint[],
  stockCode: string,
  startTime: string | undefined,
  endTime: string | undefined
) => {
  const startDate = startTime?.slice(0, 10);
  const endDate = endTime?.slice(0, 10);
  if (!stockCode || !startDate || !endDate) return [];

  return points.filter(point => {
    if (point.stockCode && point.stockCode !== stockCode) return false;
    const parsed = parseMarketDate(point.sourceTime ?? point.time);
    if (!parsed) return false;
    const dateKey = getShanghaiDateKey(parsed);
    return dateKey >= startDate && dateKey <= endDate;
  });
};

export function useIntradayKLines(
  stockCode: string,
  mode: '1d' | '5d' = '1d',
  options: IntradayKLineOptions = {}
) {
  const {
    tradingDays,
    loading: tradingDaysLoading,
    error: tradingDaysError,
  } = useTradingDays();
  const [rangeNow, setRangeNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRangeNow(new Date());
    }, 30_000);

    return () => window.clearInterval(timer);
  }, []);

  const { startTime, endTime, usesAuthoritativeTarget } = useMemo(() => {
    if (!stockCode) {
      return {
        startTime: undefined,
        endTime: undefined,
        usesAuthoritativeTarget: false,
      };
    }
    return getIntradayQueryDateRange(
      tradingDays,
      mode,
      rangeNow,
      options.targetTradingDate
    );
  }, [mode, options.targetTradingDate, rangeNow, stockCode, tradingDays]);

  const isRealtimeRange = useMemo(() => {
    const anchorDate = endTime?.slice(0, 10);
    return !!anchorDate && anchorDate === getShanghaiDateKey(rangeNow);
  }, [endTime, rangeNow]);

  const {
    data: initialKLines,
    loading: initialLoading,
    stale: initialStale,
    error: initialError,
    refresh,
  } = useKLines(stockCode, 'MIN_1', startTime, endTime, {
    order: 'asc',
    pause: !startTime || !endTime,
    requestPolicy: 'cache-and-network',
  });

  const scopedInitialKLines = useMemo(
    () =>
      filterIntradayPointsForRange(
        initialKLines,
        stockCode,
        startTime,
        endTime
      ),
    [endTime, initialKLines, startTime, stockCode]
  );

  const fallbackRange = useMemo(
    () => getFallbackTradingDateRange(tradingDays, startTime),
    [startTime, tradingDays]
  );
  const shouldLoadFallback =
    mode === '1d' &&
    !usesAuthoritativeTarget &&
    !initialLoading &&
    !initialStale &&
    scopedInitialKLines.length === 0 &&
    Boolean(fallbackRange.startTime && fallbackRange.endTime);
  const {
    data: fallbackKLines,
    loading: fallbackLoading,
    stale: fallbackStale,
    error: fallbackError,
  } = useKLines(
    stockCode,
    'MIN_1',
    fallbackRange.startTime,
    fallbackRange.endTime,
    {
      order: 'asc',
      pause: !shouldLoadFallback,
      requestPolicy: 'cache-and-network',
    }
  );

  const scopedFallbackKLines = useMemo(
    () =>
      filterIntradayPointsForRange(
        fallbackKLines,
        stockCode,
        fallbackRange.startTime,
        fallbackRange.endTime
      ),
    [fallbackKLines, fallbackRange.endTime, fallbackRange.startTime, stockCode]
  );

  const { startTime: auctionStartTime, endTime: auctionEndTime } =
    useMemo(() => {
      if (!stockCode || mode !== '1d' || !endTime) {
        return { startTime: undefined, endTime: undefined };
      }
      return getCallAuctionDateRange(endTime);
    }, [endTime, mode, stockCode]);

  const {
    data: initialAuctionTicks,
    loading: auctionTicksLoading,
    stale: auctionTicksStale,
    error: auctionTicksError,
    refresh: refreshAuctionTicks,
  } = useTicks(stockCode, auctionStartTime, auctionEndTime, {
    limit: CALL_AUCTION_TICK_LIMIT,
    order: 'asc',
    pause: !auctionStartTime || !auctionEndTime,
    requestPolicy: 'network-only',
  });

  const scopedInitialAuctionTicks = useMemo(
    () =>
      filterIntradayPointsForRange(
        initialAuctionTicks,
        stockCode,
        auctionStartTime,
        auctionEndTime
      ),
    [auctionEndTime, auctionStartTime, initialAuctionTicks, stockCode]
  );

  const dataScopeKey = `${stockCode}|${mode}|${usesAuthoritativeTarget ? 'target' : 'calendar'}|${startTime || ''}|${endTime || ''}`;

  const [klineMap, setKlineMap] = useState<Map<number, IntradayPoint>>(
    new Map()
  );
  const [auctionTickMap, setAuctionTickMap] = useState<
    Map<number, IntradayPoint>
  >(new Map());
  const [latestTick, setLatestTick] = useState<IntradayPoint | null>(null);
  const [activeDataScopeKey, setActiveDataScopeKey] = useState(dataScopeKey);

  const [klineSubResult] = useSubscription({
    query: MarketKLineSubscription,
    variables: { stockList: [stockCode], periods: ['1m'] },
    pause: !stockCode || !isRealtimeRange,
  });

  const [tickSubResult] = useSubscription({
    query: MarketTickSubscription,
    variables: { stockList: [stockCode] },
    pause: !stockCode || !isRealtimeRange,
  });

  useEffect(() => {
    setKlineMap(new Map());
    setAuctionTickMap(new Map());
    setLatestTick(null);
    setActiveDataScopeKey(dataScopeKey);
  }, [dataScopeKey]);

  useEffect(() => {
    const kline = klineSubResult.data?.marketKlines;
    if (!kline || kline.stockCode !== stockCode || kline.period !== '1m')
      return;
    setKlineMap(prev => mergeKLines(prev, [kline]));
  }, [klineSubResult.data, stockCode]);

  useEffect(() => {
    const tick = tickSubResult.data?.marketTicks;
    if (!tick || tick.stockCode !== stockCode) return;
    const liveTick = { ...tick, isRealtime: true, source: 'marketTicks' };
    setLatestTick(liveTick);
    if (isCallAuctionTimestamp(liveTick?.time)) {
      setAuctionTickMap(prev => mergeTicks(prev, [liveTick]));
    }
  }, [tickSubResult.data, stockCode]);

  const baseBars = useMemo(() => {
    let merged = mergeKLines(new Map(), scopedInitialKLines);
    if (shouldLoadFallback && scopedInitialKLines.length === 0) {
      merged = mergeKLines(merged, scopedFallbackKLines);
    }
    if (activeDataScopeKey === dataScopeKey) {
      merged = mergeKLines(merged, Array.from(klineMap.values()));
    }
    return Array.from(merged.values()).sort(
      (a, b) => (getMinuteMs(a?.time) || 0) - (getMinuteMs(b?.time) || 0)
    );
  }, [
    activeDataScopeKey,
    dataScopeKey,
    klineMap,
    scopedFallbackKLines,
    scopedInitialKLines,
    shouldLoadFallback,
  ]);

  const baseReadyForLatestTick = useMemo(
    () => isKLineBaseReadyForTick(baseBars, latestTick),
    [baseBars, latestTick]
  );

  const currentRangeDate = endTime?.slice(0, 10);
  const baseHasCurrentRangeBars = useMemo(
    () =>
      Boolean(currentRangeDate) &&
      baseBars.some(bar => {
        const date = parseMarketDate(bar?.time);
        return date && getShanghaiDateKey(date) === currentRangeDate;
      }),
    [baseBars, currentRangeDate]
  );

  const latestTickIsCallAuction = useMemo(
    () => isCallAuctionTimestamp(latestTick?.sourceTime ?? latestTick?.time),
    [latestTick]
  );

  const auctionBars = useMemo(() => {
    let merged = mergeTicks(new Map(), scopedInitialAuctionTicks);
    if (activeDataScopeKey === dataScopeKey) {
      merged = mergeTicks(merged, Array.from(auctionTickMap.values()));
    }
    return buildCallAuctionTickBars(Array.from(merged.values()));
  }, [
    activeDataScopeKey,
    auctionTickMap,
    dataScopeKey,
    scopedInitialAuctionTicks,
  ]);

  useEffect(() => {
    if (!stockCode || !startTime || !endTime) return;
    if (
      !tickSubResult.error &&
      baseHasCurrentRangeBars &&
      (!latestTick || latestTickIsCallAuction || baseReadyForLatestTick)
    ) {
      return;
    }

    const timer = window.setInterval(
      () => {
        refresh();
      },
      baseBars.length > 0 ? 30_000 : 5_000
    );

    return () => window.clearInterval(timer);
  }, [
    baseBars.length,
    baseHasCurrentRangeBars,
    baseReadyForLatestTick,
    endTime,
    latestTick,
    latestTickIsCallAuction,
    refresh,
    startTime,
    stockCode,
    tickSubResult.error,
  ]);

  useEffect(() => {
    if (
      !stockCode ||
      mode !== '1d' ||
      !auctionStartTime ||
      !auctionEndTime ||
      !isRealtimeRange ||
      !isCallAuctionTimestamp(rangeNow)
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      refreshAuctionTicks();
    }, 5_000);

    return () => window.clearInterval(timer);
  }, [
    auctionEndTime,
    auctionStartTime,
    isRealtimeRange,
    mode,
    rangeNow,
    refreshAuctionTicks,
    stockCode,
  ]);

  const data = useMemo(() => {
    const tickBar =
      latestTick && !latestTickIsCallAuction && baseReadyForLatestTick
        ? buildTickMinuteBar(baseBars, latestTick)
        : null;

    const withCurrentData = new Map<number, IntradayPoint>();
    auctionBars.forEach(bar => {
      const time = getTimeMs(bar?.time);
      if (time !== null) withCurrentData.set(time, bar);
    });
    baseBars.forEach(bar => {
      const time = getTimeMs(bar?.time);
      if (time !== null) withCurrentData.set(time, bar);
    });

    if (tickBar) {
      const tickMinuteMs = getMinuteMs(tickBar.time);
      if (tickMinuteMs !== null) {
        withCurrentData.set(tickMinuteMs, tickBar);
      }
    }
    return Array.from(withCurrentData.values()).sort(
      (a, b) => (getTimeMs(a?.time) || 0) - (getTimeMs(b?.time) || 0)
    );
  }, [
    auctionBars,
    baseBars,
    baseReadyForLatestTick,
    latestTick,
    latestTickIsCallAuction,
  ]);

  const callAuctionDataIsPrimary =
    mode === '1d' &&
    isRealtimeRange &&
    isCallAuctionTimestamp(rangeNow) &&
    baseBars.length === 0 &&
    auctionBars.length === 0;

  return {
    data,
    loading:
      ((!usesAuthoritativeTarget && tradingDaysLoading) ||
        initialLoading ||
        initialStale ||
        (shouldLoadFallback && (fallbackLoading || fallbackStale)) ||
        (callAuctionDataIsPrimary &&
          (auctionTicksLoading || auctionTicksStale))) &&
      data.length === 0,
    error:
      (!usesAuthoritativeTarget ? tradingDaysError : undefined) ||
      initialError ||
      (shouldLoadFallback ? fallbackError : undefined) ||
      klineSubResult.error ||
      tickSubResult.error ||
      (callAuctionDataIsPrimary ? auctionTicksError : undefined),
  };
}

export const __intradayKLineTestUtils = {
  buildCallAuctionTickBars,
  buildTickMinuteBar,
  getMinuteMs,
  getFallbackTradingDateRange,
  getIntradayQueryDateRange,
  isKLineBaseReadyForTick,
  mergeKLines,
  mergeTicks,
  resolveTickDeltaMetric,
  resolveTickMinuteMetric,
};
