import { useCallback, useMemo } from 'react';
import { useMutation, useQuery } from 'urql';

import { gql } from '@/generated/gql';

export const StockDisclosureSummaryQuery = gql(`
  query StockDisclosureSummary($stockCode: String!, $limit: Int = 20) {
    stockDisclosureSummary(stockCode: $stockCode, limit: $limit) {
      stockCode
      sourceStatus
      sourceMessage
      latestAnnouncementDate
      latestRepurchaseDate
      latestSync {
        success
        stockCode
        sourceStatus
        message
        startedAt
        finishedAt
        announcementCount
        repurchaseCount
        errorMessage
      }
      announcements {
        id
        stockCode
        stockName
        title
        announcementType
        announceDate
        source
        sourceUrl
        pdfUrl
        isRepurchaseRelated
        fetchedAt
      }
      repurchaseEvents {
        id
        stockCode
        stockName
        source
        sourceUrl
        latestAnnounceDate
        progressStatus
        priceFloor
        priceCeiling
        plannedQuantityLower
        plannedQuantityAverage
        plannedQuantityUpper
        plannedAmountLower
        plannedAmountUpper
        repurchasedQuantity
        repurchasedAmount
        repurchasedRatio
        fetchedAt
      }
    }
  }
`);

export const RefreshStockDisclosuresMutation = gql(`
  mutation RefreshStockDisclosures($stockCode: String!, $force: Boolean = false) {
    refreshStockDisclosures(stockCode: $stockCode, force: $force) {
      success
      stockCode
      sourceStatus
      message
      startedAt
      finishedAt
      announcementCount
      repurchaseCount
      errorMessage
    }
  }
`);

function normalizeStockCode(value?: string | null) {
  return (value || '').trim().toUpperCase();
}

export function useStockDisclosures(stockCode?: string | null, limit = 20) {
  const normalizedStockCode = useMemo(
    () => normalizeStockCode(stockCode),
    [stockCode]
  );

  const [queryResult, reexecuteQuery] = useQuery({
    query: StockDisclosureSummaryQuery,
    variables: {
      stockCode: normalizedStockCode,
      limit,
    },
    pause: !normalizedStockCode,
  });

  const [refreshResult, executeRefresh] = useMutation(
    RefreshStockDisclosuresMutation
  );

  const refresh = useCallback(async () => {
    if (!normalizedStockCode) return null;
    const result = await executeRefresh({
      stockCode: normalizedStockCode,
      force: true,
    });
    reexecuteQuery({ requestPolicy: 'network-only' });
    return result;
  }, [executeRefresh, normalizedStockCode, reexecuteQuery]);

  return useMemo(
    () => ({
      summary: queryResult.data?.stockDisclosureSummary,
      isLoading: queryResult.fetching,
      error: queryResult.error,
      refresh,
      refreshStatus: refreshResult.data?.refreshStockDisclosures,
      isRefreshing: refreshResult.fetching,
      refreshError: refreshResult.error,
    }),
    [
      queryResult.data?.stockDisclosureSummary,
      queryResult.error,
      queryResult.fetching,
      refresh,
      refreshResult.data?.refreshStockDisclosures,
      refreshResult.error,
      refreshResult.fetching,
    ]
  );
}
