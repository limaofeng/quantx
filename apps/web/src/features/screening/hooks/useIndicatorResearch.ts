import { useMemo } from 'react';
import { useQuery } from 'urql';

import {
  IndicatorReportDocument,
  StockIndicatorCatalogDocument,
  StockIndicatorReportMatchesDocument,
  StockScreenUniverse,
} from '@/generated/gql/graphql';

import { buildIndicatorReportRequests } from '../indicatorModel';
import type { ScreeningCriteria } from '../types';

const UNIVERSES = {
  STOCK: StockScreenUniverse.Stock,
  ETF: StockScreenUniverse.Etf,
  STOCK_AND_ETF: StockScreenUniverse.StockAndEtf,
};

export function useIndicatorResearch(
  criteria: ScreeningCriteria,
  focusIndicatorId?: string | null
) {
  const [catalog, refreshCatalog] = useQuery({
    query: StockIndicatorCatalogDocument,
    requestPolicy: 'cache-and-network',
  });
  const requests = useMemo(
    () =>
      buildIndicatorReportRequests(criteria, focusIndicatorId).map(request => ({
        ...request,
        universe: UNIVERSES[request.universe],
        conditions: request.conditions.map(condition => ({
          ...condition,
          value: condition.value!,
        })),
      })),
    [criteria, focusIndicatorId]
  );
  const [matches, refreshMatches] = useQuery({
    query: StockIndicatorReportMatchesDocument,
    variables: { requests },
    pause: !requests.length || criteria.screeningMode !== 'INDICATOR',
    requestPolicy: 'cache-and-network',
  });
  // URQL can retain the previous operation's data while a new input is loading.
  // Never present a previous threshold's report as an exact match for this draft.
  const matchesAreCurrent =
    matches.operation?.variables.requests &&
    JSON.stringify(matches.operation.variables.requests) ===
      JSON.stringify(requests);
  return {
    indicators: catalog.data?.stockIndicatorCatalog ?? [],
    catalogLoading: catalog.fetching,
    catalogError: catalog.error,
    matches: matchesAreCurrent
      ? (matches.data?.stockIndicatorReportMatches ?? [])
      : [],
    loading: matches.fetching,
    error: matches.error,
    refresh: () => {
      refreshCatalog({ requestPolicy: 'network-only' });
      if (requests.length && criteria.screeningMode === 'INDICATOR') {
        refreshMatches({ requestPolicy: 'network-only' });
      }
    },
  };
}

export function useIndicatorReport(runKey: string, reportId: string) {
  const [result, refresh] = useQuery({
    query: IndicatorReportDocument,
    variables: { runKey, reportId },
    pause: !runKey || !reportId,
    requestPolicy: 'cache-and-network',
  });
  const current =
    result.operation?.variables.runKey === runKey &&
    result.operation?.variables.reportId === reportId;
  return {
    detail: current ? (result.data?.indicatorReport ?? null) : null,
    fetching: result.fetching,
    error: result.error,
    refresh: () => {
      if (runKey && reportId) refresh({ requestPolicy: 'network-only' });
    },
  };
}
