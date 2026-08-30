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
      let signals = holdings.compactMap { $0.session?.signalSnapshot }
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
          $0.evaluatedAt > $1.evaluatedAt
        },
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
      let signalSnapshot = try session.signalSnapshot.map { snapshot in
        try ReadOnlyModelValidator.requireNonempty(
          snapshot.instrumentCode,
          field: "tTrade.signal.instrumentCode"
        )
        guard snapshot.instrumentCode == value.stockCode else {
          throw ReadOnlyMappingError.invalidField("tTrade.signal.instrumentCode")
        }
        try ReadOnlyModelValidator.requireFinite(
          [
            snapshot.opportunityScore,
            snapshot.features.price,
            snapshot.features.pullbackPct,
            snapshot.features.reboundPct,
          ].compactMap { $0 } + [snapshot.candidateThreshold],
          field: "tTrade.signal.values"
        )
        try ReadOnlyModelValidator.requireNonnegative(
          [snapshot.signalVersion, snapshot.candidateStateVersion, snapshot.configVersion],
          field: "tTrade.signal.versions"
        )
        let evaluatedAt = try ReadOnlyModelValidator.requireDate(
          snapshot.evaluatedAt,
          field: "tTrade.signal.evaluatedAt"
        )
        let sourceAt = try ReadOnlyModelValidator.requireDate(
          snapshot.sourceAt,
          field: "tTrade.signal.sourceAt"
        )
        let candidateExpiresAt = snapshot.candidateExpiresAt.flatMap(PortfolioDateParser.parse)
        let candidateStatus = TTradeCandidateStatus(
          serverValue: snapshot.candidateStatus.value?.rawValue
        )
        let dataHealth = snapshot.dataHealth.value?.rawValue ?? "UNKNOWN"
        let dominantPhase = snapshot.dominantPhase.value?.rawValue ?? "UNKNOWN"
        let candidateID = normalizedIdentity(snapshot.candidateId, maximumLength: 160)
        let candidateFingerprint = normalizedIdentity(
          snapshot.candidateFingerprint,
          maximumLength: 256
        )
        let pendingEntryIntentID = normalizedIdentity(
          snapshot.pendingEntryIntentId,
          maximumLength: 160
        )
        let sessionPendingEntryIntentID = normalizedIdentity(
          session.pendingEntryIntentId,
          maximumLength: 160
        )
        let policyVersion = normalizedIdentity(snapshot.policyVersion, maximumLength: 160)
        let stateSchemaVersion = normalizedIdentity(
          snapshot.stateSchemaVersion,
          maximumLength: 40
        )
        let featureSchemaVersion = normalizedIdentity(
          snapshot.featureSchemaVersion,
          maximumLength: 40
        )
        var compatibilityIssues: [String] = []
        if case .unknown = candidateStatus { compatibilityIssues.append("候选状态未知") }
        if dataHealth == "UNKNOWN" { compatibilityIssues.append("数据健康状态未知") }
        if dominantPhase == "UNKNOWN" { compatibilityIssues.append("主导阶段未知") }
        if stateSchemaVersion != "3" || featureSchemaVersion != "1" {
          compatibilityIssues.append("信号协议版本不兼容")
        }
        if policyVersion == nil { compatibilityIssues.append("策略版本缺失") }
        if sessionPendingEntryIntentID != pendingEntryIntentID {
          compatibilityIssues.append("待确认意图上下文不一致")
        }
        if candidateStatus == .awaitingApproval {
          if candidateID == nil || candidateFingerprint == nil || pendingEntryIntentID == nil {
            compatibilityIssues.append("候选审批身份不完整")
          }
          if snapshot.candidateStateVersion <= 0 {
            compatibilityIssues.append("候选状态版本无效")
          }
          if candidateExpiresAt == nil {
            compatibilityIssues.append("候选到期时间缺失或无效")
          }
        }
        let compatibilityMessage = compatibilityIssues.isEmpty
          ? nil
          : "\(compatibilityIssues.joined(separator: "、"))，当前信号保持只读"
        let approvalExpectation: TTradeCandidateApprovalExpectation?
        if
          compatibilityMessage == nil,
          candidateStatus == .awaitingApproval,
          let candidateID,
          let candidateFingerprint,
          pendingEntryIntentID != nil,
          let policyVersion
        {
          approvalExpectation = TTradeCandidateApprovalExpectation(
            signalVersion: snapshot.signalVersion,
            candidateID: candidateID,
            candidateFingerprint: candidateFingerprint,
            candidateStateVersion: snapshot.candidateStateVersion,
            configVersion: snapshot.configVersion,
            policyVersion: policyVersion
          )
        } else {
          approvalExpectation = nil
        }
        let firstBlocker = try snapshot.topBlockers.first.map { blocker in
          try ReadOnlyModelValidator.requireNonempty(
            blocker.code,
            field: "tTrade.signal.blocker.code"
          )
          return TTradeSignalBlocker(
            code: String(blocker.code.prefix(120)),
            label: String(blocker.label.prefix(160)),
            detail: String(blocker.detail.prefix(500))
          )
        }
        return TTradeSignalItem(
          id: session.runId,
          runID: session.runId,
          stockCode: value.stockCode,
          candidateStatus: candidateStatus,
          signalPrice: snapshot.features.price,
          pullbackPercent: snapshot.features.pullbackPct,
          reboundPercent: snapshot.features.reboundPct,
          opportunityScore: snapshot.opportunityScore,
          candidateThreshold: snapshot.candidateThreshold,
          dataHealth: dataHealth,
          dominantPhase: dominantPhase,
          firstBlocker: firstBlocker,
          sourceAt: sourceAt,
          evaluatedAt: evaluatedAt,
          candidateExpiresAt: candidateExpiresAt,
          pendingEntryIntentID: pendingEntryIntentID,
          approvalExpectation: approvalExpectation,
          compatibilityMessage: compatibilityMessage
        )
      }
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
        errorMessage: session.errorMessage,
        signalSnapshot: signalSnapshot
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

  private func normalizedIdentity(_ value: String?, maximumLength: Int) -> String? {
    guard let value else { return nil }
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      !normalized.isEmpty,
      normalized == value,
      normalized.count <= maximumLength
    else { return nil }
    return normalized
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
