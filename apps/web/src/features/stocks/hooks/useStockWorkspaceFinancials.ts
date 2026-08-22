import { useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

export const StockWorkspaceFinancialsQuery = gql(`
  query StockWorkspaceFinancials($stockCode: String!, $limit: Int = 12) {
    financialSummary(stockCode: $stockCode) {
      stockCode
      latestReportDate
      latestAnnounceDate
      revenue
      netProfitExclMinIntInc
      epsBasic
      totalAssets
      totalLiabilities
      totalEquity
      operatingCashFlow
      incomeCount
      balanceCount
      cashFlowCount
      capitalCount
    }
    financialStatements(stockCode: $stockCode, limit: $limit) {
      stockCode
      income {
        reportDate
        revenue
        totalOperatingCost
        operProfit
        netProfitExclMinIntInc
        epsBasic
      }
      balance {
        reportDate
        totalAssets
        totalCurrentAssets
        totalLiabilities
        totalCurrentLiability
        totalEquity
      }
      cashFlow {
        reportDate
        netCashFlowsOperAct
        netCashFlowsInvAct
        netCashFlowsFncAct
        netIncrCashCashEqu
        cashCashEquEndPeriod
      }
      capital {
        reportDate
        totalCapital
        circulatingCapital
        restrictCirculatingCapital
        freeFloatCapital
      }
    }
  }
`);

function normalizeStockCode(value?: string | null) {
  return (value || '').trim().toUpperCase();
}

export function useStockWorkspaceFinancials(
  stockCode?: string | null,
  limit = 12
) {
  const normalizedStockCode = useMemo(
    () => normalizeStockCode(stockCode),
    [stockCode]
  );
  const [result, reexecuteQuery] = useQuery({
    query: StockWorkspaceFinancialsQuery,
    variables: { stockCode: normalizedStockCode, limit },
    pause: !normalizedStockCode,
  });

  const refresh = useCallback(() => {
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [reexecuteQuery]);

  return useMemo(
    () => ({
      summary: result.data?.financialSummary ?? null,
      statements: result.data?.financialStatements ?? null,
      isLoading: result.fetching,
      error: result.error,
      refresh,
    }),
    [
      refresh,
      result.data?.financialStatements,
      result.data?.financialSummary,
      result.error,
      result.fetching,
    ]
  );
}
