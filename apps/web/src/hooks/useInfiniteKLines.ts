import { useEffect, useState, useCallback } from 'react';

import { parseMarketDate } from '@/components/trading-chart/utils/time-utils';
import { useKLinesPage } from '@/features/trading/hooks/useTrading';

export type InfiniteKLine = ReturnType<typeof useKLinesPage>['data'][number];

interface InfiniteKLineOptions {
  enabled?: boolean;
  initialCursor?: string | null;
  rangeStart?: string | null;
  rangeEnd?: string | null;
  limit?: number;
}

function toTime(value?: string | null) {
  if (!value) return null;
  const time = parseMarketDate(value)?.getTime() ?? NaN;
  return Number.isNaN(time) ? null : time;
}

export function useInfiniteKLines(
  stockCode: string,
  period: string,
  enabledOrOptions: boolean | InfiniteKLineOptions = true
) {
  const options =
    typeof enabledOrOptions === 'boolean'
      ? { enabled: enabledOrOptions }
      : enabledOrOptions;
  const enabled = options.enabled ?? true;
  const limit = options.limit ?? 200;
  const rangeStartMs = toTime(options.rangeStart);
  const rangeEndMs = toTime(options.rangeEnd);
  const initialCursor = options.initialCursor ?? options.rangeEnd ?? null;

  const [allData, setAllData] = useState<InfiniteKLine[]>([]);
  // We use a timestamp cursor for pagination
  const [cursor, setCursor] = useState<string | null>(initialCursor);
  const [hasMore, setHasMore] = useState(true);

  // Track if we are currently loading to prevent duplicate fetches
  const [isFetchingMore, setIsFetchingMore] = useState(false);

  // Fetch K-Lines using the cursor
  // When cursor is null, it fetches the latest data (PREV direction implied)
  // When cursor is set, it fetches data strictly before that cursor
  const {
    data: pageData,
    pageInfo,
    loading: queryLoading,
  } = useKLinesPage(enabled ? stockCode : '', period, limit, cursor);

  // Reset when stock/period changes
  useEffect(() => {
    setAllData([]);
    setCursor(initialCursor);
    setHasMore(true);
    setIsFetchingMore(false);
  }, [stockCode, period, initialCursor, options.rangeStart, limit]);

  // Handle data updates
  useEffect(() => {
    // Skip if disabled or still loading the query
    if (!enabled || queryLoading) return;

    // Logic for handling data...

    // If no data returned, just stop (unless it's empty result which ends pagination)
    if (!pageData || pageData.length === 0) {
      if (cursor !== null) {
        // If we were fetching more and got nothing, no more history
        setHasMore(false);
      }
      return;
    }

    const sortedPageData = [...pageData]
      .filter(item => {
        const itemTime = toTime(item.time);
        if (itemTime === null) return false;
        if (rangeStartMs !== null && itemTime < rangeStartMs) return false;
        if (rangeEndMs !== null && itemTime > rangeEndMs) return false;
        return true;
      })
      .sort((a, b) => (toTime(a.time) ?? 0) - (toTime(b.time) ?? 0));

    if (sortedPageData.length === 0) {
      setHasMore(false);
      setIsFetchingMore(false);
      return;
    }

    setAllData(prev => {
      // If cursor is null, it's the initial load (latest data) -> Replace
      if (cursor === null) {
        return sortedPageData;
      }

      // If cursor is set, it's a history load -> Prepend
      // Deduplication: prevent appending charts that overlap
      if (prev.length > 0) {
        const firstExisting = prev[0];
        const prevStart = toTime(firstExisting.time);

        // Keep only new items strictly older than the oldest existing item
        const distinctNew = sortedPageData.filter(
          item =>
            prevStart !== null &&
            (toTime(item.time) ?? Number.MAX_SAFE_INTEGER) < prevStart
        );

        return [...distinctNew, ...prev];
      }

      return [...sortedPageData, ...prev];
    });

    // Update pagination status
    const oldestLoadedMs =
      sortedPageData.length > 0 ? toTime(sortedPageData[0].time) : null;

    if (
      rangeStartMs !== null &&
      oldestLoadedMs !== null &&
      oldestLoadedMs <= rangeStartMs
    ) {
      setHasMore(false);
    } else if (pageInfo) {
      setHasMore(pageInfo.hasNextPage);
    }

    setIsFetchingMore(false);
  }, [
    pageData,
    queryLoading,
    enabled,
    pageInfo,
    cursor,
    rangeStartMs,
    rangeEndMs,
  ]);

  const loadMore = useCallback(() => {
    if (!hasMore || isFetchingMore || queryLoading) return;

    if (pageInfo && pageInfo.endCursor) {
      setIsFetchingMore(true);
      setCursor(pageInfo.endCursor);
    }
  }, [hasMore, isFetchingMore, queryLoading, pageInfo]);

  return {
    data: allData,
    loading: queryLoading && allData.length === 0, // Only show global loading if we have NO data at all
    loadMore,
    hasMore,
  };
}
