import { useMemo } from 'react';
import { useQuery } from 'urql';

import {
  FactorReportDocument,
  StockFactorCatalogDocument,
  StockFactorReportMatchesDocument,
  StockScreenUniverse,
} from '@/generated/gql/graphql';

import { buildFactorReportRequests } from '../factorModel';
import type { ScreeningCriteria } from '../types';

const UNIVERSES = {
  STOCK: StockScreenUniverse.Stock,
  ETF: StockScreenUniverse.Etf,
  STOCK_AND_ETF: StockScreenUniverse.StockAndEtf,
};

export function useFactorResearch(
  criteria: ScreeningCriteria,
  focusFactorId?: string | null
) {
  const [catalog, refreshCatalog] = useQuery({
    query: StockFactorCatalogDocument,
    requestPolicy: 'cache-and-network',
  });
  const requests = useMemo(
    () =>
      buildFactorReportRequests(criteria, focusFactorId).map(request => ({
        ...request,
        universe: UNIVERSES[request.universe],
        conditions: request.conditions.map(condition => ({
          ...condition,
          value: condition.value!,
        })),
      })),
    [criteria, focusFactorId]
  );
  const [matches, refreshMatches] = useQuery({
    query: StockFactorReportMatchesDocument,
    variables: { requests },
    pause: !requests.length || criteria.screeningMode === 'INTRADAY',
    requestPolicy: 'cache-and-network',
  });
  // URQL can retain the previous operation's data while a new input is loading.
  // Never present a previous threshold's report as an exact match for this draft.
  const matchesAreCurrent =
    matches.operation?.variables.requests &&
    JSON.stringify(matches.operation.variables.requests) ===
      JSON.stringify(requests);
  return {
    factors: catalog.data?.stockFactorCatalog ?? [],
    catalogLoading: catalog.fetching,
    catalogError: catalog.error,
    matches: matchesAreCurrent
      ? (matches.data?.stockFactorReportMatches ?? [])
      : [],
    loading: matches.fetching,
    error: matches.error,
    refresh: () => {
      refreshCatalog({ requestPolicy: 'network-only' });
      if (requests.length && criteria.screeningMode !== 'INTRADAY') {
        refreshMatches({ requestPolicy: 'network-only' });
      }
    },
  };
}

export function useFactorReport(runKey: string, reportId: string) {
  const [result, refresh] = useQuery({
    query: FactorReportDocument,
    variables: { runKey, reportId },
    pause: !runKey || !reportId,
    requestPolicy: 'cache-and-network',
  });
  const current =
    result.operation?.variables.runKey === runKey &&
    result.operation?.variables.reportId === reportId;
  return {
    detail: current ? (result.data?.factorReport ?? null) : null,
    fetching: result.fetching,
    error: result.error,
    refresh: () => {
      if (runKey && reportId) refresh({ requestPolicy: 'network-only' });
    },
  };
}
