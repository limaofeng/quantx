import { useMemo } from 'react';
import { useQuery } from 'urql';

import {
  addShanghaiDays,
  getShanghaiDateKey,
} from '@/components/trading-chart/utils/time-utils';
import { gql } from '@/generated/gql';

const GET_TRADING_CALENDAR = gql(`
  query GetTradingCalendar($startDate: Date, $endDate: Date, $market: String!) {
    tradingCalendar(startDate: $startDate, endDate: $endDate, market: $market)
  }
`);

export function useTradingDays(market: string = 'SH', daysBefore: number = 30) {
  // Calculate date range: [Today - daysBefore, Today + 1]
  const { startDate, endDate } = useMemo(() => {
    const now = new Date();
    const end = addShanghaiDays(now, 1); // Future buffer
    const start = addShanghaiDays(now, -daysBefore);

    return {
      startDate: getShanghaiDateKey(start),
      endDate: getShanghaiDateKey(end),
    };
  }, [daysBefore]);

  const [{ data, fetching, error }] = useQuery({
    query: GET_TRADING_CALENDAR,
    variables: { startDate, endDate, market },
    requestPolicy: 'cache-and-network',
  });

  const tradingDays: string[] = useMemo(() => {
    return (data?.tradingCalendar as string[]) || [];
  }, [data]);

  return {
    tradingDays,
    loading: fetching,
    error,
  };
}
