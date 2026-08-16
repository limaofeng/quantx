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
      momentumEnabled
      momentumWindowSeconds
      momentumMinRisePct
      momentumMinMoveSeconds
      momentumBaselineSeconds
      momentumMinAmountVelocityRatio
      momentumMinVwapPremiumPct
      momentumMaxVwapPremiumPct
      momentumHighToleranceTicks
      momentumMaxSpreadTicks
      momentumMaxSpreadPct
      approvalTtlSeconds
      maxPriceDeviationPct
      targetProfitPct
      baseFloorPct
      initialGapPct
      trailingGapSlope
      maxGapPct
      highProfitLockEnabled
      highProfitArmPct
      highProfitMaxDrawdownPct
      rapidReversalEnabled
      rapidReversalWindowSeconds
      rapidReversalDrawdownPct
      rapidReversalConfirmTicks
      limitUpTouchExitEnabled
      limitUpTouchToleranceTicks
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
      rolloutStage
      engineStatus
      agentStatus
      reconcileStatus
      killSwitch
      canApprove
      canActivateLive
      blockedReasons
      projectionVersion
      projectionGeneratedAt
      readiness {
        accountId
        ready
        status
        preparationReady
        automationReady
        stage
        engineStatus
        agentStatus
        agentDeviceId
        agentMode
        protocolVersion
        reconcileStatus
        killSwitch
        policyVersion
        canApprove
        canActivateLive
        blockedReasons
        preparationBlockedReasons
        manualCoexistence
        externalOrderCount
        externalTradeCount
        controlledWindowActive
        controlledWindowSnapshotId
        controlledWindowStartedAt
        newExternalOrderCount
        newExternalTradeCount
        workingExternalOrderCount
        snapshotId
        snapshotHash
        snapshotAt
        reconciliationAgeSeconds
        queuedCommandCount
        queueDelaySeconds
        deadLetterCount
        unresolvedCriticalAlertCount
        journalIntegrity
        journalSizeBytes
        journalPendingReports
        lastBackupAt
        checkedAt
        checks {
          code
          passed
          message
          scope
        }
      }
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
          pendingExitIntentId
          entryOrderStatus
          exitOrderStatus
          entryFilledVolume
          entryAvgPrice
          exitFilledVolume
          exitAvgPrice
          profitArmed
          lastExitReason
          canCancel
          errorMessage
          latestEvaluation {
            phase
            lastTickAt
            processedTickCount
            windowSampleCount
            windowCoverageSeconds
            triggered
            reason
            signalType
            signalPrice
            windowHigh
            windowLow
            pullbackPct
            reboundPct
            vwap
            vwapPremiumPct
            spreadTicks
            spreadPct
            momentumRisePct
            momentumMoveSeconds
            momentumAmountVelocityRatio
            momentumBaselineCoverageSeconds
          }
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
        pendingExitIntentId
        entryOrderStatus
        exitOrderStatus
        entryFilledVolume
        entryAvgPrice
        exitFilledVolume
        exitAvgPrice
        activeVolume
        lastPrice
        lastNetProfitPct
        peakNetProfitPct
        trailingFloorPct
        targetProfitPct
        profitArmed
        lastExitReason
        canCancel
        completedCycles
        errorMessage
        latestEvaluation {
          phase
          lastTickAt
          processedTickCount
          windowSampleCount
          windowCoverageSeconds
          triggered
          reason
          signalType
          signalPrice
          windowHigh
          windowLow
          pullbackPct
          reboundPct
          vwap
          vwapPremiumPct
          spreadTicks
          spreadPct
          momentumRisePct
          momentumMoveSeconds
          momentumAmountVelocityRatio
          momentumBaselineCoverageSeconds
        }
      }
    }
  }
`);

export const TTradeReadinessQuery = gql(`
  query Portfolio_TTradeReadiness($accountId: String!) {
    validateTTradeLiveReadiness(accountId: $accountId) {
      accountId
      ready
      status
      preparationReady
      automationReady
      stage
      engineStatus
      agentStatus
      agentDeviceId
      agentMode
      protocolVersion
      reconcileStatus
      killSwitch
      policyVersion
      canApprove
      canActivateLive
      blockedReasons
      preparationBlockedReasons
      manualCoexistence
      externalOrderCount
      externalTradeCount
      controlledWindowActive
      controlledWindowSnapshotId
      controlledWindowStartedAt
      newExternalOrderCount
      newExternalTradeCount
      workingExternalOrderCount
      snapshotId
      snapshotHash
      snapshotAt
      reconciliationAgeSeconds
      queuedCommandCount
      queueDelaySeconds
      deadLetterCount
      unresolvedCriticalAlertCount
      journalIntegrity
      journalSizeBytes
      journalPendingReports
      lastBackupAt
      checkedAt
      checks {
        code
        passed
        message
        scope
      }
    }
  }
`);

export const TTradeBatchesPageQuery = gql(`
  query Portfolio_TTradeBatchesPage(
    $accountId: String!
    $statusGroup: String
    $first: Int!
    $after: String
  ) {
    tTradeBatchesPage(
      accountId: $accountId
      statusGroup: $statusGroup
      first: $first
      after: $after
    ) {
      items {
        batchId
        accountId
        stockCode
        strategyRunId
        status
        entryIntentId
        exitIntentId
        entryClientOrderId
        exitClientOrderId
        entryBrokerOrderId
        exitBrokerOrderId
        targetVolume
        entryFilledVolume
        entryAvgPrice
        exitFilledVolume
        exitAvgPrice
        activeVolume
        lastPrice
        lastNetProfitPct
        peakNetProfitPct
        trailingFloorPct
        exitReason
        exceptionReason
        policyVersion
        version
        createdAt
        updatedAt
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
`);

export const TTradeBatchEventsPageQuery = gql(`
  query Portfolio_TTradeBatchEventsPage(
    $accountId: String!
    $batchId: String
    $first: Int!
    $after: String
  ) {
    tTradeBatchEventsPage(
      accountId: $accountId
      batchId: $batchId
      first: $first
      after: $after
    ) {
      items {
        eventId
        batchId
        eventType
        status
        clientOrderId
        brokerOrderId
        payload
        createdAt
        appliedAt
        error
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
`);

export const TTradeSignalHistoryPageQuery = gql(`
  query Portfolio_TTradeSignalHistoryPage(
    $accountId: String!
    $first: Int!
    $after: String
  ) {
    tTradeSignalHistoryPage(
      accountId: $accountId
      first: $first
      after: $after
    ) {
      items {
        intentId
        runId
        stockCode
        status
        statusReason
        signalPrice
        pullbackPct
        reboundPct
        requestedVolume
        createdAt
        expiresAt
        updatedAt
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
`);

export const TTradeUpdatesSubscription = gql(`
  subscription Portfolio_TTradeUpdates($accountId: String!) {
    tTradeUpdates(accountId: $accountId) {
      accountId
      version
      occurredAt
    }
  }
`);

export const TTradeOperationsQuery = gql(`
  query Portfolio_TTradeOperations($accountId: String!) {
    validateTTradeLiveReadiness(accountId: $accountId) {
      accountId
      ready
      status
      preparationReady
      automationReady
      stage
      engineStatus
      agentStatus
      agentDeviceId
      reconcileStatus
      killSwitch
      policyVersion
      canApprove
      canActivateLive
      blockedReasons
      preparationBlockedReasons
      manualCoexistence
      externalOrderCount
      externalTradeCount
      controlledWindowActive
      controlledWindowSnapshotId
      controlledWindowStartedAt
      newExternalOrderCount
      newExternalTradeCount
      workingExternalOrderCount
      checkedAt
      checks {
        code
        passed
        message
        scope
      }
    }
    tTradeBatches(accountId: $accountId, offset: 0, limit: 100) {
      batchId
      stockCode
      strategyRunId
      status
      entryIntentId
      exitIntentId
      entryClientOrderId
      exitClientOrderId
      entryBrokerOrderId
      exitBrokerOrderId
      targetVolume
      entryFilledVolume
      entryAvgPrice
      exitFilledVolume
      exitAvgPrice
      activeVolume
      lastPrice
      lastNetProfitPct
      peakNetProfitPct
      trailingFloorPct
      exitReason
      exceptionReason
      policyVersion
      version
      createdAt
      updatedAt
    }
    tTradeBatchEvents(accountId: $accountId, limit: 100) {
      eventId
      batchId
      eventType
      status
      clientOrderId
      brokerOrderId
      payload
      createdAt
      appliedAt
      error
    }
  }
`);

export const BeginTTradeControlledWindowMutation = gql(`
  mutation Portfolio_BeginTTradeControlledWindow(
    $accountId: String!
    $snapshotId: String!
  ) {
    beginTTradeControlledWindow(
      accountId: $accountId
      snapshotId: $snapshotId
    ) {
      success
      code
      message
      readiness {
        status
        stage
        controlledWindowActive
        controlledWindowSnapshotId
        workingExternalOrderCount
        blockedReasons
        preparationBlockedReasons
      }
    }
  }
`);

export const ActivateTTradeLiveMutation = gql(`
  mutation Portfolio_ActivateTTradeLive(
    $accountId: String!
    $policyVersion: Int!
    $targetStage: TTradeRolloutTarget!
    $confirmation: String!
  ) {
    activateTTradeLive(
      accountId: $accountId
      policyVersion: $policyVersion
      targetStage: $targetStage
      confirmation: $confirmation
    ) {
      success
      code
      message
      readiness {
        ready
        status
        preparationReady
        automationReady
        stage
        canApprove
        controlledWindowActive
        blockedReasons
        preparationBlockedReasons
      }
    }
  }
`);

export const PauseTTradeEntriesMutation = gql(`
  mutation Portfolio_PauseTTradeEntries($accountId: String!, $reason: String!) {
    pauseTTradeEntries(accountId: $accountId, reason: $reason) {
      success
      code
      message
    }
  }
`);

export const TriggerTTradeKillSwitchMutation = gql(`
  mutation Portfolio_TriggerTTradeKillSwitch(
    $accountId: String!
    $reason: String!
  ) {
    triggerTTradeKillSwitch(accountId: $accountId, reason: $reason) {
      success
      code
      message
    }
  }
`);

export const CancelTTradeOrderMutation = gql(`
  mutation Portfolio_CancelTTradeOrder(
    $accountId: String!
    $clientOrderId: String!
  ) {
    cancelTTradeOrder(
      accountId: $accountId
      clientOrderId: $clientOrderId
    ) {
      success
      code
      message
    }
  }
`);

export const TTradeSignalHistoryQuery = gql(`
  query Portfolio_TTradeSignalHistory($accountId: String!, $limit: Int!) {
    tTradeSignalHistory(accountId: $accountId, limit: $limit) {
      intentId
      runId
      stockCode
      status
      statusReason
      signalPrice
      pullbackPct
      reboundPct
      requestedVolume
      createdAt
      expiresAt
      updatedAt
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
        naturalExitCycles
        forcedExitCycles
        liquidationFailedCycles
        winningCycles
        winRatePct
        capitalCapacity
        averageOccupiedCapital
        peakOccupiedCapital
        capitalOccupancyPct
        capitalAvailabilityPct
        capitalTurnoverTimes
        capitalTurnoverPerTradingDay
        capitalUtilizationPct
        averageHoldingHours
        maxHoldingHours
        capitalProfitPerOccupiedDayPct
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
        forcedExitCycles
        winningCycles
        winRatePct
        capitalUtilizationPct
        averageHoldingHours
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
      report {
        status
        schemaVersion
        generatedAt
        conclusionCode
        conclusion
        htmlArtifact
        jsonArtifact
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
        liquidationStatus
        forcedExit
        entryCapital
        holdingHours
        capitalUtilizationPct
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
