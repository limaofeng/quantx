import Foundation

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
