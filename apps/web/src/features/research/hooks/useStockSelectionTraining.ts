import { useCallback, useEffect, useMemo } from 'react';
import { useQuery } from 'urql';

import {
  StockSelectionDatasetVersionsDocument,
  StockSelectionTrainingCapabilitiesDocument,
  StockSelectionTrainingComparisonDocument,
  StockSelectionTrainingRunDocument,
  StockSelectionTrainingRunsDocument,
  type StockSelectionTrainingRun,
  type StockSelectionTrainingRunKind,
  type StockSelectionTrainingRunStatus,
} from '@/generated/gql/graphql';

function activeRun(run: StockSelectionTrainingRun) {
  return run.status === 'QUEUED' || run.status === 'RUNNING';
}

export function useStockSelectionTrainingCapabilities() {
  const [result, refresh] = useQuery({
    query: StockSelectionTrainingCapabilitiesDocument,
    requestPolicy: 'cache-and-network',
  });
  const refreshCapabilities = useCallback(
    () => refresh({ requestPolicy: 'network-only' }),
    [refresh]
  );
  return {
    data: result.data?.stockSelectionTrainingCapabilities ?? null,
    error: result.error,
    fetching: result.fetching,
    refresh: refreshCapabilities,
  };
}

export function useStockSelectionDatasetVersions() {
  const [result, refresh] = useQuery({
    query: StockSelectionDatasetVersionsDocument,
    variables: { limit: 100, offset: 0 },
    requestPolicy: 'cache-and-network',
  });
  return {
    data: result.data?.stockSelectionDatasetVersions ?? [],
    error: result.error,
    fetching: result.fetching,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
  };
}

export function useStockSelectionTrainingRuns(
  status?: StockSelectionTrainingRunStatus,
  runKind?: StockSelectionTrainingRunKind
) {
  const [result, refresh] = useQuery({
    query: StockSelectionTrainingRunsDocument,
    variables: {
      status: status ?? null,
      runKind: runKind ?? null,
      limit: 100,
      offset: 0,
    },
    requestPolicy: 'cache-and-network',
  });
  const runs = useMemo(
    () => result.data?.stockSelectionTrainingRuns.items ?? [],
    [result.data?.stockSelectionTrainingRuns.items]
  );
  const hasActiveRuns = runs.some(activeRun);

  useEffect(() => {
    if (!hasActiveRuns) return undefined;
    const timer = window.setInterval(() => {
      void refresh({ requestPolicy: 'network-only' });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveRuns, refresh]);

  return {
    runs,
    total: result.data?.stockSelectionTrainingRuns.total ?? 0,
    error: result.error,
    fetching: result.fetching,
    polling: hasActiveRuns,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
  };
}

export function useStockSelectionTrainingRun(runId: string | null) {
  const [result, refresh] = useQuery({
    query: StockSelectionTrainingRunDocument,
    variables: { runId: runId ?? '' },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });
  const run = result.data?.stockSelectionTrainingRun ?? null;
  const hasActiveRun = run ? activeRun(run) : false;

  useEffect(() => {
    if (!hasActiveRun || !runId) return undefined;
    const timer = window.setInterval(() => {
      void refresh({ requestPolicy: 'network-only' });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveRun, refresh, runId]);

  return {
    run,
    error: result.error,
    fetching: result.fetching,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
  };
}

export function useStockSelectionTrainingComparison(runIds: string[]) {
  const ids = useMemo(
    () => Array.from(new Set(runIds.filter(Boolean))).slice(0, 5),
    [runIds]
  );
  const [result, refresh] = useQuery({
    query: StockSelectionTrainingComparisonDocument,
    variables: { runIds: ids },
    pause: ids.length < 2,
    requestPolicy: 'cache-and-network',
  });
  return {
    comparison: result.data?.stockSelectionTrainingComparison ?? null,
    error: result.error,
    fetching: result.fetching,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
  };
}
