import { useMemo } from 'react';
import { useQuery } from 'urql';

import {
  ResearchRunDocument,
  ResearchRunsDocument,
} from '@/generated/gql/graphql';

import { parseResearchResult } from '../model';

export function useResearchRuns(status: string | null) {
  const [result, refresh] = useQuery({
    query: ResearchRunsDocument,
    variables: {
      limit: 100,
      offset: 0,
      status: status || null,
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
