import { useEffect, useMemo } from 'react';
import { useQuery } from 'urql';

import {
  ResearchLifecycleRunStatus,
  ResearchLifecycleRunsDocument,
  ResearchRunDocument,
  ResearchRunsDocument,
  type ResearchLifecycleRunFilter,
} from '@/generated/gql/graphql';

import { parseResearchResult } from '../model';

export function useResearchRuns(
  status: string | null,
  studyId: string | null = null
) {
  const [result, refresh] = useQuery({
    query: ResearchRunsDocument,
    variables: {
      limit: 100,
      offset: 0,
      status: status || null,
      studyId,
    },
    requestPolicy: 'cache-and-network',
  });
  return {
    error: result.error,
    fetching: result.fetching,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
    runs: result.data?.researchRuns.items || [],
    total: result.data?.researchRuns.total || 0,
  };
}

export function useResearchLifecycleRuns(
  filter: ResearchLifecycleRunFilter | null = null,
  limit = 20,
  offset = 0
) {
  const [result, refresh] = useQuery({
    query: ResearchLifecycleRunsDocument,
    variables: {
      filter,
      limit,
      offset,
    },
    requestPolicy: 'cache-and-network',
  });
  const runs = useMemo(
    () => result.data?.researchLifecycleRuns.items ?? [],
    [result.data?.researchLifecycleRuns.items]
  );
  const hasActiveRuns = runs.some(
    run =>
      run.status === ResearchLifecycleRunStatus.Queued ||
      run.status === ResearchLifecycleRunStatus.Running
  );

  useEffect(() => {
    if (!hasActiveRuns) return undefined;
    const timer = window.setInterval(() => {
      void refresh({ requestPolicy: 'network-only' });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveRuns, refresh]);

  return {
    error: result.error,
    fetching: result.fetching,
    items: runs,
    limit: result.data?.researchLifecycleRuns.limit ?? limit,
    offset: result.data?.researchLifecycleRuns.offset ?? offset,
    polling: hasActiveRuns,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
    runs,
    total: result.data?.researchLifecycleRuns.total ?? 0,
  };
}

export function useResearchRun(key: string) {
  const [result, refresh] = useQuery({
    query: ResearchRunDocument,
    variables: { key },
    pause: !key,
    requestPolicy: 'cache-and-network',
  });
  const detail = result.data?.researchRun || null;
  const parsed = useMemo(
    () =>
      detail
        ? parseResearchResult({
            analysisSampleCount: detail.analysisSampleCount,
            comparison: detail.comparison,
            comparisonSensitivity: detail.comparisonSensitivity,
            dataQuality: detail.dataQuality,
            eventCurve: detail.eventCurve,
            interactionHeatmap: detail.interactionHeatmap,
            regressions: detail.regressions,
            robustness: detail.robustness,
          })
        : null,
    [detail]
  );

  return {
    detail,
    error: result.error,
    fetching: result.fetching,
    parsed,
    refresh: () => refresh({ requestPolicy: 'network-only' }),
  };
}
