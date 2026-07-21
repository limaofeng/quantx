import { useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

export const AccountOverviewQuery = gql(`
  query Account_Overview(
    $accountId: String!
    $startDate: String!
    $endDate: String!
  ) {
    portfolioSummary(accountId: $accountId) {
      accountId
      accountName
      totalAsset
      totalMarketValue
      cash
      cashRatio
      totalProfitLoss
      totalProfitLossPercent
      positionCount
      profitPositionCount
      lossPositionCount
      updateTime
    }
    positions(accountId: $accountId) {
      id
      accountId
      stockCode
      instrumentName
      volume
      canUseVolume
      avgPrice
      lastPrice
      marketValue
      profitLoss
      profitRate
      updatedAt
      quote {
        stockCode
        time
        lastPrice
        preClose
      }
    }
    dailyAssetSnapshots(
      accountId: $accountId
      scopeType: "ACCOUNT"
      startDate: $startDate
      endDate: $endDate
      limit: 366
    ) {
      id
      tradeDate
      snapshotAt
      source
      totalAssetCny
      cashAvailableCny
      cashFrozenCny
      marketValueCny
      dailyPnlCny
      dailyReturnPct
      dataQuality
    }
  }
`);

export const ClosedPositionCyclesQuery = gql(`
  query Account_ClosedPositionCycles(
    $accountId: String!
    $startDate: String
    $endDate: String
    $limit: Int!
    $offset: Int!
  ) {
    closedPositionCycles(
      accountId: $accountId
      startDate: $startDate
      endDate: $endDate
      limit: $limit
      offset: $offset
    ) {
      totalCount
      hasMore
      items {
        id
        accountId
        stockCode
        instrumentName
        openedAt
        closedAt
        buyVolume
        sellVolume
        averageBuyPrice
        averageSellPrice
        grossBuyAmount
        grossSellAmount
        grossRealizedPnl
        grossRealizedPnlPercent
        relatedTradeIds
        source
        pnlQuality
        qualityFlags
      }
    }
  }
`);

export function useAccountOverview(
  accountId: string | undefined,
  startDate: string,
  endDate: string
) {
  const [result, reexecute] = useQuery({
    query: AccountOverviewQuery,
    variables: { accountId: accountId || '', startDate, endDate },
    pause: !accountId,
  });

  const refresh = useCallback(() => {
    reexecute({ requestPolicy: 'network-only' });
  }, [reexecute]);

  return useMemo(
    () => ({
      summary: result.data?.portfolioSummary,
      positions: result.data?.positions ?? [],
      snapshots: result.data?.dailyAssetSnapshots ?? [],
      loading: result.fetching,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
  );
}

export function useClosedPositionCycles(
  accountId: string | undefined,
  startDate: string,
  endDate: string,
  limit: number,
  offset: number,
  pause = false
) {
  const [result, reexecute] = useQuery({
    query: ClosedPositionCyclesQuery,
    variables: {
      accountId: accountId || '',
      startDate: startDate || undefined,
      endDate: endDate || undefined,
      limit,
      offset,
    },
    pause: pause || !accountId,
  });

  const refresh = useCallback(() => {
    reexecute({ requestPolicy: 'network-only' });
  }, [reexecute]);

  return {
    page: result.data?.closedPositionCycles,
    loading: result.fetching,
    error: result.error,
    refresh,
  };
}
