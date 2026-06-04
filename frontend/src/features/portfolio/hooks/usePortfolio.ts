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
 * 获取清仓预检概况
 */
export const LiquidationSummaryQuery = gql(`
  query Portfolio_LiquidationSummary($accountId: String) {
    liquidationSummary(accountId: $accountId) {
      totalPositions
      liquidatablePositions
      totalMarketValue
      positions {
        stockCode
        instrumentName
        volume
        canUseVolume
        avgPrice
        marketValue
      }
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
 * 查询条件清仓单
 */
export const ConditionalLiquidationOrdersQuery = gql(`
  query ConditionalLiquidationOrders(
    $accountId: String
    $stockCode: String
    $includeCancelled: Boolean! = false
  ) {
    conditionalLiquidationOrders(
      accountId: $accountId
      stockCode: $stockCode
      includeCancelled: $includeCancelled
    ) {
      id
      accountId
      stockCode
      instrumentName
      enabled
      status
      targetProfitPct
      targetPrice
      sellMode
      sellRatioPct
      sellVolume
      triggeredAt
      triggeredPrice
      triggeredProfitPct
      submittedOrderId
      submittedVolume
      lastCheckedAt
      lastError
      remark
      createdAt
      updatedAt
    }
  }
`);

/**
 * 创建或更新条件清仓单
 */
export const UpsertConditionalLiquidationOrderMutation = gql(`
  mutation UpsertConditionalLiquidationOrder(
    $input: ConditionalLiquidationOrderInput!
  ) {
    upsertConditionalLiquidationOrder(input: $input) {
      id
      accountId
      stockCode
      instrumentName
      enabled
      status
      targetProfitPct
      targetPrice
      sellMode
      sellRatioPct
      sellVolume
      triggeredAt
      triggeredPrice
      triggeredProfitPct
      submittedOrderId
      submittedVolume
      lastCheckedAt
      lastError
      remark
      createdAt
      updatedAt
    }
  }
`);

/**
 * 启用或停用条件清仓单
 */
export const SetConditionalLiquidationOrderEnabledMutation = gql(`
  mutation SetConditionalLiquidationOrderEnabled(
    $orderId: String!
    $enabled: Boolean!
  ) {
    setConditionalLiquidationOrderEnabled(orderId: $orderId, enabled: $enabled) {
      id
      accountId
      stockCode
      instrumentName
      enabled
      status
      targetProfitPct
      targetPrice
      sellMode
      sellRatioPct
      sellVolume
      triggeredAt
      triggeredPrice
      triggeredProfitPct
      submittedOrderId
      submittedVolume
      lastCheckedAt
      lastError
      remark
      createdAt
      updatedAt
    }
  }
`);

/**
 * 取消条件清仓单
 */
export const CancelConditionalLiquidationOrderMutation = gql(`
  mutation CancelConditionalLiquidationOrder($orderId: String!) {
    cancelConditionalLiquidationOrder(orderId: $orderId) {
      id
      accountId
      stockCode
      instrumentName
      enabled
      status
      targetProfitPct
      targetPrice
      sellMode
      sellRatioPct
      sellVolume
      triggeredAt
      triggeredPrice
      triggeredProfitPct
      submittedOrderId
      submittedVolume
      lastCheckedAt
      lastError
      remark
      createdAt
      updatedAt
    }
  }
`);

/**
 * 立即评估条件清仓单
 */
export const EvaluateConditionalLiquidationOrdersMutation = gql(`
  mutation EvaluateConditionalLiquidationOrders(
    $accountId: String
    $stockCode: String
  ) {
    evaluateConditionalLiquidationOrders(
      accountId: $accountId
      stockCode: $stockCode
    ) {
      triggered
      submitted
      message
      sellVolume
      orderId
      latestPrice
      profitPct
      error
      order {
        id
        accountId
        stockCode
        instrumentName
        enabled
        status
        targetProfitPct
        targetPrice
        sellMode
        sellRatioPct
        sellVolume
        triggeredAt
        triggeredPrice
        triggeredProfitPct
        submittedOrderId
        submittedVolume
        lastCheckedAt
        lastError
        remark
        createdAt
        updatedAt
      }
    }
  }
`);

/**
 * 全部清仓
 */
export const LiquidateAllPositionsMutation = gql(`
  mutation LiquidateAllPositions($input: LiquidateAllPositionsInput!) {
    liquidateAllPositions(input: $input) {
      success
      totalPositions
      liquidatedPositions
      failedPositions
      message
      orders
      errors {
        stockCode
        error
      }
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
    liquidate: (stockCode: string, accountId?: string) =>
      execute({ input: { stockCode, confirm: true, accountId } }),
    loading: result.fetching,
    data: result.data,
    error: result.error,
  };
}
