import type { AreaData, Time, WhitespaceData } from 'lightweight-charts';
import { useMemo } from 'react';

import type { TradingRange } from '@/components/trading-chart/types';
import {
  getShanghaiDateKey,
  getTradingRange,
  isCallAuctionTimestamp,
  parseMarketDate,
  toChartTimestamp,
} from '@/components/trading-chart/utils/time-utils';

import { useIntradayKLines } from './useIntradayKLines';

const MAX_INTRADAY_BAR_GAP_SECONDS = 2 * 60;

export type IntradayTrendPoint = AreaData | WhitespaceData;

export interface IntradayTrendBar {
  time?: string | number | Date | null;
  close?: unknown;
  lastPrice?: unknown;
  currentPrice?: unknown;
  open?: unknown;
  high?: unknown;
  low?: unknown;
  sourceTime?: string | number | Date | null;
  volume?: unknown;
  amount?: unknown;
  preClose?: unknown;
}

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

const getShanghaiMinutes = (time: number) => {
  const date = new Date(time * 1000);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);
  const hour = Number(
    parts.find(part => part.type === 'hour')?.value.replace('24', '0') || 0
  );
  const minute = Number(parts.find(part => part.type === 'minute')?.value || 0);
  return hour * 60 + minute;
};

const isLunchBreakGap = (previousTime: number, currentTime: number) => {
  const previousMinutes = getShanghaiMinutes(previousTime);
  const currentMinutes = getShanghaiMinutes(currentTime);
  return previousMinutes <= 11 * 60 + 30 && currentMinutes >= 13 * 60;
};

const isCallAuctionChartTime = (time: number) =>
  isCallAuctionTimestamp(new Date(time * 1000));

const isCallAuctionInternalGap = (previousTime: number, currentTime: number) =>
  isCallAuctionChartTime(previousTime) && isCallAuctionChartTime(currentTime);

const getAnchorDate = (bars: IntradayTrendBar[]): string => {
  let latestTime = Number.NEGATIVE_INFINITY;
  let latestValue: string | null = null;

  bars.forEach(bar => {
    const parsed = parseMarketDate(bar?.time);
    const time = parsed?.getTime() ?? NaN;
    if (Number.isFinite(time) && time > latestTime) {
      latestTime = time;
      latestValue =
        bar?.time instanceof Date ? bar.time.toISOString() : String(bar?.time);
    }
  });

  return latestValue || new Date().toISOString();
};

export function buildIntradayTrendSeries(
  rawIntradayBars: IntradayTrendBar[] | null | undefined
): {
  lineData: IntradayTrendPoint[];
  visibleRange: TradingRange;
} {
  const safeIntradayBars = Array.isArray(rawIntradayBars)
    ? rawIntradayBars
    : [];
  const refDate = getAnchorDate(safeIntradayBars);
  const hasCallAuctionBars = safeIntradayBars.some(item =>
    isCallAuctionTimestamp(item?.sourceTime ?? item?.time)
  );
  const visibleRange = getTradingRange(
    refDate,
    hasCallAuctionBars ? { includeCallAuction: true } : {}
  );

  const intradayBars = safeIntradayBars
    .map(item => {
      const time = toChartTimestamp(item?.time);
      const close = toValidPrice(
        item?.close,
        item?.lastPrice,
        item?.currentPrice,
        item?.open,
        item?.high,
        item?.low
      );

      return { time, close };
    })
    .filter(item => {
      const time = item.time as number | null;
      return (
        item.close !== null &&
        time !== null &&
        Number.isFinite(time) &&
        time >= (visibleRange.from as number) &&
        time <= (visibleRange.to as number)
      );
    })
    .sort((a, b) => (a.time as number) - (b.time as number));

  const lineData: IntradayTrendPoint[] = [];

  intradayBars.forEach((item, idx) => {
    const previousBar = idx > 0 ? intradayBars[idx - 1] : null;
    const previousTime = previousBar?.time as number | undefined;
    const currentTime = item.time as number;
    const hasMissingMinutes =
      previousTime !== undefined &&
      currentTime - previousTime > MAX_INTRADAY_BAR_GAP_SECONDS &&
      !isCallAuctionInternalGap(previousTime, currentTime) &&
      !isLunchBreakGap(previousTime, currentTime);

    if (hasMissingMinutes) {
      lineData.push({
        time: Math.min(previousTime + 60, currentTime - 60) as Time,
      });
    }

    lineData.push({
      time: item.time as Time,
      value: item.close as number,
    });
  });

  if (lineData.length === 1 && 'value' in lineData[0]) {
    const firstPoint = lineData[0];
    const previousTime = Math.max(
      visibleRange.from as number,
      (firstPoint.time as number) - 60
    ) as Time;

    lineData.unshift({
      time: previousTime,
      value: firstPoint.value,
    });
  }

  return {
    lineData,
    visibleRange,
  };
}

export function useIntradayTrendData(
  stockCode: string,
  mode: '1d' | '5d' = '1d'
) {
  const { data: bars, loading, error } = useIntradayKLines(stockCode, mode);
  const { lineData, visibleRange } = useMemo(
    () => buildIntradayTrendSeries(bars),
    [bars]
  );

  const anchorDate = useMemo(() => {
    const parsed = parseMarketDate(getAnchorDate(bars));
    return parsed ? getShanghaiDateKey(parsed) : undefined;
  }, [bars]);

  return {
    bars,
    lineData,
    visibleRange,
    loading,
    error,
    anchorDate,
  };
}

export const __intradayTrendDataTestUtils = {
  buildIntradayTrendSeries,
};
