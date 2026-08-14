import Foundation

enum LiquidationScope: String, CaseIterable, Identifiable, Sendable {
  case single = "SINGLE"
  case selected = "SELECTED"
  case all = "ALL"

  var id: Self { self }

  var title: String {
    switch self {
    case .single: "单只持仓"
    case .selected: "选中持仓"
    case .all: "全部持仓"
    }
  }

  var graphQLValue: QuantXAPI.LiquidationScope {
    switch self {
    case .single: .single
    case .selected: .selected
    case .all: .all
    }
  }
}

enum LiquidationCompletionStrategy: String, CaseIterable, Identifiable, Sendable {
  case availableNow = "AVAILABLE_NOW"
  case untilSnapshotCleared = "UNTIL_SNAPSHOT_CLEARED"

  var id: Self { self }

  var title: String {
    switch self {
    case .availableNow: "仅处理当前可卖量"
    case .untilSnapshotCleared: "持续处理预览持仓快照"
    }
  }

  var detail: String {
    switch self {
    case .availableNow:
      "只保护预览时可卖的数量；T+1、冻结或在途数量不会被扩大。"
    case .untilSnapshotCleared:
      "可包含预览时尚不可卖的 T+1 数量，后续仍逐次经过可卖量与风控校验。"
    }
  }

  var graphQLValue: QuantXAPI.LiquidationCompletionStrategy {
    switch self {
    case .availableNow: .availableNow
    case .untilSnapshotCleared: .untilSnapshotCleared
    }
  }
}

enum LiquidationConflictStrategy: String, CaseIterable, Identifiable, Sendable {
  case unallocatedOnly = "UNALLOCATED_ONLY"
  case replaceCancellable = "REPLACE_CANCELLABLE"

  var id: Self { self }

  var title: String {
    switch self {
    case .unallocatedOnly: "只使用未分配数量"
    case .replaceCancellable: "替换可取消退出计划"
    }
  }

  var detail: String {
    switch self {
    case .unallocatedOnly:
      "保留现有保护计划，只为尚未分配的数量创建清仓计划。"
    case .replaceCancellable:
      "服务端可取消并替换冲突计划；确认页会逐项列出受影响计划。"
    }
  }

  var graphQLValue: QuantXAPI.LiquidationConflictStrategy {
    switch self {
    case .unallocatedOnly: .unallocatedOnly
    case .replaceCancellable: .replaceCancellable
    }
  }
}

enum LiquidationExecutionMode: String, CaseIterable, Identifiable, Sendable {
  case paper = "PAPER"
  case live = "LIVE"

  var id: Self { self }

  var title: String {
    switch self {
    case .paper: "模拟（PAPER）"
    case .live: "实盘（LIVE）"
    }
  }

  var graphQLValue: QuantXAPI.LiquidationExecutionMode {
    switch self {
    case .paper: .paper
    case .live: .live
    }
  }
}

struct LiquidationPreviewRequest: Equatable, Sendable {
  let accountID: String
  let scope: LiquidationScope
  let instrumentCodes: [String]
  let completionStrategy: LiquidationCompletionStrategy
  let conflictStrategy: LiquidationConflictStrategy
  let executionMode: LiquidationExecutionMode
  let idempotencyKey: UUID

  var idempotencyKeyValue: String {
    idempotencyKey.uuidString.lowercased()
  }
}

struct LiquidationConflict: Equatable, Hashable, Identifiable, Sendable {
  let planID: String
  let sourceType: String
  let status: String
  let remainingVolume: Int
  let configVersion: Int
  let pending: Bool

  var id: String { planID }
}

struct LiquidationPreviewItem: Equatable, Hashable, Identifiable, Sendable {
  let instrumentCode: String
  let instrumentName: String?
  let totalVolume: Int
  let availableVolume: Int
  let frozenVolume: Int
  let t1UnavailableVolume: Int
  let protectedVolume: Int
  let pendingSellVolume: Int
  let maxProtectedVolume: Int
  let included: Bool
  let reasonCode: String
  let reasonDetail: String
  let positionUpdatedAt: Date?
  let conflicts: [LiquidationConflict]

  var id: String { instrumentCode }

  var displayName: String {
    guard let value = instrumentName?.trimmingCharacters(in: .whitespacesAndNewlines),
      !value.isEmpty
    else {
      return instrumentCode
    }
    return value
  }
}

struct LiquidationPreviewTicket: Equatable, Identifiable, Sendable {
  let id: String
  let confirmationToken: String
  let contextID: UUID
  let groupID: String
  let accountID: String
  let scope: LiquidationScope
  let instrumentCodes: [String]
  let completionStrategy: LiquidationCompletionStrategy
  let conflictStrategy: LiquidationConflictStrategy
  let executionMode: LiquidationExecutionMode
  let idempotencyKey: UUID
  let snapshotVersion: String
  let accountUpdatedAt: Date
  let rolloutSnapshotID: String?
  let rolloutSnapshotHash: String?
  let challengeExpiresAt: Date
  let includedCount: Int
  let skippedCount: Int
  let items: [LiquidationPreviewItem]
  let warnings: [String]

  var includedItems: [LiquidationPreviewItem] {
    items.filter(\.included)
  }

  var signedInstrumentCodes: [String] {
    items.map(\.instrumentCode)
  }

  func isExpired(at date: Date = Date()) -> Bool {
    challengeExpiresAt <= date
  }
}

struct LiquidationPlanOutcome: Equatable, Identifiable, Sendable {
  let instrumentCode: String
  let success: Bool
  let planID: String?
  let protectedVolume: Int?
  let conflictPlanIDs: [String]
  let error: String?

  var id: String { instrumentCode }
}

enum LiquidationCommandStatus: Equatable, Sendable {
  case pending
  case processing
  case succeeded
  case failed
  case unknown(String)

  init(serverValue: String) {
    switch serverValue.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() {
    case "PENDING": self = .pending
    case "PROCESSING": self = .processing
    case "SUCCEEDED": self = .succeeded
    case "FAILED": self = .failed
    case let value: self = .unknown(value)
    }
  }

  var title: String {
    switch self {
    case .pending: "Engine 命令已排队"
    case .processing: "Engine 正在创建计划"
    case .succeeded: "计划创建已返回结果"
    case .failed: "Engine 命令处理失败"
    case .unknown: "Engine 返回未知状态"
    }
  }

  var allowsRecovery: Bool {
    switch self {
    case .pending, .processing, .unknown: true
    case .succeeded, .failed: false
    }
  }
}

struct LiquidationConfirmation: Equatable, Sendable {
  let success: Bool
  let code: String
  let message: String
  let challengeID: String
  let groupID: String
  let commandID: String
  let status: LiquidationCommandStatus
  let createdCount: Int
  let failedCount: Int
  let plans: [LiquidationPlanOutcome]

  var isPartial: Bool {
    code == "LIQUIDATION_PARTIAL" || (createdCount > 0 && failedCount > 0)
  }

  var outcomeMessage: String {
    switch status {
    case .pending:
      return "Engine 命令已排队，尚未返回退出计划创建结果。可继续刷新同一挑战。"
    case .processing:
      return "Engine 正在处理退出计划创建，尚未返回最终结果。可继续刷新同一挑战。"
    case .succeeded:
      if isPartial {
        return "Engine 已返回部分计划创建结果：\(createdCount) 个已创建，\(failedCount) 个未创建。"
      }
      if createdCount > 0 {
        return "Engine 已返回计划创建结果：\(createdCount) 个退出计划已创建。"
      }
      return "Engine 已返回计划创建结果，但没有创建退出计划。"
    case .failed:
      return "Engine 未能完成退出计划创建。未创建的项目可在下方核对。"
    case .unknown:
      return "服务端返回了客户端尚不认识的 Engine 状态；不要据此判断委托或成交，可刷新同一挑战。"
    }
  }
}

enum LiquidationRepositoryError: Error, Equatable, LocalizedError {
  case rejected(code: String, message: String)
  case invalidRequest(String)
  case invalidResponse
  case accountScopeMismatch
  case contextMismatch

  var errorDescription: String? {
    switch self {
    case .rejected(let code, let message):
      return message.isEmpty ? "卖出管理请求被拒绝（\(code)）" : "\(message)（\(code)）"
    case .invalidRequest(let message):
      return message
    case .invalidResponse:
      return "卖出管理服务返回了无法安全验证的数据"
    case .accountScopeMismatch:
      return "卖出管理账户与当前主账户不一致，已停止操作"
    case .contextMismatch:
      return "卖出管理预览与当前持仓或会话不一致，请刷新后重试"
    }
  }

  var allowsResultRecovery: Bool {
    guard case .rejected(let code, _) = self else { return false }
    return code == "CONFIRMATION_RESULT_PENDING"
  }
}

enum LiquidationStoreError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case alreadyInProgress
  case challengeExpired
  case contextChanged
  case resultUncertain

  var errorDescription: String? {
    switch self {
    case .unavailable(let message): message
    case .alreadyInProgress: "另一项卖出管理操作正在处理中"
    case .challengeExpired: "确认挑战已过期，请重新获取服务器预览"
    case .contextChanged: "账户、会话或持仓上下文已变化，请重新预览"
    case .resultUncertain: "确认请求可能已送达，但暂未取得结果；请保留当前页面并使用生物识别刷新结果"
    }
  }
}

enum LiquidationDomainValidator {
  static func canonicalInstrumentCode(_ value: String) throws -> String {
    let normalized = value
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    guard
      normalized.range(
        of: #"^[0-9]{6}\.(SH|SZ|BJ)$"#,
        options: .regularExpression
      ) != nil
    else {
      throw LiquidationRepositoryError.invalidRequest(
        "证券代码必须包含 .SH、.SZ 或 .BJ 市场后缀"
      )
    }
    return normalized
  }

  static func nonempty(
    _ value: String?,
    maximumLength: Int = 300
  ) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    return String(trimmed.prefix(maximumLength))
  }
}
