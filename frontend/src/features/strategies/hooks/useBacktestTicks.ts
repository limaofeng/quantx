import { useCallback, useEffect, useMemo, useState } from 'react';
import { gql, useQuery } from 'urql';

const BacktestTicksPageQuery = gql`
  query StrategyBacktestTicksPage(
    $stockCode: String!
    $startTime: DateTime
    $endTime: DateTime
    $limit: Int
    $order: String!
  ) {
    ticks(
      stockCode: $stockCode
      startTime: $startTime
      endTime: $endTime
      limit: $limit
      order: $order
    ) {
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
`;

function toTime(value?: string | null) {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? null : time;
}

interface UseBacktestTicksOptions {
  stockCode?: string;
  startTime?: string | null;
  endTime?: string | null;
  enabled?: boolean;
  limit?: number;
}

export function useBacktestTicks({
  stockCode,
  startTime,
  endTime,
  enabled = true,
  limit = 1000,
}: UseBacktestTicksOptions) {
  const [cursor, setCursor] = useState<string | null>(endTime ?? null);
  const [allData, setAllData] = useState<any[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [isFetchingMore, setIsFetchingMore] = useState(false);

  const startMs = toTime(startTime);
  const endMs = toTime(endTime);

  const [result] = useQuery({
    query: BacktestTicksPageQuery,
    variables: {
      stockCode: stockCode || '',
      startTime,
      endTime: cursor ?? endTime,
      limit,
      order: 'desc',
    },
    pause: !enabled || !stockCode || !startTime || !endTime,
    requestPolicy: 'cache-and-network',
  });

  useEffect(() => {
    setCursor(endTime ?? null);
    setAllData([]);
    setHasMore(true);
    setIsFetchingMore(false);
  }, [stockCode, startTime, endTime, limit]);

  useEffect(() => {
    if (!enabled || result.fetching) return;

    const pageData = ((result.data as any)?.ticks || []) as any[];
    if (pageData.length === 0) {
      if (cursor !== endTime) setHasMore(false);
      setIsFetchingMore(false);
      return;
    }

    const sortedPageData = [...pageData]
      .filter(item => {
        const itemTime = new Date(item.time).getTime();
        if (Number.isNaN(itemTime)) return false;
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

    const oldestLoadedMs =
      sortedPageData.length > 0
        ? new Date(sortedPageData[0].time).getTime()
        : null;

    if (
      startMs !== null &&
      oldestLoadedMs !== null &&
      oldestLoadedMs <= startMs
    ) {
      setHasMore(false);
    } else {
      setHasMore(pageData.length >= limit);
    }

    setIsFetchingMore(false);
  }, [
    cursor,
    enabled,
    endTime,
    endMs,
    limit,
    result.data,
    result.fetching,
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
