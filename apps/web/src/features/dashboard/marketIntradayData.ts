import {
  getShanghaiDateKey,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import type { IntradayTrendBar } from '@/hooks/useIntradayTrendData';

export const selectShanghaiMarketBarsForTradingDate = (
  bars: IntradayTrendBar[],
  targetTradingDate: string | null | undefined
) => {
  if (!targetTradingDate) return [];

  return bars.filter(bar => {
    const date = parseMarketDate(bar.time);
    return date && getShanghaiDateKey(date) === targetTradingDate;
  });
};
