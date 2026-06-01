import { useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

const GET_TRADING_CALENDAR = gql(`
  query GetTradingCalendar($startDate: Date, $endDate: Date, $market: String!) {
    tradingCalendar(startDate: $startDate, endDate: $endDate, market: $market)
  }
`);

export function useTradingDays(market: string = 'SH', daysBefore: number = 30) {
  // Calculate date range: [Today - daysBefore, Today + 1]
  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    end.setDate(end.getDate() + 1); // Future buffer

    const start = new Date();
    start.setDate(start.getDate() - daysBefore);

    return {
      startDate: start.toISOString().split('T')[0],
      endDate: end.toISOString().split('T')[0],
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
