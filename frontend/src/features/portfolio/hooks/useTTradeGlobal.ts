import { gql } from '@/generated/gql';

export const TTradeInstrumentNameQuery = gql(`
  query Portfolio_TTradeInstrumentName($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id
      name
    }
  }
`);

export const TTradeGlobalMonitorQuery = gql(`
  query Portfolio_TTradeGlobalMonitor($accountId: String!) {
    tTradeGlobalMonitor(accountId: $accountId) {
      configId
      strategyRunId
      universeRevision
      accountId
      enabled
      mode
      autoExitAcknowledged
      ignoredStockCodes
      configVersion
      targetTradeAmount
      maxTradeAmount
      maxConcurrentBatches
      maxTotalTExposurePct
      signalLookbackSeconds
      stabilizationSeconds
      pullbackThresholdPct
      reboundThresholdPct
      maxSpreadTicks
      approvalTtlSeconds
      maxPriceDeviationPct
      targetProfitPct
      baseFloorPct
      initialGapPct
      trailingGapSlope
      maxGapPct
      hardStopEnabled
      hardStopPct
      timeExitMode
      timeExitTime
      maxHoldingTradingDays
      cooldownSeconds
      holdingCount
      eligibleCount
      ignoredCount
      monitoredCount
      pendingSignalCount
      activeBatchCount
      drainingCount
      lastReconciledAt
      lastError
      updatedAt
      positionSnapshotSource
      positionSnapshotSequence
      positionSnapshotReportedAt
      positionSnapshotReceivedAt
      positionSnapshotComplete
      positionSnapshotError
      holdings {
        stockCode
        instrumentName
        volume
        availableVolume
        ignored
        eligible
        status
        reason
        session {
          runId
          runStatus
          status
          mode
          activeVolume
          lastNetProfitPct
          peakNetProfitPct
          trailingFloorPct
          completedCycles
          pendingEntryIntentId
          errorMessage
        }
      }
      sessions {
        runId
        stockCode
        runStatus
        status
        mode
        targetTradeAmount
        maxTradeAmount
        plannedEntryVolume
        currentSignal
        pendingEntryIntentId
        activeVolume
        lastPrice
        lastNetProfitPct
        peakNetProfitPct
        trailingFloorPct
        targetProfitPct
        completedCycles
        errorMessage
      }
    }
  }
`);

export const TTradeSourceOrdersQuery = gql(`
  query Portfolio_TTradeSourceOrders($accountId: String!, $startDate: String!, $endDate: String!) {
    historyOrders(accountId: $accountId, startDate: $startDate, endDate: $endDate) {
      id
      stockCode
      type
      status
      tradedPrice
      tradedVolume
      time
      strategyName
      orderRemark
    }
    tTradeImportedEntries(accountId: $accountId) {
      sourceTradeId
      sourceOrderId
      stockCode
      volume
      price
      status
      sourceTradeTime
      strategyRunId
      batchId
    }
  }
`);

export const SaveTTradeGlobalMonitorMutation = gql(`
  mutation Portfolio_SaveTTradeGlobalMonitor(
    $input: TTradeGlobalSettingsInput!
  ) {
    saveTTradeGlobalMonitor(input: $input) {
      success
      code
      message
    }
  }
`);

export const ReconcileTTradeGlobalMonitorMutation = gql(`
  mutation Portfolio_ReconcileTTradeGlobalMonitor($accountId: String!) {
    reconcileTTradeGlobalMonitor(accountId: $accountId) {
      success
      code
      message
    }
  }
`);

export const ImportTTradeExternalEntryMutation = gql(`
  mutation Portfolio_ImportTTradeExternalEntry(
    $input: TTradeExternalEntryInput!
  ) {
    importTTradeExternalEntry(input: $input) {
      success
      code
      message
    }
  }
`);

export const SyncTTradeSourceOrdersMutation = gql(`
  mutation Portfolio_SyncTTradeSourceOrders($accountId: String!) {
    syncTTradeSourceOrders(accountId: $accountId) {
      success
      code
      message
    }
  }
`);

export const TTradeReplayPreparationQuery = gql(`
  query Portfolio_TTradeReplayPreparation(
    $accountId: String!
    $startTime: DateTime!
  ) {
    tTradeReplayPreparation(accountId: $accountId, startTime: $startTime) {
      accountId
      startTime
      snapshotId
      snapshotDate
      snapshotSource
      initialCash
      initialTotalAsset
      requiresManualPortfolio
      message
      positions {
        stockCode
        instrumentName
        volume
        availableVolume
        avgPrice
        lastPrice
        marketValue
      }
    }
  }
`);

export const TTradeReplayHistoryQuery = gql(`
  query Portfolio_TTradeReplayHistory($accountId: String!, $limit: Int!) {
    tTradeReplayHistory(accountId: $accountId, limit: $limit) {
      runId
      backtestId
      status
      progressPct
      startTime
      endTime
      snapshotDate
      createdAt
      errorMessage
      dataQuality
      dataQualityMessage
      summary {
        tNetProfit
        excessReturnPct
        completedCycles
      }
    }
  }
`);

export const TTradeReplayQuery = gql(`
  query Portfolio_TTradeReplay($runId: String!) {
    tTradeReplay(runId: $runId) {
      runId
      backtestId
      accountId
      status
      progressPct
      startTime
      endTime
      snapshotId
      snapshotDate
      createdAt
      updatedAt
      errorMessage
      dataQuality
      dataQualityMessage
      skippedStockCodes
      summary {
        initialEquity
        finalEquity
        tNetProfit
        totalReturnPct
        passiveFinalEquity
        passiveReturnPct
        excessReturnPct
        maxDrawdownPct
        totalFees
        turnover
        completedCycles
        openCycles
        winningCycles
        winRatePct
      }
      instruments {
        stockCode
        instrumentName
        status
        reason
        tNetProfit
        totalFees
        completedCycles
        openCycles
        winningCycles
        winRatePct
      }
      curve {
        timestamp
        equity
        passiveEquity
        tNetProfit
        returnPct
        passiveReturnPct
        excessReturnPct
      }
    }
  }
`);

export const TTradeReplayCyclesQuery = gql(`
  query Portfolio_TTradeReplayCycles(
    $runId: String!
    $offset: Int!
    $limit: Int!
  ) {
    tTradeReplayCycles(runId: $runId, offset: $offset, limit: $limit) {
      total
      offset
      limit
      hasMore
      items {
        batchId
        stockCode
        status
        entryTime
        exitTime
        entryVolume
        exitVolume
        openVolume
        entryAvgPrice
        exitAvgPrice
        totalFees
        netProfit
        netReturnPct
        exitReason
      }
    }
  }
`);

export const StartTTradeReplayMutation = gql(`
  mutation Portfolio_StartTTradeReplay($input: TTradeReplayStartInput!) {
    startTTradeReplay(input: $input) {
      success
      code
      message
      replay {
        runId
        status
        progressPct
      }
    }
  }
`);

export const CancelTTradeReplayMutation = gql(`
  mutation Portfolio_CancelTTradeReplay($runId: String!) {
    cancelTTradeReplay(runId: $runId) {
      success
      code
      message
      replay {
        runId
        status
      }
    }
  }
`);
