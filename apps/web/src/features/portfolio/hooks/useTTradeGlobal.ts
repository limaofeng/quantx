import { gql } from '@/generated/gql';

export const TTradeSignalPolicyFieldsFragment = gql(`
  fragment Portfolio_TTradeSignalPolicyFields on TTradeSignalPolicy {
    policyVersion
    featureSchemaVersion
    maxSamples
    maxQuoteAgeMs
    pullbackMinSamples
    pullbackMinCoverageSeconds
    momentumMinSamples
    momentumMinCoverageSeconds
    sparseDegradedGapSeconds
    pullbackRequiredFields
    momentumRequiredFields
    allowedSessionCodes
    continuousAmStartTime
    continuousAmEndTime
    continuousPmStartTime
    continuousPmEndTime
    closeProtectionSeconds
    pullbackLookbackSeconds
    pullbackStabilizationSeconds
    pullbackThresholdPct
    pullbackFormationThresholdMultiplier
    pullbackReboundThresholdPct
    pullbackMaxSpreadTicks
    pullbackVolumeShortWindowSeconds
    pullbackVolumeBaselineWindowSeconds
    momentumEnabled
    momentumWindowSeconds
    momentumMinRisePct
    momentumFormationThresholdMultiplier
    momentumMinMoveSeconds
    momentumBaselineSeconds
    momentumBaselineCoverageRatio
    momentumMinAmountVelocityRatio
    momentumMinVwapPremiumPct
    momentumMaxVwapPremiumPct
    momentumHighToleranceTicks
    momentumMaxSpreadTicks
    momentumMaxSpreadPct
    profilePullbackThresholdMinMultiplier
    profilePullbackThresholdMaxMultiplier
    profileMomentumRiseMinMultiplier
    profileMomentumRiseMaxMultiplier
    profileMomentumVelocityMinRatio
    profileMomentumVelocityMaxRatio
    pullbackDepthWeight
    pullbackReboundWeight
    pullbackStabilizationWeight
    pullbackTurnSlopeWeight
    pullbackVwapWeight
    pullbackLiquidityWeight
    pullbackVolumeWeight
    momentumRiseWeight
    momentumTurnoverWeight
    momentumSlopeWeight
    momentumPersistenceWeight
    momentumVwapWeight
    momentumLiquidityWeight
    momentumBookImbalanceWeight
    pullbackDepthScoreMinPct
    pullbackDepthScoreTargetMultiplier
    pullbackReboundScoreMinPct
    pullbackReboundScoreMaxPct
    pullbackStabilizationScoreMinSeconds
    pullbackStabilizationScoreMaxSeconds
    pullbackTurnSlopeScoreMinPctPerSecond
    pullbackTurnSlopeScoreMaxPctPerSecond
    pullbackVwapFullScoreMaxPremiumPct
    pullbackVwapZeroScorePremiumPct
    pullbackLiquidityFullScoreSpreadTicks
    pullbackLiquidityZeroScoreSpreadTicks
    pullbackVolumeScoreMinRatio
    pullbackVolumeScoreMaxRatio
    momentumRiseScoreMinPct
    momentumRiseScoreTargetMultiplier
    momentumTurnoverScoreMinRatio
    momentumTurnoverScoreTargetMultiplier
    momentumSlopeScoreMinPctPerSecond
    momentumSlopeScoreTargetMultiplier
    momentumPersistenceScoreMinRatio
    momentumPersistenceScoreMaxRatio
    momentumVwapZeroScoreMinPremiumPct
    momentumVwapZeroScoreMaxPremiumPct
    momentumLiquidityFullScoreSpreadTicks
    momentumLiquidityZeroScoreSpreadTicks
    momentumBookImbalanceScoreMinRatio
    momentumBookImbalanceScoreMaxRatio
    pullbackDataQualityPenaltyPoints
    pullbackChasePenaltyStartPremiumPct
    pullbackChasePenaltyFullPremiumPct
    pullbackChasePenaltyPoints
    momentumDataQualityPenaltyPoints
    momentumOverextensionPenaltyStartPremiumPct
    momentumOverextensionPenaltyFullPremiumPct
    momentumOverextensionPenaltyPoints
    previewScore
    candidateScore
    revalidateScore
    rearmScore
    candidateConfirmSeconds
    candidateConfirmTicks
    candidateTtlSeconds
    rearmSeconds
  }
`);

export const TTradeSignalSnapshotFieldsFragment = gql(`
  fragment Portfolio_TTradeSignalSnapshotFields on TTradeSignalSnapshot {
    instrumentCode
    tradeDate
    evaluatedAt
    sourceAt
    sourceTimeMs
    tickOrdinal
    continuityGeneration
    dataAgeMs
    windowCoverageSeconds
    sampleCount
    dataHealth
    dataHealthReasons {
      code
      label
      detail
    }
    pullbackPhase
    momentumPhase
    dominantPhase
    selectedPath
    pullbackScore
    momentumScore
    opportunityScore
    previewThreshold
    candidateThreshold
    revalidateThreshold
    rearmThreshold
    features {
      sampleCount
      coverageSeconds
      maxGapSeconds
      price
      priceTick
      bidPrice
      askPrice
      spreadTicks
      spreadPct
      bookImbalance
      sessionVwap
      vwapPremiumPct
      return5sPct
      return15sPct
      return30sPct
      return60sPct
      return300sPct
      priceSlope60sPctPerSecond
      priceAccelerationPctPerSecond2
      realizedVolatility60sPct
      realizedVolatility300sPct
      windowHigh
      windowLow
      pullbackPct
      reboundPct
      secondsSinceLow
      reboundSlopePctPerSecond
      rangePosition
      amountVelocityRatio15s60s
      momentumRisePct
      momentumMoveSeconds
      momentumWindowHigh
      momentumRangePosition
      momentumBaselineCoverageSeconds
      momentumAmountVelocityRatio
    }
    pullback {
      phase
      score
      preview
      candidateReady
      hardGates {
        code
        label
        passed
        observedValue
        requiredValue
        detail
      }
      scoreContributions {
        code
        label
        points
        maxPoints
        observedValue
        targetValue
        detail
      }
      blockers {
        code
        label
        detail
      }
    }
    momentum {
      phase
      score
      preview
      candidateReady
      hardGates {
        code
        label
        passed
        observedValue
        requiredValue
        detail
      }
      scoreContributions {
        code
        label
        points
        maxPoints
        observedValue
        targetValue
        detail
      }
      blockers {
        code
        label
        detail
      }
    }
    hardGates {
      code
      label
      passed
      observedValue
      requiredValue
      detail
    }
    scoreContributions {
      code
      label
      points
      maxPoints
      observedValue
      targetValue
      detail
    }
    topBlockers {
      code
      label
      detail
    }
    episodeId
    candidateId
    candidateFingerprint
    candidateStatus
    candidateCreatedAt
    candidateExpiresAt
    pendingEntryIntentId
    signalVersion
    candidateStateVersion
    stateSchemaVersion
    featureSchemaVersion
    policyVersion
    configVersion
    profileVersion
    profileFingerprint
  }
`);

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
      signalPolicy {
        ...Portfolio_TTradeSignalPolicyFields
      }
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
          plannedEntryAmount
          signalSnapshot {
            ...Portfolio_TTradeSignalSnapshotFields
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
        plannedEntryAmount
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
        signalSnapshot {
          ...Portfolio_TTradeSignalSnapshotFields
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

export const TTradeSignalEvaluationsQuery = gql(`
  query Portfolio_TTradeSignalEvaluations(
    $accountId: String!
    $stockCode: String
    $eventKinds: [TTradeSignalEvaluationKind!]
    $startTime: DateTime
    $endTime: DateTime
    $first: Int!
    $after: String
  ) {
    tTradeSignalEvaluations(
      accountId: $accountId
      stockCode: $stockCode
      eventKinds: $eventKinds
      startTime: $startTime
      endTime: $endTime
      first: $first
      after: $after
    ) {
      items {
        id
        accountId
        runId
        stockCode
        eventKind
        eventType
        evaluatedAt
        windowStartedAt
        windowEndedAt
        coalescedCount
        policyVersion
        schemaVersion
        contentFingerprint
        signalSnapshot {
          ...Portfolio_TTradeSignalSnapshotFields
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
`);

export const TTradeSignalDiagnosticsQuery = gql(`
  query Portfolio_TTradeSignalDiagnostics(
    $accountId: String!
    $stockCode: String
    $startTime: DateTime!
    $endTime: DateTime!
    $mergeVersions: Boolean! = false
  ) {
    tTradeSignalDiagnostics(
      accountId: $accountId
      stockCode: $stockCode
      startTime: $startTime
      endTime: $endTime
      mergeVersions: $mergeVersions
    ) {
      available
      reasonCode
      reason
      accountId
      stockCode
      startTime
      endTime
      mergedVersions
      warnings
      partitions {
        policyVersion
        featureSchemaVersion
        profileVersion
        denominator {
          code
          label
          readyInstrumentSeconds
        }
        funnel {
          code
          label
          unitCode
          denominatorCode
          count
          conversionRate
        }
        blockers {
          blocker {
            code
            label
            detail
          }
          count
          rate
          denominatorCode
          denominatorValue
        }
        scoreDistribution {
          policyVersion
          featureSchemaVersion
          profileVersion
          path
          lowerBound
          upperBound
          count
        }
        fsmDwell {
          branch
          phase
          durationSeconds
          transitionCount
        }
        fsmTransitions {
          branch
          fromPhase
          toPhase
          count
        }
        candidateOutcomes {
          code
          label
          count
        }
        postCandidatePerformance {
          available
          reasonCode
          reason
          sampleCount
          netMfePct
          netMaePct
          fixedWindowReturns {
            windowSeconds
            sampleCount
            averageNetReturnPct
          }
          requiredDataCodes
        }
      }
      versionGroups {
        policyVersion
        featureSchemaVersion
        profileVersion
        count
      }
    }
  }
`);

export const TTradeCandidateTraceQuery = gql(`
  query Portfolio_TTradeCandidateTrace(
    $accountId: String!
    $strategyRunId: String!
    $candidateId: String!
  ) {
    tTradeCandidateTrace(
      accountId: $accountId
      strategyRunId: $strategyRunId
      candidateId: $candidateId
    ) {
      accountId
      candidateId
      strategyRunId
      instrumentCode
      sourceEvaluationId
      sourceIdentity {
        sourceTimeMs
        tickOrdinal
        continuityGeneration
        tradeDate
        candidateFingerprint
        policyVersion
        featureSchemaVersion
        profileVersion
      }
      integrityStatus
      missingReasons {
        code
        stage
        expected
        detail
      }
      links {
        evaluationIds
        intentIds
        clientOrderIds
        correlationIds
        brokerOrderIds
        orderIds
        tradeIds
        batchIds
        exitPlanIds
        exitPlanEventIds
      }
      events {
        stage
        eventType
        entityId
        occurredAt
        status
        relatedIds
        details
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

export const RecordTTradeClientTelemetryMutation = gql(`
  mutation Portfolio_RecordTTradeClientTelemetry(
    $accountId: String!
    $refreshSuccess: Boolean!
    $refreshFailure: Boolean!
    $subscriptionReconnected: Boolean!
  ) {
    refreshSuccess: recordTTradeClientTelemetry(
      input: {
        accountId: $accountId
        platform: WEB
        event: REFRESH_SUCCESS
        surface: T_TRADE_SIGNAL_V3
      }
    ) @include(if: $refreshSuccess) {
      accepted
    }
    refreshFailure: recordTTradeClientTelemetry(
      input: {
        accountId: $accountId
        platform: WEB
        event: REFRESH_FAILURE
        surface: T_TRADE_SIGNAL_V3
      }
    ) @include(if: $refreshFailure) {
      accepted
    }
    subscriptionReconnected: recordTTradeClientTelemetry(
      input: {
        accountId: $accountId
        platform: WEB
        event: SUBSCRIPTION_RECONNECTED
        surface: T_TRADE_SIGNAL_V3
      }
    ) @include(if: $subscriptionReconnected) {
      accepted
    }
  }
`);

export const ApproveTTradeEntryV3Mutation = gql(`
  mutation Portfolio_ApproveTTradeEntryV3(
    $runId: String!
    $intentId: String!
    $expectation: TTradeCandidateApprovalExpectationInput!
    $idempotencyKey: String!
  ) {
    approveTTradeEntry(
      runId: $runId
      intentId: $intentId
      expectation: $expectation
      idempotencyKey: $idempotencyKey
    ) {
      success
      code
      message
      session {
        runId
        status
      }
    }
  }
`);

export const RejectTTradeEntryV3Mutation = gql(`
  mutation Portfolio_RejectTTradeEntryV3($runId: String!, $intentId: String!) {
    rejectTTradeEntry(runId: $runId, intentId: $intentId) {
      success
      code
      message
      session {
        runId
        status
      }
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

export const ActivateTTradeLiveMutation = gql(`
  mutation Portfolio_ActivateTTradeLive(
    $accountId: String!
    $policyVersion: Int!
    $snapshotId: String!
    $idempotencyKey: String!
    $targetStage: TTradeRolloutTarget!
    $confirmation: String!
  ) {
    activateTTradeLive(
      accountId: $accountId
      policyVersion: $policyVersion
      snapshotId: $snapshotId
      idempotencyKey: $idempotencyKey
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
      monitor {
        accountId
        configVersion
        signalPolicy {
          ...Portfolio_TTradeSignalPolicyFields
        }
      }
    }
  }
`);

export const PreviewTTradeSignalPolicyMutation = gql(`
  mutation Portfolio_PreviewTTradeSignalPolicy(
    $input: TTradeSignalPolicyPreviewInput!
  ) {
    previewTTradeSignalPolicy(input: $input) {
      valid
      configVersion
      errors {
        code
        field
        message
      }
      warnings {
        code
        field
        message
      }
      normalizedPolicy {
        ...Portfolio_TTradeSignalPolicyFields
      }
      changedFields
      requiresRewarm
    }
  }
`);

export const ReconcileTTradeGlobalMonitorMutation = gql(`
  mutation Portfolio_ReconcileTTradeGlobalMonitor(
    $accountId: String!
    $idempotencyKey: String!
  ) {
    reconcileTTradeGlobalMonitor(
      accountId: $accountId
      idempotencyKey: $idempotencyKey
    ) {
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
      revision
      processedUntil
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
      revision
      processedUntil
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

export const TTradeReplayUpdatesSubscription = gql(`
  subscription Portfolio_TTradeReplayUpdates($accountId: String!) {
    tTradeReplayUpdates(accountId: $accountId) {
      accountId
      runId
      revision
      kind
      occurredAt
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
