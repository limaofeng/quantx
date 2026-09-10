import { useCallback, useEffect } from 'react';
import { useQuery } from 'urql';

import { StockSelectionTrainerStatusDocument } from '@/generated/trainer/graphql';

export function useStockSelectionTrainerStatus() {
  const [result, execute] = useQuery({
    query: StockSelectionTrainerStatusDocument,
    requestPolicy: 'cache-and-network',
  });
  const refresh = useCallback(
    () => execute({ requestPolicy: 'network-only' }),
    [execute]
  );
  useEffect(() => {
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  return {
    data: result.data?.stockSelectionTrainerStatus ?? null,
    fetching: result.fetching,
    error: result.error,
    refresh,
  };
}
