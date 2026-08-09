import { useMemo } from 'react';

import { parseMarketDate } from '@/components/trading-chart/utils/time-utils';
import { cn } from '@/utils/cn';

interface IntradayBarLike {
  time?: string | number | Date | null;
  lastPrice?: unknown;
  currentPrice?: unknown;
  open?: unknown;
  high?: unknown;
  low?: unknown;
  close?: unknown;
  preClose?: unknown;
  volume?: unknown;
  amount?: unknown;
}

interface NormalizedIntradayBar {
  time: Date;
  price: number | null;
  preClose: number | null;
  volume: number | null;
  amount: number | null;
}

interface IntradayInfoBarProps {
  bars: IntradayBarLike[];
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

const formatPrice = (value: number | null) =>
  value === null ? '--' : value.toFixed(value >= 10 ? 2 : 3);

export function IntradayInfoBar({ bars }: IntradayInfoBarProps) {
  const info = useMemo(() => {
    const safeBars = Array.isArray(bars) ? bars : [];
    const normalized = safeBars
      .map(item => ({
        time: parseMarketDate(item?.time),
        price: toValidPrice(
          item?.close,
          item?.lastPrice,
          item?.currentPrice,
          item?.open,
          item?.high,
          item?.low
        ),
        preClose: toValidPrice(item?.preClose),
        volume: toFiniteNumber(item?.volume),
        amount: toFiniteNumber(item?.amount),
      }))
      .filter(
        (item): item is NormalizedIntradayBar =>
          item.time !== null && Number.isFinite(item.time.getTime())
      );

    const validBars = normalized.filter(item => item.price !== null);
    const latestPrice =
      validBars.length > 0 ? validBars[validBars.length - 1].price : null;
    const preClose =
      normalized.find(item => item.preClose !== null)?.preClose || null;
    const simpleAverage =
      validBars.length > 0
        ? validBars.reduce((sum, item) => sum + (item.price || 0), 0) /
          validBars.length
        : null;

    const cumulativeVolume = normalized.reduce(
      (sum, item) => sum + Math.max(0, item.volume || 0),
      0
    );
    const cumulativeAmount = normalized.reduce(
      (sum, item) => sum + Math.max(0, item.amount || 0),
      0
    );
    const rawWeightedAverage =
      cumulativeAmount > 0 && cumulativeVolume > 0
        ? cumulativeAmount / cumulativeVolume
        : null;
    const weightedAverage =
      rawWeightedAverage !== null &&
      latestPrice !== null &&
      rawWeightedAverage > latestPrice * 20
        ? rawWeightedAverage / 100
        : rawWeightedAverage;

    const change =
      latestPrice !== null && preClose !== null ? latestPrice - preClose : null;
    const changePercent =
      change !== null && preClose !== null && preClose > 0
        ? (change / preClose) * 100
        : null;

    return {
      average: weightedAverage ?? simpleAverage,
      change,
      changePercent,
      latestPrice,
    };
  }, [bars]);

  const trendClass =
    info.change === null
      ? 'text-slate-400'
      : info.change >= 0
        ? 'text-red-400'
        : 'text-emerald-400';

  return (
    <div className="pointer-events-none absolute left-4 right-20 top-12 z-10 flex min-h-7 flex-wrap items-center gap-x-4 gap-y-1 text-[11px] font-semibold text-slate-300">
      <span className="text-amber-300">均价: {formatPrice(info.average)}</span>
      <span className={cn('tabular-nums', trendClass)}>
        最新: {formatPrice(info.latestPrice)}
      </span>
      <span className={cn('tabular-nums', trendClass)}>
        {info.change === null ? '--' : info.change.toFixed(2)}
      </span>
      <span className={cn('tabular-nums', trendClass)}>
        {info.changePercent === null
          ? '--'
          : `${info.changePercent.toFixed(2)}%`}
      </span>
    </div>
  );
}
