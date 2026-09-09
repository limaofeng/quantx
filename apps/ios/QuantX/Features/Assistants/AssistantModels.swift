import Foundation

enum TradeApprovalKind: String, Equatable, Sendable {
  case tTradeEntry = "T_TRADE_ENTRY_APPROVAL"
  case strategyTradeIntent = "STRATEGY_TRADE_INTENT_APPROVAL"
}

struct TTradeCandidateApprovalExpectation: Equatable, Hashable, Sendable {
  let signalVersion: Int
  let candidateID: String
  let candidateFingerprint: String
  let candidateStateVersion: Int
  let configVersion: Int
  let policyVersion: String
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
  let tTradeExpectation: TTradeCandidateApprovalExpectation?

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

enum TTradeCandidateStatus: Equatable, Hashable, Sendable {
  case none
  case latched
  case awaitingApproval
  case suppressed
  case rearming
  case unknown(String)

  init(serverValue: String?) {
    let normalized = (serverValue ?? "")
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    switch normalized {
    case "NONE": self = .none
    case "LATCHED": self = .latched
    case "AWAITING_APPROVAL": self = .awaitingApproval
    case "SUPPRESSED": self = .suppressed
    case "REARMING": self = .rearming
    default: self = .unknown(normalized)
    }
  }

  var title: String {
    switch self {
    case .none: "观察中"
    case .latched: "候选已锁存"
    case .awaitingApproval: "等待确认"
    case .suppressed: "候选已抑制"
    case .rearming: "等待再武装"
    case .unknown: "未知候选状态"
    }
  }
}

struct TTradeSignalBlocker: Equatable, Hashable, Sendable {
  let code: String
  let label: String
  let detail: String
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
  let signalSnapshot: TTradeSignalItem?
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
  let lastPrice: Double?
  let lastNetProfitPercent: Double?
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
  let candidateStatus: TTradeCandidateStatus
  let signalPrice: Double?
  let pullbackPercent: Double?
  let reboundPercent: Double?
  let opportunityScore: Double?
  let candidateThreshold: Double
  let dataHealth: String
  let dominantPhase: String
  let firstBlocker: TTradeSignalBlocker?
  let sourceAt: Date
  let evaluatedAt: Date
  let candidateExpiresAt: Date?
  let pendingEntryIntentID: String?
  let approvalExpectation: TTradeCandidateApprovalExpectation?
  let compatibilityMessage: String?

  func approvalUnavailableReason(at now: Date = Date()) -> String? {
    if let compatibilityMessage { return compatibilityMessage }
    guard candidateStatus == .awaitingApproval else { return "当前候选不在等待确认状态" }
    guard pendingEntryIntentID != nil, approvalExpectation != nil else {
      return "候选审批身份不完整，请等待服务端刷新"
    }
    guard let candidateExpiresAt, candidateExpiresAt > now else {
      return "候选已过期，请刷新后等待新的服务端信号"
    }
    return nil
  }
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
