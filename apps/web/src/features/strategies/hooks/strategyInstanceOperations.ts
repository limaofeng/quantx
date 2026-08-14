import { gql } from '@/generated/gql';

export const StrategyDefinitionsQuery = `
  query StrategyDefinitions($includeAssistantManaged: Boolean! = false) {
    strategyDefinitions(includeAssistantManaged: $includeAssistantManaged) {
      key
      strategyId
      displayName
      market
      description
      parameterSchema {
        type
        required
        additionalProperties
        properties {
          key
          value {
            type
            title
            description
            default
            minimum
            maximum
            step
            enum
            enumDescriptions
            placeholder
            unit
            widget
            group
          }
        }
      }
      supportedInstruments
      instrumentUniverseMode
      riskLevel
      category
    }
  }
`;

export const StrategyInstancesQuery = `
  query StrategyInstances($status: String, $strategyKey: String, $instrumentCode: String, $includeAssistantManaged: Boolean! = false) {
    strategyInstances(status: $status, strategyKey: $strategyKey, instrumentCode: $instrumentCode, includeAssistantManaged: $includeAssistantManaged) {
      id
      strategyKey
      strategyId
      strategyName
      instrumentCode
      displayName
      status
      mode
      parameters
      parameterVersion
      createdAt
      updatedAt
      lastDecisionAt
      latestExecutionStatus
    }
  }
`;

export const StrategyInstanceQuery = `
  query StrategyInstance($id: String!) {
    strategyInstance(id: $id) {
      id
      strategyKey
      strategyId
      strategyName
      instrumentCode
      displayName
      status
      mode
      parameters
      parameterVersion
      createdAt
      updatedAt
      lastDecisionAt
      latestExecutionStatus
    }
  }
`;

export const StrategyDecisionHistoryQuery = `
  query StrategyDecisionHistory($instanceId: String!, $cursor: String, $limit: Int! = 50, $backtestId: String) {
    strategyDecisionHistory(instanceId: $instanceId, cursor: $cursor, limit: $limit, backtestId: $backtestId) {
      id
      instanceId
      traceId
      decidedAt
      inputSummary
      outputSummary
      statePatch
      decisionTrace
      reason
      tags
      tradeIntents {
        id
        side
        instrumentCode
        targetBucket
        priceIntent
        quantityIntent
        reason
        traceId
        status
        createdAt
        updatedAt
      }
    }
  }
`;

export const StrategyExecutionTraceQuery = `
  query StrategyExecutionTrace($instanceId: String!, $decisionId: String, $backtestId: String, $cursor: String, $limit: Int! = 50) {
    strategyExecutionTrace(instanceId: $instanceId, decisionId: $decisionId, backtestId: $backtestId, cursor: $cursor, limit: $limit) {
      id
      intentId
      instrumentCode
      side
      orderId
      riskDecision
      sizingResult
      orderStatus
      fillStatus
      executedPrice
      executedVolume
      executedTime
      reason
      traceId
      createdAt
      updatedAt
    }
  }
`;

export const StrategyBucketLedgerQuery = `
  query StrategyBucketLedger($instanceId: String!) {
    strategyBucketLedger(instanceId: $instanceId) {
      lockedCore
      core
      swing
      updatedAt
      raw
    }
  }
`;

export const StrategyGridBookQuery = `
  query StrategyGridBook($instanceId: String!, $backtestId: String) {
    strategyGridBook(instanceId: $instanceId, backtestId: $backtestId) {
      runId
      instrumentCode
      basePrice
      parameterVersion
      version
      modelVersion
      inventoryModel
      releaseRule
      sellEmptyBehavior
      editable
      needsBacktest
      updatedAt
      summary {
        totalLevels
        enabledLevels
        pendingLevels
        filledLevels
        disabledLevels
        plannedAmount
        buySlotCount
        sellWaterlineCount
        openLotShares
        reservedLotShares
        waitingInventoryLevels
        completedCycles
        releaseEventCount
      }
      levels {
        gridId
        levelIndex
        side
        role
        price
        plannedShares
        amount
        pctFromBase
        expectedProfit
        enabled
        status
        monitoring
        pendingShares
        filledShares
        availableInventoryShares
        reservedInventoryShares
        cycleCount
        waitingReason
        orderId
        entryPrice
        entryTime
        lastIntentId
        lastTraceId
        reason
        updatedAt
      }
      inventoryLots {
        lotId
        sourceLevelId
        sourceLevelIndex
        source
        bucket
        entryPrice
        originalShares
        remainingShares
        reservedShares
        reservedForLevelId
        reservedOrderId
        status
        createdAt
        updatedAt
      }
      releaseEvents {
        eventId
        sellLevelId
        sellLevelIndex
        releasedLevelId
        releasedLevelIndex
        lotIds
        orderId
        intentId
        tradeId
        price
        shares
        createdAt
      }
    }
  }
`;

export const UpdateStrategyGridBookMutation = `
  mutation UpdateStrategyGridBook($instanceId: String!, $input: StrategyGridBookUpdateInput!) {
    updateStrategyGridBook(instanceId: $instanceId, input: $input) {
      runId
      instrumentCode
      basePrice
      parameterVersion
      version
      modelVersion
      inventoryModel
      releaseRule
      sellEmptyBehavior
      editable
      needsBacktest
      updatedAt
      summary {
        totalLevels
        enabledLevels
        pendingLevels
        filledLevels
        disabledLevels
        plannedAmount
        buySlotCount
        sellWaterlineCount
        openLotShares
        reservedLotShares
        waitingInventoryLevels
        completedCycles
        releaseEventCount
      }
      levels {
        gridId
        levelIndex
        side
        role
        price
        plannedShares
        amount
        pctFromBase
        expectedProfit
        enabled
        status
        monitoring
        pendingShares
        filledShares
        availableInventoryShares
        reservedInventoryShares
        cycleCount
        waitingReason
        orderId
        entryPrice
        entryTime
        lastIntentId
        lastTraceId
        reason
        updatedAt
      }
      inventoryLots {
        lotId
        sourceLevelId
        sourceLevelIndex
        source
        bucket
        entryPrice
        originalShares
        remainingShares
        reservedShares
        reservedForLevelId
        reservedOrderId
        status
        createdAt
        updatedAt
      }
      releaseEvents {
        eventId
        sellLevelId
        sellLevelIndex
        releasedLevelId
        releasedLevelIndex
        lotIds
        orderId
        intentId
        tradeId
        price
        shares
        createdAt
      }
    }
  }
`;

export const BacktestHistoryQuery = `
  query BacktestHistory($runId: String!) {
    backtestHistory(runId: $runId) {
      id
      strategyRunId
      version
      parameters
      instruments
      backtestStartTime
      backtestEndTime
      startTime
      endTime
      metrics
      status
      errorMessage
      resultPath
      createdAt
    }
  }
`;

export const StrategyExecutionLogsQuery = gql(`
  query StrategyExecutionLogs(
    $runId: String!
    $backtestId: String
    $version: Int
    $cursor: Int
    $limit: Int! = 200
    $before: Boolean! = false
    $tail: Boolean! = true
  ) {
    strategyExecutionLogs(
      runId: $runId
      backtestId: $backtestId
      version: $version
      cursor: $cursor
      limit: $limit
      before: $before
      tail: $tail
    ) {
      runId
      mode
      backtestId
      backtestVersion
      sourcePath
      startCursor
      endCursor
      hasPreviousPage
      hasNextPage
      totalLines
      fileSizeBytes
      entries {
        runId
        timestamp
        level
        message
        source
      }
    }
  }
`);

export const StrategyPerformanceQuery = `
  query StrategyPerformance(
    $runId: String!
    $backtestId: String
    $benchmarkCode: String
    $cursor: String
    $limit: Int! = 2000
  ) {
    strategyPerformance(
      runId: $runId
      backtestId: $backtestId
      benchmarkCode: $benchmarkCode
      cursor: $cursor
      limit: $limit
    ) {
      runId
      backtestId
      mode
      benchmarkCode
      source
      generatedAt
      summaryOnly
      summary
      risk
      tradeStats
      executionQuality
      equityCurve {
        sequence
        timestamp
        equity
        value
        benchmarkValue
        eventType
      }
      drawdownCurve {
        sequence
        timestamp
        equity
        value
        benchmarkValue
        eventType
      }
      monthlyReturns {
        month
        returnPct
      }
      dataQuality {
        status
        warning
        sampleCount
        returnedSampleCount
        truncated
        rawSampleCount
        compressedSampleCount
        compressionPolicy
      }
      pageInfo {
        hasMore
        nextCursor
      }
    }
  }
`;

export const RerunBacktestVersionMutation = `
  mutation RerunBacktestVersion($runId: String!, $backtestStartTime: DateTime, $backtestEndTime: DateTime) {
    rerunBacktestVersion(runId: $runId, backtestStartTime: $backtestStartTime, backtestEndTime: $backtestEndTime) {
      id
      strategyRunId
      version
      parameters
      instruments
      backtestStartTime
      backtestEndTime
      startTime
      endTime
      metrics
      status
      errorMessage
      resultPath
      createdAt
    }
  }
`;

export const DeleteBacktestVersionMutation = `
  mutation DeleteBacktestVersion($runId: String!, $backtestId: String!) {
    deleteBacktestVersion(runId: $runId, backtestId: $backtestId) {
      success
      message
      data
    }
  }
`;

export const CreateStrategyInstanceMutation = `
  mutation CreateStrategyInstance($input: StrategyInstanceCreateInput!, $autoStart: Boolean! = true) {
    createStrategyInstance(input: $input, autoStart: $autoStart) {
      id
      strategyId
      instrumentCode
      displayName
      status
      mode
    }
  }
`;

export const UpdateStrategyInstanceParametersMutation = `
  mutation UpdateStrategyInstanceParameters($instanceId: String!, $input: StrategyInstanceParameterUpdateInput!) {
    updateStrategyInstanceParameters(instanceId: $instanceId, input: $input) {
      id
      parameters
      parameterVersion
      status
    }
  }
`;

export const PauseStrategyInstanceMutation = `
  mutation PauseStrategyInstance($instanceId: String!) {
    pauseStrategyInstance(instanceId: $instanceId) {
      success
      message
    }
  }
`;

export const ResumeStrategyInstanceMutation = `
  mutation ResumeStrategyInstance($instanceId: String!) {
    resumeStrategyInstance(instanceId: $instanceId) {
      success
      message
    }
  }
`;

export const DeleteStrategyRunMutation = `
  mutation DeleteStrategyRun($runId: String!) {
    deleteStrategyRun(runId: $runId) {
      success
      message
    }
  }
`;

export const CloneStrategyInstanceMutation = `
  mutation CloneStrategyInstance($sourceId: String!, $instrumentCode: String!) {
    cloneStrategyInstance(sourceId: $sourceId, instrumentCode: $instrumentCode) {
      id
      strategyId
      instrumentCode
      displayName
      status
      mode
    }
  }
`;
