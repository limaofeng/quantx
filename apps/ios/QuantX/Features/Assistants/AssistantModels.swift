import Foundation

enum TradeApprovalKind: String, Equatable, Sendable {
  case tTradeEntry = "T_TRADE_ENTRY_APPROVAL"
  case strategyTradeIntent = "STRATEGY_TRADE_INTENT_APPROVAL"
}

struct TradeApprovalPreview: Equatable, Identifiable, Sendable {
  let id: String
  let confirmationToken: String
  let kind: TradeApprovalKind
  let accountID: String
  let runID: String
  let intentID: String
  let instrumentCode: String
  let side: String
  let bucket: String
  let reason: String
  let targetVolume: Int?
  let referencePrice: Double?
  let estimatedAmount: Double?
  let signalExpiresAt: Date?
  let challengeExpiresAt: Date
  let warnings: [String]

  func isExpired(at date: Date = Date()) -> Bool {
    challengeExpiresAt <= date
  }
}

struct TradeApprovalConfirmation: Equatable, Sendable {
  let success: Bool
  let code: String
  let message: String
  let challengeID: String
}

struct TTradeReadinessCheck: Equatable, Hashable, Identifiable, Sendable {
  let code: String
  let passed: Bool
  let message: String

  var id: String { code }
}

struct TTradeReadiness: Equatable, Sendable {
  let ready: Bool
  let stage: String
  let engineStatus: String
  let agentStatus: String
  let reconcileStatus: String
  let killSwitch: Bool
  let policyVersion: Int
  let canApprove: Bool
  let canActivateLive: Bool
  let blockedReasons: [String]
  let checkedAt: Date
  let checks: [TTradeReadinessCheck]
}

struct TTradeHoldingSession: Equatable, Sendable {
  let runID: String
  let runStatus: String
  let status: String
  let mode: String
  let activeVolume: Int
  let lastPrice: Double
  let lastNetProfitPercent: Double
  let peakNetProfitPercent: Double
  let trailingFloorPercent: Double?
  let completedCycles: Int
  let pendingEntryIntentID: String?
  let pendingExitIntentID: String?
  let entryOrderStatus: String
  let exitOrderStatus: String
  let entryFilledVolume: Int
  let entryAveragePrice: Double
  let exitFilledVolume: Int
  let exitAveragePrice: Double
  let profitArmed: Bool
  let lastExitReason: String
  let canCancel: Bool
  let errorMessage: String?
}

struct TTradeHolding: Equatable, Identifiable, Sendable {
  let stockCode: String
  let instrumentName: String
  let volume: Int
  let availableVolume: Int
  let ignored: Bool
  let eligible: Bool
  let status: String
  let reason: String
  let session: TTradeHoldingSession?

  var id: String { stockCode }
}

struct TTradeBatchItem: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let accountID: String
  let stockCode: String
  let status: String
  let targetVolume: Int
  let entryFilledVolume: Int
  let entryAveragePrice: Double
  let exitFilledVolume: Int
  let exitAveragePrice: Double
  let activeVolume: Int
  let lastPrice: Double
  let lastNetProfitPercent: Double
  let peakNetProfitPercent: Double
  let trailingFloorPercent: Double?
  let exitReason: String?
  let exceptionReason: String?
  let createdAt: Date?
  let updatedAt: Date?
}

struct TTradeSignalItem: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let runID: String
  let stockCode: String
  let status: String
  let statusReason: String
  let signalPrice: Double
  let pullbackPercent: Double
  let reboundPercent: Double
  let requestedVolume: Int
  let createdAt: Date?
  let expiresAt: Date?
  let updatedAt: Date?
}

struct TTradeAssistantSnapshot: Equatable, Sendable {
  let accountID: String
  let enabled: Bool
  let mode: String
  let holdingCount: Int
  let eligibleCount: Int
  let ignoredCount: Int
  let monitoredCount: Int
  let pendingSignalCount: Int
  let activeBatchCount: Int
  let drainingCount: Int
  let lastReconciledAt: Date?
  let lastError: String?
  let updatedAt: Date?
  let positionSnapshotComplete: Bool
  let positionSnapshotError: String?
  let rolloutStage: String
  let engineStatus: String
  let agentStatus: String
  let reconcileStatus: String
  let killSwitch: Bool
  let canApprove: Bool
  let canActivateLive: Bool
  let blockedReasons: [String]
  let projectionGeneratedAt: Date?
  let readiness: TTradeReadiness?
  let holdings: [TTradeHolding]
  let batches: [TTradeBatchItem]
  let batchesHaveMore: Bool
  let signals: [TTradeSignalItem]
  let signalsHaveMore: Bool
  let fetchedAt: Date
}

enum TTradeAssistantState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case noAccount
  case loaded(TTradeAssistantSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: TTradeAssistantSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

struct LimitUpApprovalIntent: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let runID: String
  let instrumentCode: String
  let side: String
  let bucket: String
  let reason: String
  let status: String
  let executionMode: String
  let confidence: Double
  let limitPriceHint: Double?
  let targetPositionPercent: Double?
  let targetAmount: Double?
  let targetVolume: Int?
  let signalPrice: Double?
  let limitUpPrice: Double?
  let distanceToLimitTicks: Double?
  let approvalExpiresAt: Date?
  let createdAt: Date?
}

struct LimitUpExitPlan: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let instrumentCode: String
  let sourceType: String
  let bucket: String
  let status: String
  let entryFilledVolume: Int
  let entryAveragePrice: Double
  let exitedVolume: Int
  let exitAveragePrice: Double
  let remainingVolume: Int
  let peakPrice: Double
  let lastPrice: Double
  let lastNetProfitPercent: Double
  let peakNetProfitPercent: Double
  let holdingTradingDays: Int
  let pendingIntentID: String?
  let pendingOrderID: String?
  let lastExitReason: String?
  let t1Policy: String
  let executionMode: String
  let autoExitAuthorized: Bool
  let ruleTypes: [String]
}

struct LimitUpBoardSnapshot: Equatable, Sendable {
  let runID: String
  let approvals: [LimitUpApprovalIntent]
  let exitPlans: [LimitUpExitPlan]
  let fetchedAt: Date
}

enum LimitUpBoardState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case noStrategy
  case loaded(LimitUpBoardSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: LimitUpBoardSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

extension StrategyMonitorItem {
  var isLimitUpBoardStrategy: Bool {
    let searchable = [strategyKey, strategyName ?? "", displayName]
      .joined(separator: " ")
      .lowercased()
    return searchable.contains("打板")
      || searchable.contains("limit up")
      || searchable.contains("limit-up")
      || searchable.contains("limit_up")
  }
}
