import {
  getShanghaiDateKey,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import type { IntradayTrendBar } from '@/hooks/useIntradayTrendData';

export const selectCurrentShanghaiMarketBars = (
  bars: IntradayTrendBar[],
  now: Date = new Date()
) => {
  const todayKey = getShanghaiDateKey(now);
  return bars.filter(bar => {
    const date = parseMarketDate(bar.time);
    return date && getShanghaiDateKey(date) === todayKey;
  });
};
