import { useQuery, useMutation } from 'urql';

import { gql } from '@/generated/gql';

/**
 * 获取持仓列表
 */
export const GetHoldingsQuery = gql(`
  query Portfolio_Holdings($accountId: String) {
    positions(accountId: $accountId) {
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

export const PortfolioOverviewQuery = gql(`
  query Portfolio_Overview($accountId: String!) {
    portfolioOverview(accountId: $accountId) {
      asOf
      positionSnapshot {
        sequence
        source
        reportedAt
        receivedAt
        positionCount
        isComplete
        lastError
      }
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
      summary {
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
    }
  }
`);

export const LatestMarketQuotesQuery = gql(`
  query Portfolio_LatestMarketQuotes($stockList: [String!]!) {
    latestMarketQuotes(stockList: $stockList) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      change
      changePercent
      volume
      amount
    }
  }
`);

export const MarketQuotesSubscription = gql(`
  subscription Portfolio_MarketQuotes($stockList: [String!]!) {
    marketQuotes(stockList: $stockList) {
      stockCode
      currentPrice
      change
      changePercent
      high
      low
      open
      preClose
      volume
      time
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

export const DailyAssetSnapshotsPageQuery = gql(`
  query Portfolio_DailyAssetSnapshotsPage(
    $accountId: String
    $scopeType: String = "ACCOUNT"
    $startDate: String
    $endDate: String
    $first: Int = 60
    $after: String
  ) {
    dailyAssetSnapshotsPage(
      accountId: $accountId
      scopeType: $scopeType
      startDate: $startDate
      endDate: $endDate
      first: $first
      after: $after
    ) {
      items {
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
      pageInfo {
        hasNextPage
        endCursor
      }
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

export const LiquidatePositionsMutation = gql(`
  mutation LiquidatePositions($input: LiquidatePositionsInput!) {
    liquidatePositions(input: $input) {
      groupId
      success
      message
      plans {
        instrumentCode
        success
        planId
        protectedVolume
        conflictPlanIds
        error
      }
    }
  }
`);

export const ExitPlansQuery = gql(`
  query ExitPlans(
    $accountId: String
    $instrumentCode: String
    $statuses: [String!]
    $sourceType: String
    $limit: Int! = 200
  ) {
    exitPlans(
      accountId: $accountId
      instrumentCode: $instrumentCode
      statuses: $statuses
      sourceType: $sourceType
      limit: $limit
    ) {
      planId
      groupId
      accountId
      instrumentCode
      bucket
      sourceType
      sourceId
      strategyRunId
      enabled
      status
      executionMode
      autoExitAuthorized
      autoExitAuthorizationConfigVersion
      autoExitAuthorizationExpiresAt
      configVersion
      completionStrategy
      completionNote
      protectedVolume
      exitedVolume
      remainingVolume
      entryAvgPrice
      costBasis
      capacityStatus
      capacityError
      rules
      metadata
      canEditRules
      editRoute
      phase
      dataQuality
      lastDecision
      peakPrice
      peakDrawdownPct
      trailingFloorPct
      pendingClientOrderId
      pendingIntentId
      lastEvaluatedAt
      lastError
      createdAt
      updatedAt
    }
  }
`);

export const ExitPlanUpdatesSubscription = gql(`
  subscription Portfolio_ExitPlanUpdates(
    $accountId: String
    $instrumentCode: String
  ) {
    exitPlanUpdates(
      accountId: $accountId
      instrumentCode: $instrumentCode
    ) {
      planId
      accountId
      instrumentCode
      occurredAt
    }
  }
`);

export const ExitPlanCapabilitiesQuery = gql(`
  query ExitPlanCapabilities {
    exitPlanCapabilities {
      ruleTypes {
        ruleType
        label
        category
        parameters
      }
      completionStrategies
      conflictStrategies
      executionModes
      ruleSemantics
    }
  }
`);

export const ExitPlanEventsQuery = gql(`
  query ExitPlanEvents($planId: String!, $limit: Int! = 200) {
    exitPlanEvents(planId: $planId, limit: $limit) {
      eventId
      planId
      eventType
      payload
      createdAt
    }
  }
`);

export const ExitPlanHoldingCapacityQuery = gql(`
  query ExitPlanHoldingCapacity($accountId: String, $instrumentCode: String!) {
    exitPlanHoldingCapacity(
      accountId: $accountId
      instrumentCode: $instrumentCode
    ) {
      accountId
      instrumentCode
      totalVolume
      availableVolume
      frozenVolume
      protectedVolume
      pendingVolume
      unallocatedVolume
      capacityStatus
      capacityError
      conflicts {
        planId
        sourceType
        status
        remainingVolume
        pending
      }
    }
  }
`);

export const ExitPlanCostBasisCandidatesQuery = gql(`
  query ExitPlanCostBasisCandidates(
    $accountId: String
    $instrumentCode: String!
    $limit: Int! = 100
  ) {
    exitPlanCostBasisCandidates(
      accountId: $accountId
      instrumentCode: $instrumentCode
      limit: $limit
    ) {
      accountId
      instrumentCode
      historyWarning
      items {
        orderId
        tradedVolume
        tradedPrice
        estimatedBuyFeeCny
        orderTime
        strategyName
        remark
      }
    }
  }
`);

export const CreateManualExitPlanMutation = gql(`
  mutation CreateManualExitPlan($input: CreateManualExitPlanInput!) {
    createManualExitPlan(input: $input) {
      planId
      instrumentCode
      status
      configVersion
      protectedVolume
    }
  }
`);

export const UpdateManualExitPlanMutation = gql(`
  mutation UpdateManualExitPlan($input: UpdateManualExitPlanInput!) {
    updateManualExitPlan(input: $input) {
      planId
      instrumentCode
      status
      configVersion
      protectedVolume
      executionMode
      autoExitAuthorized
      rules
      metadata
    }
  }
`);

export const PreviewExitPlanAuthorizationMutation = gql(`
  mutation PreviewExitPlanAuthorization(
    $input: ExitPlanAuthorizationPreviewInput!
  ) {
    previewExitPlanAuthorization(input: $input) {
      success
      code
      message
      preview {
        challengeId
        confirmationToken
        accountId
        planId
        instrumentCode
        bucket
        sourceType
        executionMode
        configVersion
        protectedVolume
        exitedVolume
        remainingVolume
        costBasis
        rules
        t1Policy
        executionPolicy
        position {
          totalVolume
          availableVolume
          frozenVolume
          yesterdayVolume
          t1UnavailableVolume
          positionUpdatedAt
        }
        otherProtections {
          planId
          sourceType
          status
          remainingVolume
          configVersion
          pending
        }
        readiness
        authorizationFingerprint
        authorizationExpiresAt
        challengeExpiresAt
        warnings
      }
    }
  }
`);

export const ReconcileExitPlanCapacityMutation = gql(`
  mutation ReconcileExitPlanCapacity(
    $accountId: String
    $instrumentCode: String!
  ) {
    reconcileExitPlanCapacity(
      accountId: $accountId
      instrumentCode: $instrumentCode
    ) {
      ready
      capacityStatus
      capacityError
      totalVolume
      protectedVolume
      planIds
    }
  }
`);

export const ConfirmExitPlanAuthorizationMutation = gql(`
  mutation ConfirmExitPlanAuthorization(
    $input: ExitPlanAuthorizationConfirmationInput!
  ) {
    confirmExitPlanAuthorization(input: $input) {
      success
      code
      message
      challengeId
      planId
      configVersion
      authorized
      authorizationExpiresAt
      auditEventId
    }
  }
`);

export const SetExitPlanEnabledMutation = gql(`
  mutation SetExitPlanEnabled(
    $planId: String!
    $enabled: Boolean!
    $configVersion: Int!
  ) {
    setExitPlanEnabled(
      planId: $planId
      enabled: $enabled
      configVersion: $configVersion
    ) {
      planId
      enabled
      status
      configVersion
    }
  }
`);

export const CancelExitPlanMutation = gql(`
  mutation CancelExitPlan($planId: String!, $configVersion: Int!) {
    cancelExitPlan(planId: $planId, configVersion: $configVersion) {
      planId
      enabled
      status
      configVersion
    }
  }
`);

export const EvaluateExitPlanNowMutation = gql(`
  mutation EvaluateExitPlanNow($planId: String!) {
    evaluateExitPlanNow(planId: $planId) {
      planId
      status
      lastEvaluatedAt
      lastDecision
      lastError
    }
  }
`);

export const PreviewExitIntentMutation = gql(`
  mutation PreviewExitIntent($planId: String!, $intentId: String!) {
    previewExitIntent(planId: $planId, intentId: $intentId) {
      success
      code
      message
      preview {
        challengeId
        confirmationToken
        instrumentCode
        side
        targetVolume
        referencePrice
        estimatedAmount
        challengeExpiresAt
        warnings
      }
    }
  }
`);

export const ConfirmExitIntentMutation = gql(`
  mutation ConfirmExitIntent(
    $planId: String!
    $intentId: String!
    $confirmationToken: String!
  ) {
    confirmExitIntent(
      planId: $planId
      intentId: $intentId
      confirmationToken: $confirmationToken
    ) {
      success
      code
      message
      challengeId
    }
  }
`);

export const RejectExitIntentMutation = gql(`
  mutation RejectExitIntent($planId: String!, $intentId: String!) {
    rejectExitIntent(planId: $planId, intentId: $intentId) {
      success
      code
      message
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
      strategy
      dynamicPolicy
      exitPlanId
      executionMode
      autoExitAuthorized
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
      phase
      dataQuality
      lastDecision
      protectedVolume
      exitedVolume
      remainingVolume
      peakPrice
      peakDrawdownPct
      volumeVelocity
      weakScore
      trailingFloorPct
      pendingClientOrderId
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
