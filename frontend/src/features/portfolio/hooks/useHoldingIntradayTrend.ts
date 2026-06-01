import { useMemo } from 'react';

import { useTicks } from '@/features/trading/hooks/useTrading';
import { useTradingDays } from '@/hooks/useTradingDays';

import type { Position } from '../types';
import {
  COMPRESSED_TRADING_RANGE,
  getIntradayQueryRange,
  normalizeTicksToIntradayTrend,
  resolveIntradayAnchorDate,
  type IntradayTrendTick,
} from '../utils/intradayTrend';

export function useHoldingIntradayTrend(holding: Position) {
  const { tradingDays, loading: tradingDaysLoading } = useTradingDays('SH', 30);
  const safeTradingDays = useMemo(
    () => (Array.isArray(tradingDays) ? tradingDays : []),
    [tradingDays]
  );

  const anchor = useMemo(
    () => resolveIntradayAnchorDate(safeTradingDays),
    [safeTradingDays]
  );

  const queryRange = useMemo(
    () => (anchor ? getIntradayQueryRange(anchor.date) : null),
    [anchor]
  );

  const { data: historicalTicks, loading: ticksLoading } = useTicks(
    anchor ? holding.stockCode : '',
    queryRange?.startTime,
    queryRange?.endTime,
    { limit: 6000, order: 'asc' }
  );

  const data = useMemo(() => {
    if (!anchor) return [];

    const ticks: IntradayTrendTick[] = Array.isArray(historicalTicks)
      ? [...historicalTicks]
      : [];
    if (holding.quoteTime && typeof holding.lastPrice === 'number') {
      ticks.push({
        time: holding.quoteTime,
        lastPrice: holding.lastPrice,
      });
    }

    return normalizeTicksToIntradayTrend(ticks, anchor.date);
  }, [anchor, historicalTicks, holding.lastPrice, holding.quoteTime]);

  return {
    data,
    visibleRange: COMPRESSED_TRADING_RANGE,
    loading: tradingDaysLoading || ticksLoading,
    anchorDate: anchor?.date,
  };
}
