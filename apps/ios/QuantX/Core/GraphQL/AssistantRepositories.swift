import Apollo
import Foundation

@MainActor
protocol TTradeAssistantLoading: AnyObject {
  func load(accountID: String) async throws -> TTradeAssistantSnapshot
}

@MainActor
protocol LimitUpBoardLoading: AnyObject {
  func load(runID: String) async throws -> LimitUpBoardSnapshot
}

@MainActor
final class TTradeAssistantRepository: TTradeAssistantLoading {
  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func load(accountID: String) async throws -> TTradeAssistantSnapshot {
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSTTradeAssistantQuery(accountId: accountID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let monitor = data.tTradeGlobalMonitor
      guard monitor.accountId == accountID else {
        throw ReadOnlyRepositoryError.accountScopeMismatch
      }

      try ReadOnlyModelValidator.requireNonnegative(
        [
          monitor.holdingCount,
          monitor.eligibleCount,
          monitor.ignoredCount,
          monitor.monitoredCount,
          monitor.pendingSignalCount,
          monitor.activeBatchCount,
          monitor.drainingCount,
        ],
        field: "tTrade.monitor.counts"
      )

      let holdings = try monitor.holdings.map(mapHolding)
      let batches = try data.tTradeBatchesPage.items.map { item in
        guard item.accountId == accountID else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        try ReadOnlyModelValidator.requireNonempty(item.batchId, field: "tTrade.batch.id")
        try ReadOnlyModelValidator.requireNonempty(item.stockCode, field: "tTrade.batch.stockCode")
        try ReadOnlyModelValidator.requireNonnegative(
          [item.targetVolume, item.entryFilledVolume, item.exitFilledVolume, item.activeVolume],
          field: "tTrade.batch.volume"
        )
        try ReadOnlyModelValidator.requireFinite(
          [
            item.entryAvgPrice,
            item.exitAvgPrice,
            item.lastPrice,
            item.lastNetProfitPct,
            item.peakNetProfitPct,
          ] + [item.trailingFloorPct].compactMap { $0 },
          field: "tTrade.batch.price"
        )
        return TTradeBatchItem(
          id: item.batchId,
          accountID: item.accountId,
          stockCode: item.stockCode,
          status: item.status,
          targetVolume: item.targetVolume,
          entryFilledVolume: item.entryFilledVolume,
          entryAveragePrice: item.entryAvgPrice,
          exitFilledVolume: item.exitFilledVolume,
          exitAveragePrice: item.exitAvgPrice,
          activeVolume: item.activeVolume,
          lastPrice: item.lastPrice,
          lastNetProfitPercent: item.lastNetProfitPct,
          peakNetProfitPercent: item.peakNetProfitPct,
          trailingFloorPercent: item.trailingFloorPct,
          exitReason: item.exitReason,
          exceptionReason: item.exceptionReason,
          createdAt: item.createdAt.flatMap(PortfolioDateParser.parse),
          updatedAt: item.updatedAt.flatMap(PortfolioDateParser.parse)
        )
      }

      let signals = try data.tTradeSignalHistoryPage.items.map { item in
        try ReadOnlyModelValidator.requireNonempty(item.intentId, field: "tTrade.signal.id")
        try ReadOnlyModelValidator.requireNonempty(item.runId, field: "tTrade.signal.runId")
        try ReadOnlyModelValidator.requireNonempty(item.stockCode, field: "tTrade.signal.stockCode")
        try ReadOnlyModelValidator.requireNonnegative(
          [item.requestedVolume],
          field: "tTrade.signal.volume"
        )
        try ReadOnlyModelValidator.requireFinite(
          [item.signalPrice, item.pullbackPct, item.reboundPct],
          field: "tTrade.signal.price"
        )
        return TTradeSignalItem(
          id: item.intentId,
          runID: item.runId,
          stockCode: item.stockCode,
          status: item.status,
          statusReason: item.statusReason,
          signalPrice: item.signalPrice,
          pullbackPercent: item.pullbackPct,
          reboundPercent: item.reboundPct,
          requestedVolume: item.requestedVolume,
          createdAt: item.createdAt.flatMap(PortfolioDateParser.parse),
          expiresAt: item.expiresAt.flatMap(PortfolioDateParser.parse),
          updatedAt: item.updatedAt.flatMap(PortfolioDateParser.parse)
        )
      }

      let readiness = try monitor.readiness.map { value in
        guard value.accountId == accountID else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        return TTradeReadiness(
          ready: value.ready,
          stage: value.stage,
          engineStatus: value.engineStatus,
          agentStatus: value.agentStatus,
          reconcileStatus: value.reconcileStatus,
          killSwitch: value.killSwitch,
          policyVersion: value.policyVersion,
          canApprove: value.canApprove,
          canActivateLive: value.canActivateLive,
          blockedReasons: value.blockedReasons,
          checkedAt: try ReadOnlyModelValidator.requireDate(
            value.checkedAt,
            field: "tTrade.readiness.checkedAt"
          ),
          checks: value.checks.map {
            TTradeReadinessCheck(code: $0.code, passed: $0.passed, message: $0.message)
          }
        )
      }

      return TTradeAssistantSnapshot(
        accountID: accountID,
        enabled: monitor.enabled,
        mode: monitor.mode,
        holdingCount: monitor.holdingCount,
        eligibleCount: monitor.eligibleCount,
        ignoredCount: monitor.ignoredCount,
        monitoredCount: monitor.monitoredCount,
        pendingSignalCount: monitor.pendingSignalCount,
        activeBatchCount: monitor.activeBatchCount,
        drainingCount: monitor.drainingCount,
        lastReconciledAt: monitor.lastReconciledAt.flatMap(PortfolioDateParser.parse),
        lastError: monitor.lastError,
        updatedAt: monitor.updatedAt.flatMap(PortfolioDateParser.parse),
        positionSnapshotComplete: monitor.positionSnapshotComplete,
        positionSnapshotError: monitor.positionSnapshotError,
        rolloutStage: monitor.rolloutStage,
        engineStatus: monitor.engineStatus,
        agentStatus: monitor.agentStatus,
        reconcileStatus: monitor.reconcileStatus,
        killSwitch: monitor.killSwitch,
        canApprove: monitor.canApprove,
        canActivateLive: monitor.canActivateLive,
        blockedReasons: monitor.blockedReasons,
        projectionGeneratedAt: monitor.projectionGeneratedAt.flatMap(PortfolioDateParser.parse),
        readiness: readiness,
        holdings: holdings.sorted { $0.stockCode < $1.stockCode },
        batches: batches.sorted {
          ($0.updatedAt ?? $0.createdAt ?? .distantPast)
            > ($1.updatedAt ?? $1.createdAt ?? .distantPast)
        },
        batchesHaveMore: data.tTradeBatchesPage.pageInfo.hasNextPage,
        signals: signals.sorted {
          ($0.createdAt ?? .distantPast) > ($1.createdAt ?? .distantPast)
        },
        signalsHaveMore: data.tTradeSignalHistoryPage.pageInfo.hasNextPage,
        fetchedAt: Date()
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch is ReadOnlyMappingError {
      throw ReadOnlyRepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }

  private func mapHolding(
    _ value: QuantXAPI.IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding
  ) throws -> TTradeHolding {
    try ReadOnlyModelValidator.requireNonempty(value.stockCode, field: "tTrade.holding.stockCode")
    try ReadOnlyModelValidator.requireNonnegative(
      [value.volume, value.availableVolume],
      field: "tTrade.holding.volume"
    )
    guard value.availableVolume <= value.volume else {
      throw ReadOnlyMappingError.invalidField("tTrade.holding.availableVolume")
    }
    let session = try value.session.map { session in
      try ReadOnlyModelValidator.requireNonempty(session.runId, field: "tTrade.session.runId")
      try ReadOnlyModelValidator.requireNonnegative(
        [
          session.activeVolume,
          session.completedCycles,
          session.entryFilledVolume,
          session.exitFilledVolume,
        ],
        field: "tTrade.session.volume"
      )
      try ReadOnlyModelValidator.requireFinite(
        [
          session.lastPrice,
          session.lastNetProfitPct,
          session.peakNetProfitPct,
          session.entryAvgPrice,
          session.exitAvgPrice,
        ] + [session.trailingFloorPct].compactMap { $0 },
        field: "tTrade.session.price"
      )
      return TTradeHoldingSession(
        runID: session.runId,
        runStatus: session.runStatus,
        status: session.status,
        mode: session.mode,
        activeVolume: session.activeVolume,
        lastPrice: session.lastPrice,
        lastNetProfitPercent: session.lastNetProfitPct,
        peakNetProfitPercent: session.peakNetProfitPct,
        trailingFloorPercent: session.trailingFloorPct,
        completedCycles: session.completedCycles,
        pendingEntryIntentID: session.pendingEntryIntentId,
        pendingExitIntentID: session.pendingExitIntentId,
        entryOrderStatus: session.entryOrderStatus,
        exitOrderStatus: session.exitOrderStatus,
        entryFilledVolume: session.entryFilledVolume,
        entryAveragePrice: session.entryAvgPrice,
        exitFilledVolume: session.exitFilledVolume,
        exitAveragePrice: session.exitAvgPrice,
        profitArmed: session.profitArmed,
        lastExitReason: session.lastExitReason,
        canCancel: session.canCancel,
        errorMessage: session.errorMessage
      )
    }
    return TTradeHolding(
      stockCode: value.stockCode,
      instrumentName: value.instrumentName,
      volume: value.volume,
      availableVolume: value.availableVolume,
      ignored: value.ignored,
      eligible: value.eligible,
      status: value.status,
      reason: value.reason,
      session: session
    )
  }
}

@MainActor
final class LimitUpBoardRepository: LimitUpBoardLoading {
  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func load(runID: String) async throws -> LimitUpBoardSnapshot {
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSLimitUpBoardAssistantQuery(runId: runID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ReadOnlyRepositoryError.invalidResponse
      }

      let approvals = try data.strategyPendingTradeIntents.map { item in
        guard item.runId == runID else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        try ReadOnlyModelValidator.requireNonempty(item.id, field: "limitUp.intent.id")
        try ReadOnlyModelValidator.requireNonempty(
          item.instrumentCode,
          field: "limitUp.intent.instrumentCode"
        )
        try ReadOnlyModelValidator.requireFinite(
          [item.confidence]
            + [item.limitPriceHint, item.targetPositionPct, item.targetAmount, item.signalPrice,
              item.limitUpPrice, item.distanceToLimitTicks].compactMap { $0 },
          field: "limitUp.intent.number"
        )
        if let targetVolume = item.targetVolume {
          try ReadOnlyModelValidator.requireNonnegative(
            [targetVolume],
            field: "limitUp.intent.targetVolume"
          )
        }
        return LimitUpApprovalIntent(
          id: item.id,
          runID: item.runId,
          instrumentCode: item.instrumentCode,
          side: item.side,
          bucket: item.bucket,
          reason: item.reason,
          status: item.status,
          executionMode: item.executionMode,
          confidence: item.confidence,
          limitPriceHint: item.limitPriceHint,
          targetPositionPercent: item.targetPositionPct,
          targetAmount: item.targetAmount,
          targetVolume: item.targetVolume,
          signalPrice: item.signalPrice,
          limitUpPrice: item.limitUpPrice,
          distanceToLimitTicks: item.distanceToLimitTicks,
          approvalExpiresAt: item.approvalExpiresAt.flatMap(PortfolioDateParser.parse),
          createdAt: item.createdAt.flatMap(PortfolioDateParser.parse)
        )
      }

      let exitPlans = try data.strategyExitPlans.map { plan in
        try ReadOnlyModelValidator.requireNonempty(plan.id, field: "limitUp.exitPlan.id")
        try ReadOnlyModelValidator.requireNonempty(
          plan.instrumentCode,
          field: "limitUp.exitPlan.instrumentCode"
        )
        try ReadOnlyModelValidator.requireNonnegative(
          [
            plan.entryFilledVolume,
            plan.exitedVolume,
            plan.remainingVolume,
            plan.holdingTradingDays,
          ],
          field: "limitUp.exitPlan.volume"
        )
        try ReadOnlyModelValidator.requireFinite(
          [
            plan.entryAvgPrice,
            plan.exitAvgPrice,
            plan.peakPrice,
            plan.lastPrice,
            plan.lastNetProfitPct,
            plan.peakNetProfitPct,
          ],
          field: "limitUp.exitPlan.price"
        )
        return LimitUpExitPlan(
          id: plan.id,
          instrumentCode: plan.instrumentCode,
          sourceType: plan.sourceType,
          bucket: plan.bucket,
          status: plan.status,
          entryFilledVolume: plan.entryFilledVolume,
          entryAveragePrice: plan.entryAvgPrice,
          exitedVolume: plan.exitedVolume,
          exitAveragePrice: plan.exitAvgPrice,
          remainingVolume: plan.remainingVolume,
          peakPrice: plan.peakPrice,
          lastPrice: plan.lastPrice,
          lastNetProfitPercent: plan.lastNetProfitPct,
          peakNetProfitPercent: plan.peakNetProfitPct,
          holdingTradingDays: plan.holdingTradingDays,
          pendingIntentID: plan.pendingIntentId,
          pendingOrderID: plan.pendingOrderId,
          lastExitReason: plan.lastExitReason,
          t1Policy: plan.t1Policy,
          executionMode: plan.executionMode,
          autoExitAuthorized: plan.autoExitAuthorized,
          ruleTypes: plan.ruleTypes
        )
      }

      return LimitUpBoardSnapshot(
        runID: runID,
        approvals: approvals.sorted {
          ($0.approvalExpiresAt ?? .distantFuture) < ($1.approvalExpiresAt ?? .distantFuture)
        },
        exitPlans: exitPlans,
        fetchedAt: Date()
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch is ReadOnlyMappingError {
      throw ReadOnlyRepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }
}
