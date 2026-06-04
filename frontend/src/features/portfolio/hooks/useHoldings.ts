import { useCallback, useMemo } from 'react';
import { useQuery, useMutation } from 'urql';

import { useCurrentAccount } from '../../dashboard/hooks';
import type {
  DailyAssetSnapshotData,
  Position,
  PortfolioSummaryData,
} from '../types';

import {
  GetDailyAssetSnapshotsForPortfolioSummaryQuery,
  GetHoldingsQuery,
  GetPortfolioSummaryQuery,
  LiquidatePositionMutation,
} from './usePortfolio';

const SNAPSHOT_TREND_LOOKBACK_DAYS = 180;
const SNAPSHOT_TREND_LIMIT = 366;

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function getSnapshotTrendRange() {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setDate(endDate.getDate() - SNAPSHOT_TREND_LOOKBACK_DAYS);

  return {
    startDate: formatLocalDate(startDate),
    endDate: formatLocalDate(endDate),
  };
}

/**
 * 持仓列表 Hook
 * 组合了持仓数据和账户汇总信息
 */
export function useHoldings() {
  const [holdingsResult, executeHoldingsQuery] = useQuery({
    query: GetHoldingsQuery,
  });

  const [liquidateResult, executeLiquidate] = useMutation(
    LiquidatePositionMutation
  );

  const {
    data: accountData,
    loading: accountLoading,
    error: accountError,
  } = useCurrentAccount();

  const account = accountData?.currentAccount;
  const accountId = account?.id;
  const snapshotRange = useMemo(() => getSnapshotTrendRange(), []);

  const [summaryResult, executeSummaryQuery] = useQuery({
    query: GetPortfolioSummaryQuery,
    variables: { accountId },
    pause: !accountId,
  });

  const [snapshotsResult, executeSnapshotsQuery] = useQuery({
    query: GetDailyAssetSnapshotsForPortfolioSummaryQuery,
    variables: {
      accountId,
      scopeType: 'ACCOUNT',
      startDate: snapshotRange.startDate,
      endDate: snapshotRange.endDate,
      limit: SNAPSHOT_TREND_LIMIT,
    },
    pause: !accountId,
  });

  const holdingsData = holdingsResult.data;
  const holdingsLoading = holdingsResult.fetching;
  const holdingsError = holdingsResult.error;

  // 直接返回 GraphQL Position 数据
  const holdings: Position[] = useMemo(
    () =>
      Array.isArray(holdingsData?.positions) ? holdingsData.positions : [],
    [holdingsData?.positions]
  );

  const portfolioSummary = summaryResult.data?.portfolioSummary as
    | PortfolioSummaryData
    | undefined;

  const dailyAssetSnapshots: DailyAssetSnapshotData[] = useMemo(
    () =>
      Array.isArray(snapshotsResult.data?.dailyAssetSnapshots)
        ? snapshotsResult.data.dailyAssetSnapshots
        : [],
    [snapshotsResult.data?.dailyAssetSnapshots]
  );

  const refetch = useCallback(() => {
    executeHoldingsQuery({ requestPolicy: 'network-only' });
    executeSummaryQuery({ requestPolicy: 'network-only' });
    executeSnapshotsQuery({ requestPolicy: 'network-only' });
  }, [executeHoldingsQuery, executeSummaryQuery, executeSnapshotsQuery]);

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
        holdingsLoading ||
        accountLoading ||
        (summaryResult.fetching && !summaryResult.data) ||
        (snapshotsResult.fetching && !snapshotsResult.data),
      error:
        holdingsError ||
        accountError ||
        summaryResult.error ||
        snapshotsResult.error,
      refetch,
      liquidateHolding,
      isLiquidating: liquidateResult.fetching,
    }),
    [
      holdings,
      portfolioSummary,
      dailyAssetSnapshots,
      holdingsLoading,
      accountLoading,
      holdingsError,
      accountError,
      summaryResult.fetching,
      summaryResult.data,
      summaryResult.error,
      snapshotsResult.fetching,
      snapshotsResult.data,
      snapshotsResult.error,
      refetch,
      liquidateHolding,
      liquidateResult.fetching,
    ]
  );
}
