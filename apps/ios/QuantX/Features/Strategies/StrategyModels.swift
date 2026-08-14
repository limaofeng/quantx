import Foundation

enum StrategyMobileParameterValue: Equatable, Hashable, Sendable {
  case boolean(Bool)
  case integer(Int)
  case number(Double)
  case string(String)

  var graphQLJSON: GraphQLJSON {
    switch self {
    case .boolean(let value): .boolean(value)
    case .integer(let value): .integer(value)
    case .number(let value): .number(value)
    case .string(let value): .string(value)
    }
  }

  var displayValue: String {
    switch self {
    case .boolean(let value): value ? "开启" : "关闭"
    case .integer(let value): value.formatted()
    case .number(let value): value.formatted(.number.precision(.fractionLength(0...8)))
    case .string(let value): value
    }
  }
}

enum StrategyMobileParameterKind: String, Equatable, Hashable, Sendable {
  case boolean
  case integer
  case number
  case string
}

enum StrategyMobileRiskLevel: String, Equatable, Hashable, Sendable {
  case low = "LOW"
  case medium = "MEDIUM"
  case high = "HIGH"

  var title: String {
    switch self {
    case .low: "低风险"
    case .medium: "中风险"
    case .high: "高风险"
    }
  }
}

struct StrategyMobileParameter: Equatable, Hashable, Identifiable, Sendable {
  let key: String
  let title: String
  let description: String
  let kind: StrategyMobileParameterKind
  let currentValue: StrategyMobileParameterValue
  let unit: String?
  let minimum: Double?
  let maximum: Double?
  let step: Double?
  let enumValues: [String]
  let applyImmediately: Bool
  let riskLevel: StrategyMobileRiskLevel

  var id: String { key }

  func validates(_ value: StrategyMobileParameterValue) -> Bool {
    switch (kind, value) {
    case (.boolean, .boolean):
      return true
    case (.string, .string(let value)):
      return enumValues.isEmpty || enumValues.contains(value)
    case (.integer, .integer(let value)):
      return validates(number: Double(value))
    case (.number, .number(let value)):
      return validates(number: value)
    default:
      return false
    }
  }

  private func validates(number: Double) -> Bool {
    guard number.isFinite else { return false }
    if let minimum, number < minimum { return false }
    if let maximum, number > maximum { return false }
    guard let step else { return true }
    let base = minimum ?? 0
    let quotient = (number - base) / step
    let tolerance = max(1, abs(quotient)) * 1e-9
    return abs(quotient.rounded() - quotient) <= tolerance
  }
}

struct StrategyMobileParameterSnapshot: Equatable, Sendable {
  let instanceID: String
  let configVersion: String
  let editable: Bool
  let parameters: [StrategyMobileParameter]

  var values: [String: StrategyMobileParameterValue] {
    Dictionary(uniqueKeysWithValues: parameters.map { ($0.key, $0.currentValue) })
  }
}

struct StrategyParameterConflictDifference: Equatable, Identifiable, Sendable {
  let key: String
  let title: String
  let userValue: StrategyMobileParameterValue?
  let serverValue: StrategyMobileParameterValue?

  var id: String { key }
}

/// A reviewable, in-memory rebase of a stale mobile parameter draft.
///
/// The server snapshot remains the only current truth. `userValues` preserves
/// the draft that lost the optimistic-version race so the UI can require an
/// explicit choice instead of silently overwriting either side.
struct StrategyParameterConflict: Equatable, Sendable {
  let staleVersion: String
  let serverSnapshot: StrategyMobileParameterSnapshot
  let userValues: [String: StrategyMobileParameterValue]

  var serverVersion: String { serverSnapshot.configVersion }

  var allowlistChanged: Bool {
    Set(userValues.keys) != Set(serverSnapshot.parameters.map(\.key))
  }

  var differences: [StrategyParameterConflictDifference] {
    let parametersByKey = Dictionary(
      uniqueKeysWithValues: serverSnapshot.parameters.map { ($0.key, $0) }
    )
    let serverValues = serverSnapshot.values
    return Set(userValues.keys)
      .union(serverValues.keys)
      .sorted()
      .compactMap { key in
        let userValue = userValues[key]
        let serverValue = serverValues[key]
        guard userValue != serverValue else { return nil }
        return StrategyParameterConflictDifference(
          key: key,
          title: parametersByKey[key]?.title ?? key,
          userValue: userValue,
          serverValue: serverValue
        )
      }
  }

  var canResubmit: Bool {
    guard !allowlistChanged, !differences.isEmpty else { return false }
    return serverSnapshot.parameters.allSatisfy { parameter in
      guard let value = userValues[parameter.key] else { return false }
      return parameter.validates(value)
    }
  }

  var rebasedDraftValues: [String: StrategyMobileParameterValue] {
    var values = serverSnapshot.values
    for parameter in serverSnapshot.parameters {
      if let userValue = userValues[parameter.key], parameter.validates(userValue) {
        values[parameter.key] = userValue
      }
    }
    return values
  }

  func replacingUserValues(
    _ values: [String: StrategyMobileParameterValue]
  ) -> StrategyParameterConflict {
    StrategyParameterConflict(
      staleVersion: staleVersion,
      serverSnapshot: serverSnapshot,
      userValues: values
    )
  }
}

enum StrategyParameterEditorState: Equatable, Sendable {
  case idle
  case loading(instanceID: String)
  case loaded(StrategyMobileParameterSnapshot)
  case failed(instanceID: String, message: String)

  var snapshot: StrategyMobileParameterSnapshot? {
    guard case .loaded(let snapshot) = self else { return nil }
    return snapshot
  }
}

enum StrategyLifecycleControl: String, Equatable, Hashable, Identifiable, Sendable {
  case pause
  case resumePaper
  case startLive
  case resumeLive
  case cloneToLive

  var id: Self { self }

  var title: String {
    switch self {
    case .pause: "暂停策略"
    case .resumePaper: "恢复模拟策略"
    case .startLive: "启动实盘策略"
    case .resumeLive: "恢复实盘策略"
    case .cloneToLive: "克隆为实盘"
    }
  }

  var requiresLiveConfirmation: Bool {
    switch self {
    case .pause, .resumePaper: false
    case .startLive, .resumeLive, .cloneToLive: true
    }
  }
}

enum StrategyLiveControlAction: String, Equatable, Hashable, Sendable {
  case start = "START_LIVE"
  case resume = "RESUME_LIVE"
  case clone = "CLONE_TO_LIVE"

  var graphQLValue: QuantXAPI.StrategyControlAction {
    switch self {
    case .start: .startLive
    case .resume: .resumeLive
    case .clone: .cloneToLive
    }
  }

  var title: String {
    switch self {
    case .start: "启动实盘策略"
    case .resume: "恢复实盘策略"
    case .clone: "克隆并启动实盘策略"
    }
  }
}

struct StrategyControlReadinessCheck: Equatable, Hashable, Identifiable, Sendable {
  let code: String
  let passed: Bool
  let message: String

  var id: String { code }
}

struct StrategyControlPreviewTicket: Equatable, Identifiable, Sendable {
  let id: String
  let confirmationToken: String
  let sessionContextID: UUID
  let userID: String
  let deviceSessionID: String
  let accountID: String
  let instanceID: String
  let targetInstanceID: String
  let action: StrategyLiveControlAction
  let configVersion: String
  let currentMode: String
  let currentStatus: String
  let readinessStatus: String
  let snapshotID: String?
  let snapshotAt: Date?
  let expiresAt: Date
  let checks: [StrategyControlReadinessCheck]
  let warnings: [String]

  func isExpired(at date: Date = Date()) -> Bool {
    expiresAt <= date
  }
}

struct StrategyControlConfirmation: Equatable, Sendable {
  let challengeID: String
  let instanceID: String
  let status: String
  let message: String
}

enum StrategyWorkspaceError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case invalidRequest(String)
  case invalidResponse
  case versionConflict
  case contextChanged
  case challengeExpired
  case alreadyInProgress
  case rejected(code: String, message: String)
  case resultUncertain

  var errorDescription: String? {
    switch self {
    case .unavailable(let message), .invalidRequest(let message):
      message
    case .invalidResponse:
      "服务返回了无法验证的策略数据"
    case .versionConflict:
      "策略参数版本已变化；草稿已保留，请核对与服务端最新值的差异"
    case .contextChanged:
      "账户、会话或策略上下文已变化，请重新操作"
    case .challengeExpired:
      "实盘控制确认已过期，请重新预览"
    case .alreadyInProgress:
      "策略操作正在处理中，请勿重复提交"
    case .rejected(_, let message):
      message
    case .resultUncertain:
      "网络在提交后中断，结果尚不确定；未显示为成功，请刷新策略状态"
    }
  }
}

struct StrategyMonitorItem: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let strategyKey: String
  let strategyID: Int?
  let strategyName: String?
  let instrumentCode: String
  let displayName: String
  let status: String
  let mode: String
  let parameterVersion: String
  let createdAt: Date
  let updatedAt: Date
  let lastDecisionAt: Date?
  let latestExecutionStatus: String?

  var statusDisplayName: String {
    switch status {
    case "PENDING": "待启动"
    case "RUNNING": "运行中"
    case "PAUSED": "已暂停"
    case "STOPPED": "已停止"
    case "COMPLETED": "已完成"
    case "ERROR": "异常"
    default: "未知（\(status)）"
    }
  }

  var modeDisplayName: String {
    switch mode {
    case "BACKTEST": "回测"
    case "PAPER": "模拟盘"
    case "LIVE": "实盘"
    default: "未知（\(mode)）"
    }
  }

  var lifecycleControls: [StrategyLifecycleControl] {
    switch (mode, status) {
    case ("PAPER", "RUNNING"):
      [.pause]
    case ("PAPER", "PAUSED"):
      [.resumePaper, .cloneToLive]
    case ("PAPER", "STOPPED"):
      [.cloneToLive]
    case ("LIVE", "RUNNING"):
      [.pause]
    case ("LIVE", "PAUSED"):
      [.resumeLive]
    case ("LIVE", "PENDING"), ("LIVE", "STOPPED"):
      [.startLive]
    default:
      []
    }
  }
}

struct StrategyMonitorSnapshot: Equatable, Sendable {
  let instances: [StrategyMonitorItem]
  let fetchedAt: Date
}

enum StrategyMonitorState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case loaded(StrategyMonitorSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: StrategyMonitorSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }

  var failureMessage: String? {
    guard case .failed(let message) = self else { return nil }
    return message
  }
}

enum ReadOnlyMappingError: Error, Equatable {
  case invalidField(String)
}

enum ReadOnlyModelValidator {
  static func requireNonempty(_ value: String, field: String) throws {
    guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      throw ReadOnlyMappingError.invalidField(field)
    }
  }

  static func requireNonnegative(_ values: [Int], field: String) throws {
    guard values.allSatisfy({ $0 >= 0 }) else {
      throw ReadOnlyMappingError.invalidField(field)
    }
  }

  static func requireFinite(_ values: [Double], field: String) throws {
    guard values.allSatisfy(\.isFinite) else {
      throw ReadOnlyMappingError.invalidField(field)
    }
  }

  static func requireDate(_ value: String, field: String) throws -> Date {
    guard let date = PortfolioDateParser.parse(value) else {
      throw ReadOnlyMappingError.invalidField(field)
    }
    return date
  }
}

extension StrategyMonitorItem {
  init(graphQL instance: QuantXAPI.IOSStrategyInstancesQuery.Data.StrategyInstance) throws {
    try ReadOnlyModelValidator.requireNonempty(instance.id, field: "strategy.id")
    try ReadOnlyModelValidator.requireNonempty(
      instance.instrumentCode,
      field: "strategy.instrumentCode"
    )
    try ReadOnlyModelValidator.requireNonempty(
      instance.displayName,
      field: "strategy.displayName"
    )
    id = instance.id
    strategyKey = instance.strategyKey
    strategyID = instance.strategyId
    strategyName = instance.strategyName
    instrumentCode = instance.instrumentCode
    displayName = instance.displayName
    status = instance.status.rawValue
    mode = instance.mode.rawValue
    parameterVersion = instance.parameterVersion
    createdAt = try ReadOnlyModelValidator.requireDate(
      instance.createdAt,
      field: "strategy.createdAt"
    )
    updatedAt = try ReadOnlyModelValidator.requireDate(
      instance.updatedAt,
      field: "strategy.updatedAt"
    )
    lastDecisionAt = instance.lastDecisionAt.flatMap(PortfolioDateParser.parse)
    latestExecutionStatus = instance.latestExecutionStatus
  }
}

enum StrategyMobileParameterMapper {
  static func parameter(
    key: String,
    title: String,
    description: String,
    valueType: String,
    currentValue: GraphQLJSON,
    unit: String?,
    minimum: Double?,
    maximum: Double?,
    step: Double?,
    enumValues: [String]?,
    applyImmediately: Bool,
    riskLevel: String
  ) throws -> StrategyMobileParameter {
    let normalizedKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
    let normalizedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
    let normalizedDescription = description.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      !normalizedKey.isEmpty,
      normalizedKey == key,
      normalizedKey.count <= 128,
      !normalizedTitle.isEmpty,
      normalizedTitle.count <= 160,
      normalizedDescription.count <= 1_000,
      let kind = StrategyMobileParameterKind(
        rawValue: valueType.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
      ),
      let risk = StrategyMobileRiskLevel(
        rawValue: riskLevel.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
      ),
      [minimum, maximum, step].compactMap({ $0 }).allSatisfy(\.isFinite),
      minimum.map({ minimumValue in maximum.map { minimumValue <= $0 } ?? true }) ?? true,
      step.map({ $0 > 0 }) ?? true
    else {
      throw StrategyWorkspaceError.invalidResponse
    }

    let normalizedEnums = try normalizedEnumValues(enumValues)
    let value: StrategyMobileParameterValue
    switch (kind, currentValue) {
    case (.boolean, .boolean(let current)):
      guard minimum == nil, maximum == nil, step == nil, normalizedEnums.isEmpty else {
        throw StrategyWorkspaceError.invalidResponse
      }
      value = .boolean(current)
    case (.integer, .integer(let current)):
      guard normalizedEnums.isEmpty else {
        throw StrategyWorkspaceError.invalidResponse
      }
      value = .integer(current)
    case (.number, .number(let current)):
      guard current.isFinite, normalizedEnums.isEmpty else {
        throw StrategyWorkspaceError.invalidResponse
      }
      value = .number(current)
    case (.number, .integer(let current)):
      guard normalizedEnums.isEmpty else {
        throw StrategyWorkspaceError.invalidResponse
      }
      value = .number(Double(current))
    case (.string, .string(let current)):
      guard current.count <= 2_000 else {
        throw StrategyWorkspaceError.invalidResponse
      }
      value = .string(current)
    default:
      throw StrategyWorkspaceError.invalidResponse
    }

    let parameter = StrategyMobileParameter(
      key: normalizedKey,
      title: normalizedTitle,
      description: normalizedDescription,
      kind: kind,
      currentValue: value,
      unit: normalizedOptional(unit, maximumLength: 40),
      minimum: minimum,
      maximum: maximum,
      step: step,
      enumValues: normalizedEnums,
      applyImmediately: applyImmediately,
      riskLevel: risk
    )
    guard parameter.validates(value) else {
      throw StrategyWorkspaceError.invalidResponse
    }
    return parameter
  }

  static func snapshot(
    requestedInstanceID: String,
    instanceID: String,
    configVersion: String,
    editable: Bool,
    parameters: [StrategyMobileParameter]
  ) throws -> StrategyMobileParameterSnapshot {
    let version = configVersion.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      instanceID == requestedInstanceID,
      !instanceID.isEmpty,
      parameters.count <= 100,
      Set(parameters.map(\.key)).count == parameters.count,
      !version.isEmpty,
      version.allSatisfy(\.isNumber),
      let numericVersion = Int(version),
      numericVersion > 0,
      String(numericVersion) == version
    else {
      throw StrategyWorkspaceError.invalidResponse
    }
    return StrategyMobileParameterSnapshot(
      instanceID: instanceID,
      configVersion: version,
      editable: editable,
      parameters: parameters
    )
  }

  private static func normalizedEnumValues(_ values: [String]?) throws -> [String] {
    guard let values else { return [] }
    let normalized = values.map {
      $0.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    guard
      normalized.count <= 100,
      normalized.allSatisfy({ !$0.isEmpty && $0.count <= 160 }),
      Set(normalized).count == normalized.count
    else {
      throw StrategyWorkspaceError.invalidResponse
    }
    return normalized
  }

  private static func normalizedOptional(
    _ value: String?,
    maximumLength: Int
  ) -> String? {
    guard let value else { return nil }
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty, normalized.count <= maximumLength else { return nil }
    return normalized
  }
}

enum StrategyControlPreviewMapper {
  static func map(
    challengeID: String,
    confirmationToken: String,
    sessionContextID: UUID,
    userID: String,
    deviceSessionID: String,
    accountID: String,
    responseAccountID: String,
    requestedInstanceID: String,
    responseInstanceID: String,
    targetInstanceID: String,
    requestedAction: StrategyLiveControlAction,
    responseAction: String,
    expectedConfigVersion: String,
    responseConfigVersion: String,
    currentMode: String,
    currentStatus: String,
    readinessStatus: String,
    snapshotID: String?,
    snapshotAt: String?,
    expiresAt: String,
    checks: [StrategyControlReadinessCheck],
    warnings: [String],
    now: Date = Date()
  ) throws -> StrategyControlPreviewTicket {
    let expires = try ReadOnlyModelValidator.requireDate(
      expiresAt,
      field: "strategyControl.challengeExpiresAt"
    )
    let parsedSnapshotAt = try snapshotAt.map {
      try ReadOnlyModelValidator.requireDate($0, field: "strategyControl.snapshotAt")
    }
    let mode = currentMode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    let status = currentStatus.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    let readiness =
      readinessStatus
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    let normalizedWarnings = warnings.map {
      $0.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    let actionStateValid =
      switch requestedAction {
      case .start:
        mode == "LIVE" && ["PENDING", "STOPPED"].contains(status)
      case .resume:
        mode == "LIVE" && status == "PAUSED"
      case .clone:
        mode == "PAPER" && ["PAUSED", "STOPPED"].contains(status)
      }
    let targetValid =
      requestedAction == .clone
      ? !targetInstanceID.isEmpty && targetInstanceID != requestedInstanceID
      : targetInstanceID == requestedInstanceID
    guard
      !challengeID.isEmpty,
      challengeID.count <= 128,
      !confirmationToken.isEmpty,
      confirmationToken.count <= 256,
      !userID.isEmpty,
      !deviceSessionID.isEmpty,
      responseAccountID == accountID,
      responseInstanceID == requestedInstanceID,
      responseAction == requestedAction.rawValue,
      responseConfigVersion == expectedConfigVersion,
      actionStateValid,
      targetValid,
      !readiness.isEmpty,
      expires > now,
      checks.count <= 100,
      Set(checks.map(\.code)).count == checks.count,
      checks.allSatisfy({ !$0.code.isEmpty && !$0.message.isEmpty }),
      normalizedWarnings.count <= 50,
      normalizedWarnings.allSatisfy({ !$0.isEmpty && $0.count <= 1_000 })
    else {
      throw StrategyWorkspaceError.invalidResponse
    }
    return StrategyControlPreviewTicket(
      id: challengeID,
      confirmationToken: confirmationToken,
      sessionContextID: sessionContextID,
      userID: userID,
      deviceSessionID: deviceSessionID,
      accountID: accountID,
      instanceID: requestedInstanceID,
      targetInstanceID: targetInstanceID,
      action: requestedAction,
      configVersion: expectedConfigVersion,
      currentMode: mode,
      currentStatus: status,
      readinessStatus: readiness,
      snapshotID: normalizedOptional(snapshotID),
      snapshotAt: parsedSnapshotAt,
      expiresAt: expires,
      checks: checks,
      warnings: normalizedWarnings
    )
  }

  private static func normalizedOptional(_ value: String?) -> String? {
    guard let value else { return nil }
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return normalized.isEmpty ? nil : String(normalized.prefix(256))
  }
}
