import { useCallback, useEffect, useMemo, useState } from 'react';
import { gql, useQuery } from 'urql';

import {
  KLinePeriod,
  type StrategyBacktestKLinesPageQuery,
  type StrategyBacktestKLinesPageQueryVariables,
} from '@/generated/gql/graphql';

const BacktestKLinesPageQuery = gql`
  query StrategyBacktestKLinesPage(
    $stockCode: String!
    $period: KLinePeriod!
    $startTime: DateTime
    $endTime: DateTime
    $limit: Int
    $order: String!
  ) {
    klines(
      stockCode: $stockCode
      period: $period
      startTime: $startTime
      endTime: $endTime
      limit: $limit
      order: $order
    ) {
      stockCode
      period
      time
      open
      high
      low
      close
      volume
      amount
    }
  }
`;

function toTime(value?: string | null) {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? null : time;
}

function toDateKey(value?: string | null) {
  if (!value) return null;
  const match = value.match(/^(\d{4}-\d{2}-\d{2})/);
  if (match) return match[1];

  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return null;
  const year = time.getFullYear();
  const month = String(time.getMonth() + 1).padStart(2, '0');
  const day = String(time.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function normalizePeriod(period: string): KLinePeriod {
  return Object.values(KLinePeriod).includes(period as KLinePeriod)
    ? (period as KLinePeriod)
    : KLinePeriod.Day_1;
}

interface UseBacktestKLinesOptions {
  stockCode?: string;
  period: string;
  startTime?: string | null;
  endTime?: string | null;
  boundaryStartTime?: string | null;
  boundaryEndTime?: string | null;
  boundaryMode?: 'date' | 'timestamp';
  enabled?: boolean;
  limit?: number;
}

export function useBacktestKLines({
  stockCode,
  period,
  startTime,
  endTime,
  boundaryStartTime,
  boundaryEndTime,
  boundaryMode = 'timestamp',
  enabled = true,
  limit = 500,
}: UseBacktestKLinesOptions) {
  const [cursor, setCursor] = useState<string | null>(endTime ?? null);
  const [allData, setAllData] = useState<
    StrategyBacktestKLinesPageQuery['klines']
  >([]);
  const [hasMore, setHasMore] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);

  const effectiveStartTime = boundaryStartTime ?? startTime;
  const effectiveEndTime = boundaryEndTime ?? endTime;
  const startMs = toTime(effectiveStartTime);
  const endMs = toTime(effectiveEndTime);
  const startDate = toDateKey(effectiveStartTime);
  const endDate = toDateKey(effectiveEndTime);

  const [result] = useQuery<
    StrategyBacktestKLinesPageQuery,
    StrategyBacktestKLinesPageQueryVariables
  >({
    query: BacktestKLinesPageQuery,
    variables: {
      stockCode: stockCode || '',
      period: normalizePeriod(period),
      startTime,
      endTime: cursor ?? endTime,
      limit,
      order: 'desc',
    },
    pause: !enabled || !stockCode || !period || !startTime || !endTime,
    requestPolicy: 'cache-and-network',
  });

  useEffect(() => {
    setCursor(endTime ?? null);
    setAllData([]);
    setHasMore(true);
    setIsFetchingMore(false);
  }, [
    boundaryEndTime,
    boundaryMode,
    boundaryStartTime,
    endTime,
    limit,
    period,
    startTime,
    stockCode,
  ]);

  useEffect(() => {
    if (!enabled || result.fetching) return;

    const pageData = result.data?.klines || [];
    if (pageData.length === 0) {
      if (cursor !== endTime) setHasMore(false);
      setIsFetchingMore(false);
      return;
    }

    const sortedPageData = [...pageData]
      .filter(item => {
        if (boundaryMode === 'date') {
          const itemDate = toDateKey(item.time);
          if (!itemDate) return false;
          if (startDate && itemDate < startDate) return false;
          if (endDate && itemDate > endDate) return false;
          return true;
        }

        const itemTime = toTime(item.time);
        if (itemTime === null) return false;
        if (startMs !== null && itemTime < startMs) return false;
        if (endMs !== null && itemTime > endMs) return false;
        return true;
      })
      .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

    if (sortedPageData.length === 0) {
      setHasMore(false);
      setIsFetchingMore(false);
      return;
    }

    setAllData(prev => {
      if (cursor === endTime) return sortedPageData;
      if (prev.length === 0) return sortedPageData;

      const prevStart = new Date(prev[0].time).getTime();
      const distinctNew = sortedPageData.filter(
        item => new Date(item.time).getTime() < prevStart
      );
      return [...distinctNew, ...prev];
    });

    if (boundaryMode === 'date') {
      const oldestDate = toDateKey(sortedPageData[0].time);
      setHasMore(
        !!startDate && oldestDate
          ? oldestDate > startDate && pageData.length >= limit
          : false
      );
    } else {
      const oldestLoadedMs = toTime(sortedPageData[0].time);
      if (
        startMs !== null &&
        oldestLoadedMs !== null &&
        oldestLoadedMs <= startMs
      ) {
        setHasMore(false);
      } else {
        setHasMore(pageData.length >= limit);
      }
    }

    setIsFetchingMore(false);
  }, [
    boundaryMode,
    cursor,
    enabled,
    endDate,
    endMs,
    endTime,
    limit,
    result.data,
    result.fetching,
    startDate,
    startMs,
  ]);

  const loadMore = useCallback(() => {
    if (!hasMore || isFetchingMore || result.fetching || allData.length === 0) {
      return;
    }
    setIsFetchingMore(true);
    setCursor(allData[0].time);
  }, [allData, hasMore, isFetchingMore, result.fetching]);

  return useMemo(
    () => ({
      data: allData,
      loading: result.fetching && allData.length === 0,
      fetchingMore: isFetchingMore,
      hasMore,
      loadMore,
      error: result.error,
    }),
    [allData, hasMore, isFetchingMore, loadMore, result.error, result.fetching]
  );
}
