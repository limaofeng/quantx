import Foundation

indirect enum ExitPlanStructuredValue: Equatable, Sendable {
  struct Field: Equatable, Identifiable, Sendable {
    let key: String
    let value: ExitPlanStructuredValue

    var id: String { key }
  }

  case null
  case boolean(Bool)
  case integer(Int)
  case number(Double)
  case string(String)
  case array([ExitPlanStructuredValue])
  case object([Field])

  init(graphQL value: GraphQLJSON) {
    switch value {
    case .null:
      self = .null
    case .boolean(let value):
      self = .boolean(value)
    case .integer(let value):
      self = .integer(value)
    case .number(let value):
      self = .number(value)
    case .string(let value):
      self = .string(value)
    case .array(let values):
      self = .array(values.map(Self.init(graphQL:)))
    case .object(let fields):
      self = .object(
        fields.map { Field(key: $0.key, value: Self(graphQL: $0.value)) }
      )
    }
  }

  var summary: String {
    switch self {
    case .null:
      "未设置"
    case .boolean(let value):
      value ? "是" : "否"
    case .integer(let value):
      value.formatted()
    case .number(let value):
      value.formatted(.number.precision(.fractionLength(0...6)))
    case .string(let value):
      value.isEmpty ? "空字符串" : value
    case .array(let values):
      values.isEmpty
        ? "空列表"
        : values.prefix(4).map(\.summary).joined(separator: "；")
          + (values.count > 4 ? "；…" : "")
    case .object(let fields):
      fields.isEmpty
        ? "空对象"
        : fields.prefix(4).map { "\($0.key)：\($0.value.summary)" }
          .joined(separator: "；") + (fields.count > 4 ? "；…" : "")
    }
  }

  var topLevelFields: [Field] {
    switch self {
    case .object(let fields):
      fields
    case .array(let values):
      values.enumerated().map { Field(key: "规则 \($0.offset + 1)", value: $0.element) }
    default:
      [Field(key: "值", value: self)]
    }
  }
}

enum ExitPlanExecutionMode: Equatable, Sendable {
  case paper
  case live
  case unknown(String)

  init(serverValue: String) {
    switch serverValue.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() {
    case "PAPER": self = .paper
    case "LIVE": self = .live
    case let value: self = .unknown(value)
    }
  }

  var title: String {
    switch self {
    case .paper: "模拟（PAPER）"
    case .live: "实盘（LIVE）"
    case .unknown(let value): "未知模式（\(value.isEmpty ? "空值" : value)）"
    }
  }
}

enum ExitPlanStatus: Equatable, Sendable {
  case active
  case paused
  case completed
  case cancelled
  case unknown(String)

  init(serverValue: String) {
    switch serverValue.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() {
    case "ACTIVE": self = .active
    case "PAUSED": self = .paused
    case "COMPLETED": self = .completed
    case "CANCELLED": self = .cancelled
    case let value: self = .unknown(value)
    }
  }

  var title: String {
    switch self {
    case .active: "监控中"
    case .paused: "已暂停"
    case .completed: "已完成"
    case .cancelled: "已取消"
    case .unknown(let value): "未知状态（\(value.isEmpty ? "空值" : value)）"
    }
  }

  var isAuthorizable: Bool {
    self == .active
  }
}

enum ExitPlanAuthorizationState: Equatable, Sendable {
  case notApplicable
  case authorized(expiresAt: Date)
  case expired(expiredAt: Date?)
  case staleVersion(authorizedVersion: Int?)
  case notAuthorized
  case unknownMode

  var title: String {
    switch self {
    case .notApplicable: "PAPER 无需实盘授权"
    case .authorized: "精确自动授权有效"
    case .expired: "自动授权已到期"
    case .staleVersion: "授权版本已失效"
    case .notAuthorized: "自动交易未授权"
    case .unknownMode: "未知执行模式，已阻断授权"
    }
  }
}

struct ExitPlanItem: Equatable, Identifiable, Sendable {
  let id: String
  let groupID: String?
  let accountID: String
  let instrumentCode: String
  let bucket: String
  let sourceType: String
  let sourceID: String
  let strategyRunID: String?
  let enabled: Bool
  let status: ExitPlanStatus
  let executionMode: ExitPlanExecutionMode
  let autoExitAuthorized: Bool
  let autoExitAuthorizationConfigVersion: Int?
  let autoExitAuthorizationExpiresAt: Date?
  let configVersion: Int
  let completionStrategy: String?
  let completionNote: String?
  let protectedVolume: Int
  let exitedVolume: Int
  let remainingVolume: Int
  let entryAveragePrice: Double
  let rules: ExitPlanStructuredValue
  let metadata: ExitPlanStructuredValue
  let canEditRules: Bool
  let editRoute: String?
  let phase: String
  let dataQuality: String
  let lastDecision: String?
  let peakPrice: Double
  let peakDrawdownPercent: Double
  let trailingFloorPercent: Double?
  let pendingClientOrderID: String?
  let pendingIntentID: String?
  let lastEvaluatedAt: Date?
  let lastError: String?
  let createdAt: Date?
  let updatedAt: Date?

  var authorizationState: ExitPlanAuthorizationState {
    authorizationState(at: Date())
  }

  func authorizationState(at now: Date) -> ExitPlanAuthorizationState {
    switch executionMode {
    case .paper:
      return .notApplicable
    case .unknown:
      return .unknownMode
    case .live:
      guard autoExitAuthorized else {
        if let expiresAt = autoExitAuthorizationExpiresAt, expiresAt <= now {
          return .expired(expiredAt: expiresAt)
        }
        return .notAuthorized
      }
      guard autoExitAuthorizationConfigVersion == configVersion else {
        return .staleVersion(authorizedVersion: autoExitAuthorizationConfigVersion)
      }
      guard let expiresAt = autoExitAuthorizationExpiresAt else {
        return .expired(expiredAt: nil)
      }
      guard expiresAt > now else { return .expired(expiredAt: expiresAt) }
      return .authorized(expiresAt: expiresAt)
    }
  }

  var progressFraction: Double {
    guard protectedVolume > 0 else { return 0 }
    return min(1, max(0, Double(exitedVolume) / Double(protectedVolume)))
  }
}

struct ExitPlanRuleCapability: Equatable, Identifiable, Sendable {
  let ruleType: String
  let label: String
  let category: String
  let parameters: ExitPlanStructuredValue

  var id: String { ruleType }
}

struct ExitPlanCapabilitiesSnapshot: Equatable, Sendable {
  let ruleTypes: [ExitPlanRuleCapability]
  let completionStrategies: [String]
  let conflictStrategies: [String]
  let executionModes: [String]
  let ruleSemantics: String
}

struct ExitPlanListSnapshot: Equatable, Sendable {
  let accountID: String
  let plans: [ExitPlanItem]
  let capabilities: ExitPlanCapabilitiesSnapshot
  let fetchedAt: Date
}

enum ExitPlanListState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case loaded(ExitPlanListSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: ExitPlanListSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

struct ExitPlanCapacityConflict: Equatable, Identifiable, Sendable {
  let planID: String
  let sourceType: String
  let status: ExitPlanStatus
  let remainingVolume: Int
  let pending: Bool

  var id: String { planID }
}

struct ExitPlanHoldingCapacitySnapshot: Equatable, Sendable {
  let accountID: String
  let instrumentCode: String
  let totalVolume: Int
  let availableVolume: Int
  let frozenVolume: Int
  let protectedVolume: Int
  let pendingVolume: Int
  let unallocatedVolume: Int
  let conflicts: [ExitPlanCapacityConflict]
}

struct ExitPlanEvent: Equatable, Identifiable, Sendable {
  let id: String
  let planID: String
  let type: String
  let payload: ExitPlanStructuredValue
  let createdAt: Date
}

struct ExitPlanDetailSnapshot: Equatable, Sendable {
  let plan: ExitPlanItem
  let capacity: ExitPlanHoldingCapacitySnapshot
  let events: [ExitPlanEvent]
  let fetchedAt: Date
}

enum ExitPlanDetailState: Equatable, Sendable {
  case idle
  case loading(planID: String)
  case loaded(ExitPlanDetailSnapshot, refreshWarning: String?)
  case failed(planID: String, message: String)

  var snapshot: ExitPlanDetailSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

struct ExitPlanAuthorizationPositionSnapshot: Equatable, Sendable {
  let totalVolume: Int
  let availableVolume: Int
  let frozenVolume: Int
  let yesterdayVolume: Int
  let t1UnavailableVolume: Int
  let updatedAt: Date?
}

struct ExitPlanAuthorizationConflict: Equatable, Identifiable, Sendable {
  let planID: String
  let sourceType: String
  let status: ExitPlanStatus
  let remainingVolume: Int
  let configVersion: Int
  let pending: Bool

  var id: String { planID }
}

struct ExitPlanAuthorizationReview: Equatable, Identifiable, Sendable {
  let id: String
  let accountID: String
  let planID: String
  let instrumentCode: String
  let bucket: String
  let sourceType: String
  let executionMode: ExitPlanExecutionMode
  let configVersion: Int
  let protectedVolume: Int
  let exitedVolume: Int
  let remainingVolume: Int
  let rules: ExitPlanStructuredValue
  let t1Policy: String
  let executionPolicy: ExitPlanStructuredValue
  let position: ExitPlanAuthorizationPositionSnapshot
  let otherProtections: [ExitPlanAuthorizationConflict]
  let readiness: ExitPlanStructuredValue
  let authorizationFingerprint: String
  let authorizationExpiresAt: Date
  let challengeExpiresAt: Date
  let warnings: [String]

  func isChallengeExpired(at date: Date = Date()) -> Bool {
    challengeExpiresAt <= date
  }
}

struct ExitPlanAuthorizationConfirmation: Equatable, Sendable {
  let challengeID: String
  let planID: String
  let configVersion: Int
  let authorizationExpiresAt: Date
  let auditEventID: String
  let message: String
}

struct ExitPlanAuthorizationTicket: Equatable, Sendable {
  let review: ExitPlanAuthorizationReview
  let confirmationToken: String
  let idempotencyKey: UUID
  let userID: String
  let deviceSessionID: String
  let sessionContextID: UUID
}

enum ExitPlanWorkspaceError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case invalidRequest(String)
  case rejected(code: String, message: String)
  case invalidResponse
  case accountScopeMismatch
  case contextChanged
  case versionConflict
  case challengeExpired
  case alreadyInProgress
  case resultUncertain

  var errorDescription: String? {
    switch self {
    case .unavailable(let message), .invalidRequest(let message):
      message
    case .rejected(let code, let message):
      message.isEmpty ? "退出计划操作被拒绝（\(code)）" : "\(message)（\(code)）"
    case .invalidResponse:
      "退出计划服务返回了无法验证的数据"
    case .accountScopeMismatch:
      "退出计划不属于当前唯一主账户，已停止展示或操作"
    case .contextChanged:
      "账户、会话、权限或计划版本已变化，请重新预览"
    case .versionConflict:
      "计划配置版本已变化，已刷新服务端真源，请重新核对"
    case .challengeExpired:
      "授权挑战已过期，请重新获取精确预览"
    case .alreadyInProgress:
      "已有退出计划操作正在进行"
    case .resultUncertain:
      "确认请求已发出但结果不确定；已刷新计划真源，请核对授权状态后再决定是否重试"
    }
  }
}
