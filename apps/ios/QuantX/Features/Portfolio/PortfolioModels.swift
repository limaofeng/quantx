import Foundation

struct PortfolioAccount: Equatable, Sendable {
  let id: String
  let name: String
  let type: String
  let totalAsset: Double
  let cash: Double
  let frozenCash: Double
  let marketValue: Double
  let totalProfitLoss: Double?
  let profitLossPercent: Double?
  let updatedAt: Date?
}

struct PortfolioMetrics: Equatable, Sendable {
  let accountID: String
  let accountName: String
  let totalAsset: Double
  let cash: Double
  let marketValue: Double
  let totalProfitLoss: Double
  let totalProfitLossPercent: Double
  let todayProfitLoss: Double?
  let todayProfitLossPercent: Double?
  let positionCount: Int
  let updatedAt: Date?
}

struct PortfolioPosition: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let accountID: String
  let stockCode: String
  let instrumentName: String?
  let volume: Int
  let availableVolume: Int
  let averagePrice: Double?
  let lastPrice: Double?
  let marketValue: Double?
  let marketValuePercent: Double?
  let profitLoss: Double?
  let profitRate: Double?
  let updatedAt: Date?

  var displayName: String {
    let trimmedName = instrumentName?.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let trimmedName, !trimmedName.isEmpty else { return stockCode }
    return trimmedName
  }
}

struct PortfolioSnapshot: Equatable, Sendable {
  let account: PortfolioAccount
  let metrics: PortfolioMetrics
  let positions: [PortfolioPosition]
  let fetchedAt: Date

  var sourceUpdatedAt: Date? {
    let timestamps = [account.updatedAt, metrics.updatedAt] + positions.map(\.updatedAt)
    guard timestamps.allSatisfy({ $0 != nil }) else { return nil }
    return timestamps.compactMap { $0 }.min()
  }

  var positionCountDoesNotMatch: Bool {
    metrics.positionCount != positions.count
  }
}

enum PortfolioLoadResult: Equatable, Sendable {
  case noAccount(fetchedAt: Date)
  case snapshot(PortfolioSnapshot)
}

enum PortfolioState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case noAccount(fetchedAt: Date)
  case loaded(PortfolioSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: PortfolioSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

struct DataFreshness: Equatable, Sendable {
  enum Level: Equatable, Sendable {
    case current
    case delayed
    case stale
    case unknown
  }

  let level: Level
  let age: TimeInterval?

  static func evaluate(updatedAt: Date?, now: Date = Date()) -> DataFreshness {
    guard let updatedAt else {
      return DataFreshness(level: .unknown, age: nil)
    }
    let age = now.timeIntervalSince(updatedAt)
    guard age >= -60 else {
      return DataFreshness(level: .unknown, age: nil)
    }
    let normalizedAge = max(0, age)
    switch normalizedAge {
    case ...90:
      return DataFreshness(level: .current, age: normalizedAge)
    case ...300:
      return DataFreshness(level: .delayed, age: normalizedAge)
    default:
      return DataFreshness(level: .stale, age: normalizedAge)
    }
  }
}

enum PortfolioMappingError: Error, Equatable {
  case invalidField(String)
}

extension PortfolioAccount {
  init(graphQL account: QuantXAPI.IOSCurrentAccountQuery.Data.CurrentAccount) throws {
    try PortfolioModelValidator.requireNonempty(account.id, field: "account.id")
    try PortfolioModelValidator.requireFinite(
      [account.totalAsset, account.cash, account.frozenCash, account.marketValue],
      field: "account.amount"
    )
    try PortfolioModelValidator.requireFinite(
      [account.totalProfitLoss, account.profitLossPercent],
      field: "account.profit"
    )
    id = account.id
    name = account.accountName
    type = account.accountType
    totalAsset = account.totalAsset
    cash = account.cash
    frozenCash = account.frozenCash
    marketValue = account.marketValue
    totalProfitLoss = account.totalProfitLoss
    profitLossPercent = account.profitLossPercent
    updatedAt = PortfolioDateParser.parse(account.updateTime)
  }
}

extension PortfolioMetrics {
  init(graphQL summary: QuantXAPI.IOSPortfolioSummaryQuery.Data.PortfolioSummary) throws {
    try PortfolioModelValidator.requireNonempty(summary.accountId, field: "summary.accountId")
    try PortfolioModelValidator.requireFinite(
      [
        summary.totalAsset,
        summary.cash,
        summary.totalMarketValue,
        summary.totalProfitLoss,
        summary.totalProfitLossPercent,
      ],
      field: "summary.amount"
    )
    try PortfolioModelValidator.requireFinite(
      [summary.todayProfitLoss, summary.todayProfitLossPercent],
      field: "summary.todayProfit"
    )
    guard summary.positionCount >= 0 else {
      throw PortfolioMappingError.invalidField("summary.positionCount")
    }
    accountID = summary.accountId
    accountName = summary.accountName
    totalAsset = summary.totalAsset
    cash = summary.cash
    marketValue = summary.totalMarketValue
    totalProfitLoss = summary.totalProfitLoss
    totalProfitLossPercent = summary.totalProfitLossPercent
    todayProfitLoss = summary.todayProfitLoss
    todayProfitLossPercent = summary.todayProfitLossPercent
    positionCount = summary.positionCount
    updatedAt = PortfolioDateParser.parse(summary.updateTime)
  }
}

extension PortfolioPosition {
  init(graphQL position: QuantXAPI.IOSPositionsQuery.Data.Position) throws {
    try PortfolioModelValidator.requireNonempty(position.id, field: "position.id")
    try PortfolioModelValidator.requireNonempty(position.accountId, field: "position.accountId")
    try PortfolioModelValidator.requireNonempty(position.stockCode, field: "position.stockCode")
    guard position.volume >= 0, position.canUseVolume >= 0 else {
      throw PortfolioMappingError.invalidField("position.volume")
    }
    try PortfolioModelValidator.requireFinite(
      [
        position.avgPrice,
        position.lastPrice,
        position.marketValue,
        position.marketValuePercent,
        position.profitLoss,
        position.profitRate,
      ],
      field: "position.value"
    )
    id = position.id
    accountID = position.accountId
    stockCode = position.stockCode
    instrumentName = position.instrumentName
    volume = position.volume
    availableVolume = position.canUseVolume
    averagePrice = position.avgPrice
    lastPrice = position.lastPrice
    marketValue = position.marketValue
    marketValuePercent = position.marketValuePercent
    profitLoss = position.profitLoss
    profitRate = position.profitRate
    updatedAt = position.updatedAt.flatMap(PortfolioDateParser.parse)
  }
}

private enum PortfolioModelValidator {
  static func requireNonempty(_ value: String, field: String) throws {
    guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      throw PortfolioMappingError.invalidField(field)
    }
  }

  static func requireFinite(_ values: [Double], field: String) throws {
    guard values.allSatisfy(\.isFinite) else {
      throw PortfolioMappingError.invalidField(field)
    }
  }

  static func requireFinite(_ values: [Double?], field: String) throws {
    guard values.compactMap({ $0 }).allSatisfy(\.isFinite) else {
      throw PortfolioMappingError.invalidField(field)
    }
  }
}

enum PortfolioDateParser {
  static func parse(_ value: String) -> Date? {
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: value) {
      return date
    }
    let standard = ISO8601DateFormatter()
    standard.formatOptions = [.withInternetDateTime]
    if let date = standard.date(from: value) {
      return date
    }

    // Older API rows are PostgreSQL `timestamp without time zone` values.
    // QuantX stores those values in Asia/Shanghai; parse them explicitly
    // instead of letting the device locale invent a time zone.
    let legacy = DateFormatter()
    legacy.locale = Locale(identifier: "en_US_POSIX")
    legacy.calendar = Calendar(identifier: .gregorian)
    legacy.timeZone = TimeZone(identifier: "Asia/Shanghai")
    for format in [
      "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
      "yyyy-MM-dd'T'HH:mm:ss.SSS",
      "yyyy-MM-dd'T'HH:mm:ss",
    ] {
      legacy.dateFormat = format
      if let date = legacy.date(from: value) {
        return date
      }
    }
    return nil
  }
}
