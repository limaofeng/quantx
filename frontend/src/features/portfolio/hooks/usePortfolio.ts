import { useQuery, useMutation } from 'urql';

import { gql } from '@/generated/gql';

/**
 * 获取持仓列表
 */
export const GetHoldingsQuery = gql(`
  query holdings {
    positions {
      id
      accountId
      stockCode
      instrumentName
      volume
      canUseVolume
      frozenVolume
      onRoadVolume
      yesterdayVolume
      avgPrice
      lastPrice
      marketValue
      marketValuePercent
      profitLoss
      profitRate
      openPrice
      accountType
      direction
      createdAt
      updatedAt
    }
  }
`);

/**
 * 获取投资组合汇总信息
 */
export const GetPortfolioSummaryQuery = gql(`
  query Portfolio_Summary($accountId: String) {
    portfolioSummary(accountId: $accountId) {

      accountId
      accountName
      totalAsset
      totalMarketValue
      cash
      cashRatio
      totalProfitLoss
      totalProfitLossPercent
      todayProfitLoss
      todayProfitLossPercent
      positionCount
      profitPositionCount
      lossPositionCount
      updateTime
      topHoldings {
        id
        stockCode
        instrumentName
        volume
        avgPrice
        lastPrice
        marketValue
        marketValuePercent
        profitLoss
        profitRate
      }
    }
  }
`);

/**
 * 获取账户每日收盘资产快照，用于汇总卡片趋势线
 */
export const GetDailyAssetSnapshotsForPortfolioSummaryQuery = gql(`
  query DailyAssetSnapshotsForPortfolioSummary(
    $accountId: String
    $scopeType: String = "ACCOUNT"
    $startDate: String
    $endDate: String
    $limit: Int = 366
  ) {
    dailyAssetSnapshots(
      accountId: $accountId
      scopeType: $scopeType
      startDate: $startDate
      endDate: $endDate
      limit: $limit
    ) {
      id
      scopeType
      scopeKey
      accountId
      tradeDate
      snapshotAt
      source
      totalAssetCny
      cashAvailableCny
      cashFrozenCny
      marketValueCny
      grossAssetDeltaCny
      netCapitalFlowCny
      dailyPnlCny
      dailyReturnPct
      dataQuality
    }
  }
`);

/**
 * 个股清仓
 */
export const LiquidatePositionMutation = gql(`
  mutation LiquidatePosition($input: LiquidatePositionInput!) {
    liquidatePosition(input: $input) {
      success
      stockCode
      message
      orderId
      error
    }
  }
`);

/**
 * 资金赎回
 */
export const RedeemClearedPositionMutation = gql(`
  mutation RedeemClearedPosition($input: RedeemPositionInput!) {
    redeemClearedPosition(input: $input) {
      success
      stockCode
      redeemedAmount
      remainingAmount
      message
      error
    }
  }
`);

/**
 * 使用持仓汇总信息的 Hook
 */
export function usePortfolioSummary(accountId?: string) {
  const [result] = useQuery({
    query: GetPortfolioSummaryQuery,
    variables: { accountId },
  });

  return {
    summary: result.data?.portfolioSummary,
    loading: result.fetching,
    error: result.error,
  };
}

/**
 * 执行个股清仓的 Hook
 */
export function useLiquidatePosition() {
  const [result, execute] = useMutation(LiquidatePositionMutation);

  return {
    liquidate: (stockCode: string) =>
      execute({ input: { stockCode, confirm: true } }),
    loading: result.fetching,
    data: result.data,
    error: result.error,
  };
}
