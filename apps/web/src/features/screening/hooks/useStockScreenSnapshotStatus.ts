import { useCallback } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

export const STOCK_SCREEN_SNAPSHOT_STATUS_QUERY = gql(`
  query StockScreenSnapshotStatus($lookbackDays: Int!) {
    stockScreenSnapshotStatus(lookbackDays: $lookbackDays) {
      latestSnapshotDate
      expectedSnapshotDate
      missingSnapshotDates
      isComplete
      latestRunStatus
      latestCalculatedAt
      warnings
    }
  }
`);

export function useStockScreenSnapshotStatus({
  lookbackDays = 30,
  pause = false,
}: {
  lookbackDays?: number;
  pause?: boolean;
} = {}) {
  const [result, reexecute] = useQuery({
    query: STOCK_SCREEN_SNAPSHOT_STATUS_QUERY,
    variables: { lookbackDays },
    pause,
    requestPolicy: 'cache-and-network',
  });
  const refresh = useCallback(
    () => reexecute({ requestPolicy: 'network-only' }),
    [reexecute]
  );

  return {
    status: result.data?.stockScreenSnapshotStatus,
    fetching: result.fetching,
    error: result.error,
    refresh,
  };
}
