import Foundation

enum TTradeControlPrivacy {
  static func maskedAccount(_ accountID: String) -> String {
    accountID.count > 4 ? "•••• \(accountID.suffix(4))" : accountID
  }
}

enum TTradeSafetyAction: String, CaseIterable, Equatable, Hashable, Identifiable, Sendable {
  case beginControlledWindow = "BEGIN_CONTROLLED_WINDOW"
  case activateCanary = "ACTIVATE_CANARY"
  case activateLive = "ACTIVATE_LIVE"
  case killSwitch = "KILL_SWITCH"

  var id: Self { self }

  var title: String {
    switch self {
    case .beginControlledWindow: "建立受控窗口"
    case .activateCanary: "启用 Canary"
    case .activateLive: "启用正式 LIVE"
    case .killSwitch: "触发紧急熔断"
    }
  }

  var detail: String {
    switch self {
    case .beginControlledWindow:
      "绑定当前完整快照，观察窗口内是否出现券商外部活动。"
    case .activateCanary:
      "仅在受控窗口和全部生产门禁保持一致时进入严格灰度。"
    case .activateLive:
      "仅在服务端预览再次通过后开放正式实盘自动化。"
    case .killSwitch:
      "立即停止账户级自动化并转人工处置；不依赖普通就绪门禁。"
    }
  }

  var serverReason: String {
    switch self {
    case .beginControlledWindow: "begin controlled window"
    case .activateCanary: "activate canary"
    case .activateLive: "activate live"
    case .killSwitch: ""
    }
  }

  var targetStage: TTradeSafetyTarget? {
    switch self {
    case .activateCanary: .canary
    case .activateLive: .live
    case .beginControlledWindow, .killSwitch: nil
    }
  }

  var graphQLValue: QuantXAPI.TTradeControlAction {
    switch self {
    case .beginControlledWindow: .beginControlledWindow
    case .activateCanary: .activateCanary
    case .activateLive: .activateLive
    case .killSwitch: .killSwitch
    }
  }
}

enum TTradeSafetyTarget: String, Equatable, Hashable, Sendable {
  case canary = "CANARY"
  case live = "LIVE"

  var graphQLValue: QuantXAPI.TTradeRolloutTarget {
    switch self {
    case .canary: .canary
    case .live: .live
    }
  }
}

struct TTradeSafetyCheck: Equatable, Hashable, Identifiable, Sendable {
  let code: String
  let passed: Bool
  let message: String
  let scope: String

  var id: String { "\(scope):\(code)" }
}

struct TTradeControlSnapshot: Equatable, Sendable {
  let accountID: String
  let monitorEnabled: Bool
  let monitorMode: String
  let stage: String
  let ready: Bool
  let status: String
  let preparationReady: Bool
  let automationReady: Bool
  let engineStatus: String
  let agentStatus: String
  let agentDeviceID: String?
  let agentMode: String
  let protocolVersion: String
  let reconcileStatus: String
  let killSwitch: Bool
  let policyVersion: Int
  let canApprove: Bool
  let canActivateLive: Bool
  let blockedReasons: [String]
  let preparationBlockedReasons: [String]
  let checks: [TTradeSafetyCheck]
  let snapshotID: String?
  let snapshotHash: String?
  let snapshotAt: Date?
  let reconciliationAgeSeconds: Double?
  let controlledWindowActive: Bool
  let controlledWindowSnapshotID: String?
  let controlledWindowStartedAt: Date?
  let positionSnapshotSource: String?
  let positionSnapshotReportedAt: Date?
  let positionSnapshotReceivedAt: Date?
  let positionSnapshotComplete: Bool
  let positionSnapshotError: String?
  let queuedCommandCount: Int
  let queueDelaySeconds: Double
  let deadLetterCount: Int
  let unresolvedCriticalAlertCount: Int
  let manualCoexistence: Bool
  let externalOrderCount: Int
  let externalTradeCount: Int
  let newExternalOrderCount: Int
  let newExternalTradeCount: Int
  let workingExternalOrderCount: Int
  let journalIntegrity: String
  let journalSizeBytes: Int
  let journalPendingReports: Int
  let lastBackupAt: Date?
  let pendingSignalCount: Int
  let activeBatchCount: Int
  let drainingCount: Int
  let projectionGeneratedAt: Date?
  let checkedAt: Date
  let fetchedAt: Date

  var snapshotBinding: String {
    [
      accountID,
      String(policyVersion),
      snapshotID ?? "",
      snapshotHash ?? "",
      stage,
      status,
      controlledWindowActive ? "1" : "0",
      controlledWindowSnapshotID ?? "",
      killSwitch ? "1" : "0",
    ].joined(separator: "|")
  }
}

enum TTradeControlState: Equatable, Sendable {
  case idle
  case loading
  case loaded(TTradeControlSnapshot, refreshWarning: String?)
  case failed(String)
  case unavailable(String)

  var snapshot: TTradeControlSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

struct TTradeControlRepositoryContext: Equatable, Sendable {
  let userID: String
  let deviceSessionID: String
  let activeAccountID: String
  let authorizedAccountIDs: Set<String>
  let sessionContextID: UUID
}

struct TTradeControlPreviewRequest: Equatable, Sendable {
  let accountID: String
  let action: TTradeSafetyAction
  let policyVersion: Int
  let snapshotID: String
  let targetStage: TTradeSafetyTarget?
  let reason: String
  let idempotencyKey: UUID
  let expectedStage: String
  let expectedReadinessStatus: String
  let expectedChecks: [TTradeSafetyCheck]
  let snapshotBinding: String

  var idempotencyKeyValue: String { idempotencyKey.uuidString.lowercased() }
}

struct TTradeControlPreviewTicket: Equatable, Identifiable, Sendable {
  let id: String
  let confirmationToken: String
  let sessionContextID: UUID
  let userID: String
  let deviceSessionID: String
  let accountID: String
  let action: TTradeSafetyAction
  let policyVersion: Int
  let snapshotID: String
  let targetStage: TTradeSafetyTarget?
  let reason: String
  let currentStage: String
  let readinessStatus: String
  let readinessFingerprint: String
  let expiresAt: Date
  let checks: [TTradeSafetyCheck]
  let warnings: [String]
  let snapshotBinding: String

  func isExpired(at date: Date = Date()) -> Bool {
    expiresAt <= date
  }
}

struct TTradeControlConfirmation: Equatable, Sendable {
  let challengeID: String
  let accountID: String
  let action: TTradeSafetyAction
  let code: String
  let operationStatus: String
  let message: String
}

struct TTradePauseConfirmation: Equatable, Sendable {
  let accountID: String
  let code: String
  let message: String
}

enum TTradeControlError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case invalidRequest(String)
  case invalidResponse
  case rejected(code: String, message: String)
  case duplicatePreviewUnavailable
  case contextChanged
  case challengeExpired
  case alreadyInProgress
  case resultUncertain

  var errorDescription: String? {
    switch self {
    case .unavailable(let message), .invalidRequest(let message):
      message
    case .invalidResponse:
      "服务返回了无法验证的做 T 控制数据"
    case .rejected(_, let message):
      message
    case .duplicatePreviewUnavailable:
      "该预览已在服务端生成，但一次性凭据不会再次返回；请稍后重新预览"
    case .contextChanged:
      "用户、设备会话、主账户或安全快照已变化，请重新操作"
    case .challengeExpired:
      "做 T 控制确认已过期，请重新预览"
    case .alreadyInProgress:
      "做 T 控制正在处理中，请勿重复提交"
    case .resultUncertain:
      "网络在确认后中断，结果尚不确定；未显示为成功，已刷新服务端真源"
    }
  }
}
