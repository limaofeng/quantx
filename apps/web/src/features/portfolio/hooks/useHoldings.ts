import { useCallback, useEffect, useMemo, useState } from 'react';
import type { OperationResult } from 'urql';
import { useClient, useQuery, useMutation } from 'urql';

import type {
  Portfolio_DailyAssetSnapshotsPageQuery,
  Portfolio_DailyAssetSnapshotsPageQueryVariables,
} from '@/generated/gql/graphql';

import { useCurrentAccount } from '../../dashboard/hooks';
import type {
  DailyAssetSnapshotData,
  Position,
  PortfolioSummaryData,
} from '../types';

import {
  DailyAssetSnapshotsPageQuery,
  LiquidatePositionMutation,
  PortfolioOverviewQuery,
} from './usePortfolio';
import { useRealTimeHoldings } from './useRealTimeHoldings';

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function getSnapshotTrendRange(lookbackDays: number) {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setDate(endDate.getDate() - lookbackDays);

  return {
    startDate: formatLocalDate(startDate),
    endDate: formatLocalDate(endDate),
  };
}

/**
 * 持仓列表 Hook
 * 组合了持仓数据和账户汇总信息
 */
interface UseHoldingsOptions {
  historyDays?: number;
  loadHistory?: boolean;
}

export function useHoldings({
  historyDays = 180,
  loadHistory = false,
}: UseHoldingsOptions = {}) {
  const {
    data: accountData,
    loading: accountLoading,
    error: accountError,
  } = useCurrentAccount();

  const account = accountData?.currentAccount;
  const accountId = account?.id;
  const snapshotRange = useMemo(
    () => getSnapshotTrendRange(historyDays),
    [historyDays]
  );
  const client = useClient();
  const [historyRefreshVersion, setHistoryRefreshVersion] = useState(0);
  const [dailyAssetSnapshots, setDailyAssetSnapshots] = useState<
    DailyAssetSnapshotData[]
  >([]);
  const [historyError, setHistoryError] = useState<Error | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [overviewResult, executeOverviewQuery] = useQuery({
    query: PortfolioOverviewQuery,
    variables: { accountId: accountId ?? '' },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });

  const [liquidateResult, executeLiquidate] = useMutation(
    LiquidatePositionMutation
  );

  const snapshotHoldings: Position[] = useMemo(
    () => overviewResult.data?.portfolioOverview.positions ?? [],
    [overviewResult.data?.portfolioOverview.positions]
  );
  const {
    holdings,
    latestQuoteAt,
    error: quoteError,
    refreshLatestQuotes,
  } = useRealTimeHoldings({
    holdings: snapshotHoldings,
    enabled: Boolean(accountId),
  });
  const brokerSummary = overviewResult.data?.portfolioOverview.summary;
  const portfolioSummary = useMemo<PortfolioSummaryData | undefined>(() => {
    if (!brokerSummary) return undefined;
    const liveMarketValue = holdings.reduce(
      (total, holding) => total + Number(holding.marketValue || 0),
      0
    );
    const deltaMarketValue =
      liveMarketValue - Number(brokerSummary.totalMarketValue || 0);
    const totalAsset = Number(brokerSummary.totalAsset || 0) + deltaMarketValue;
    const totalProfitLoss =
      Number(brokerSummary.totalProfitLoss || 0) + deltaMarketValue;
    return {
      ...brokerSummary,
      totalAsset,
      totalMarketValue: liveMarketValue,
      cashRatio:
        totalAsset > 0
          ? (Number(brokerSummary.cash || 0) / totalAsset) * 100
          : 0,
      totalProfitLoss,
      totalProfitLossPercent:
        totalAsset - totalProfitLoss > 0
          ? (totalProfitLoss / (totalAsset - totalProfitLoss)) * 100
          : brokerSummary.totalProfitLossPercent,
      profitPositionCount: holdings.filter(
        holding => Number(holding.profitLoss || 0) > 0
      ).length,
      lossPositionCount: holdings.filter(
        holding => Number(holding.profitLoss || 0) < 0
      ).length,
      topHoldings: [...holdings]
        .sort(
          (left, right) =>
            Number(right.marketValue || 0) - Number(left.marketValue || 0)
        )
        .slice(0, 10),
    };
  }, [brokerSummary, holdings]);

  useEffect(() => {
    if (!loadHistory || !accountId || !overviewResult.data?.portfolioOverview) {
      setDailyAssetSnapshots([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }
    let cancelled = false;
    setDailyAssetSnapshots([]);
    setHistoryError(null);
    setHistoryLoading(true);

    const loadHistoryPages = async () => {
      let after: string | null = null;
      let firstPage = true;
      do {
        const result: OperationResult<
          Portfolio_DailyAssetSnapshotsPageQuery,
          Portfolio_DailyAssetSnapshotsPageQueryVariables
        > = await client
          .query(
            DailyAssetSnapshotsPageQuery,
            {
              accountId,
              scopeType: 'ACCOUNT',
              startDate: snapshotRange.startDate,
              endDate: snapshotRange.endDate,
              first: 60,
              after,
            },
            { requestPolicy: 'network-only' }
          )
          .toPromise();
        if (cancelled) return;
        if (result.error) throw result.error;
        const page:
          | Portfolio_DailyAssetSnapshotsPageQuery['dailyAssetSnapshotsPage']
          | undefined = result.data?.dailyAssetSnapshotsPage;
        if (!page) return;
        setDailyAssetSnapshots(previous => {
          const byId = new Map(previous.map(item => [item.id, item]));
          for (const item of page.items) byId.set(item.id, item);
          return Array.from(byId.values()).sort((left, right) =>
            left.tradeDate.localeCompare(right.tradeDate)
          );
        });
        if (firstPage) {
          setHistoryLoading(false);
          firstPage = false;
        }
        after = page.pageInfo.hasNextPage
          ? (page.pageInfo.endCursor ?? null)
          : null;
        if (after) {
          await new Promise<void>(resolve => window.setTimeout(resolve, 0));
        }
      } while (after && !cancelled);
      if (!cancelled) setHistoryLoading(false);
    };

    void loadHistoryPages().catch(error => {
      if (cancelled) return;
      setHistoryLoading(false);
      setHistoryError(
        error instanceof Error ? error : new Error(String(error))
      );
    });
    return () => {
      cancelled = true;
    };
  }, [
    accountId,
    client,
    historyRefreshVersion,
    loadHistory,
    overviewResult.data?.portfolioOverview,
    snapshotRange.endDate,
    snapshotRange.startDate,
  ]);

  const refreshOverview = useCallback(() => {
    executeOverviewQuery({ requestPolicy: 'network-only' });
  }, [executeOverviewQuery]);
  const refreshHistory = useCallback(() => {
    setHistoryRefreshVersion(value => value + 1);
  }, []);
  const refreshQuotes = useCallback(() => {
    refreshLatestQuotes();
  }, [refreshLatestQuotes]);
  const refetch = useCallback(() => {
    refreshOverview();
    refreshQuotes();
    setHistoryRefreshVersion(value => value + 1);
  }, [refreshOverview, refreshQuotes]);

  const liquidateHolding = useCallback(
    async (stockCode: string) => {
      return await executeLiquidate({
        input: { accountId, stockCode, confirm: true },
      });
    },
    [accountId, executeLiquidate]
  );

  return useMemo(
    () => ({
      holdings,
      portfolioSummary,
      dailyAssetSnapshots,
      isLoading:
        accountLoading || (overviewResult.fetching && !overviewResult.data),
      error: overviewResult.error || accountError,
      historyError,
      historyLoading,
      quoteError,
      snapshotAsOf: overviewResult.data?.portfolioOverview.asOf,
      latestQuoteAt,
      refreshHistory,
      refreshOverview,
      refreshQuotes,
      refetch,
      liquidateHolding,
      isLiquidating: liquidateResult.fetching,
    }),
    [
      holdings,
      portfolioSummary,
      dailyAssetSnapshots,
      accountLoading,
      accountError,
      overviewResult.fetching,
      overviewResult.data,
      overviewResult.error,
      historyError,
      historyLoading,
      quoteError,
      latestQuoteAt,
      refreshHistory,
      refreshOverview,
      refreshQuotes,
      refetch,
      liquidateHolding,
      liquidateResult.fetching,
    ]
  );
}
