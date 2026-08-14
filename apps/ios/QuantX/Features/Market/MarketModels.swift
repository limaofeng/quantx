import Foundation

enum MarketPeriod: String, CaseIterable, Identifiable, Sendable {
  case minute = "MIN_1"
  case fiveMinutes = "MIN_5"
  case day = "DAY_1"
  case week = "WEEK_1"

  var id: Self { self }

  var title: String {
    switch self {
    case .minute: "分时"
    case .fiveMinutes: "5分"
    case .day: "日K"
    case .week: "周K"
    }
  }

  var graphQLValue: QuantXAPI.KLinePeriod {
    switch self {
    case .minute: .min1
    case .fiveMinutes: .min5
    case .day: .day1
    case .week: .week1
    }
  }
}

struct MarketQuote: Equatable, Hashable, Sendable {
  let stockCode: String
  let time: Date
  let lastPrice: Double
  let open: Double
  let high: Double
  let low: Double
  let preClose: Double
  let change: Double?
  let changePercent: Double?
  let volume: Double
  let amount: Double
  let turnoverRate: Double?

  var trend: Double { change ?? (lastPrice - preClose) }
}

struct MarketInstrument: Equatable, Hashable, Identifiable, Sendable {
  let stockCode: String
  let market: String?
  let instrumentID: String
  let name: String?
  let abbreviation: String?
  let exchangeCode: String?
  let previousClose: Double?
  let upperLimit: Double?
  let lowerLimit: Double?
  let priceTick: Double?
  let isTrading: Bool?
  let quote: MarketQuote?

  var id: String { stockCode }

  var displayName: String {
    guard let value = name?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
      return stockCode
    }
    return value
  }
}

struct MarketWatchItem: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let accountID: String
  let stockCode: String
  let instrumentName: String?
  let displayOrder: Int
  let groupName: String?
  let note: String?
  let updatedAt: Date?
  let quote: MarketQuote?

  var displayName: String {
    guard let value = instrumentName?.trimmingCharacters(in: .whitespacesAndNewlines),
      !value.isEmpty
    else {
      return stockCode
    }
    return value
  }

  func ordered(at displayOrder: Int) -> Self {
    Self(
      id: id,
      accountID: accountID,
      stockCode: stockCode,
      instrumentName: instrumentName,
      displayOrder: displayOrder,
      groupName: groupName,
      note: note,
      updatedAt: updatedAt,
      quote: quote
    )
  }

  func hydrated(with quote: MarketQuote?) -> Self {
    Self(
      id: id,
      accountID: accountID,
      stockCode: stockCode,
      instrumentName: instrumentName,
      displayOrder: displayOrder,
      groupName: groupName,
      note: note,
      updatedAt: updatedAt,
      quote: quote
    )
  }
}

struct MarketCandle: Equatable, Hashable, Identifiable, Sendable {
  let stockCode: String
  let period: String
  let time: Date
  let open: Double
  let high: Double
  let low: Double
  let close: Double
  let previousClose: Double
  let volume: Int
  let amount: Double

  var id: String { "\(stockCode)-\(period)-\(time.timeIntervalSince1970)" }
}

struct MarketLiveQuote: Equatable, Sendable {
  let stockCode: String
  let time: Date
  let currentPrice: Double
  let change: Double?
  let changePercent: Double?
  let volume: Double
  let amount: Double
  let bidPrice: Double
  let askPrice: Double
  let bidVolume: Int
  let askVolume: Int
  let high: Double
  let low: Double
  let open: Double
  let previousClose: Double?
}

struct MarketDepthLevel: Equatable, Hashable, Sendable {
  let price: Double
  let volume: Int
}

struct MarketDepthSnapshot: Equatable, Sendable {
  let stockCode: String
  let time: Date
  let bids: [MarketDepthLevel]
  let asks: [MarketDepthLevel]
}

struct MarketWorkspaceSnapshot: Equatable, Sendable {
  let accountID: String
  let watchlist: [MarketWatchItem]
  let fetchedAt: Date

  var sourceUpdatedAt: Date? {
    watchlist.compactMap { $0.quote?.time ?? $0.updatedAt }.min()
  }

  func replacingWatchlist(
    _ watchlist: [MarketWatchItem],
    fetchedAt: Date = Date()
  ) -> Self {
    Self(accountID: accountID, watchlist: watchlist, fetchedAt: fetchedAt)
  }
}

enum WatchlistMutationError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case alreadyInProgress
  case invalidRequest(String)
  case rejected(String)
  case invalidResponse
  case accountScopeMismatch
  case contextMismatch

  var errorDescription: String? {
    switch self {
    case .unavailable(let message), .invalidRequest(let message), .rejected(let message):
      return message
    case .alreadyInProgress:
      return "另一项自选变更正在处理中"
    case .invalidResponse:
      return "自选服务返回了无法验证的数据，变更已撤销"
    case .accountScopeMismatch:
      return "自选账户与当前主账户不一致，变更已停止"
    case .contextMismatch:
      return "自选列表已变化，请刷新后重试"
    }
  }
}

struct MarketInstrumentSnapshot: Equatable, Sendable {
  let instrument: MarketInstrument
  let period: MarketPeriod
  let candles: [MarketCandle]
  let fetchedAt: Date
}

enum MarketWorkspaceState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case noAccount
  case loaded(MarketWorkspaceSnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: MarketWorkspaceSnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }
}

enum MarketSearchState: Equatable, Sendable {
  case idle
  case loading
  case loaded([MarketInstrument])
  case failed(String)
}

enum MarketInstrumentState: Equatable, Sendable {
  case idle
  case loading
  case loaded(MarketInstrumentSnapshot)
  case notFound
  case failed(String)
}

enum MarketMapping {
  static func quote(
    stockCode: String,
    time: String,
    lastPrice: Double,
    open: Double,
    high: Double,
    low: Double,
    preClose: Double,
    change: Double?,
    changePercent: Double?,
    volume: Double,
    amount: Double,
    turnoverRate: Double?
  ) throws -> MarketQuote {
    try ReadOnlyModelValidator.requireNonempty(stockCode, field: "market.quote.stockCode")
    let date = try ReadOnlyModelValidator.requireDate(time, field: "market.quote.time")
    try ReadOnlyModelValidator.requireFinite(
      [lastPrice, open, high, low, preClose, volume, amount]
        + [change, changePercent, turnoverRate].compactMap { $0 },
      field: "market.quote.values"
    )
    guard lastPrice >= 0, open >= 0, high >= 0, low >= 0, preClose >= 0,
      volume >= 0, amount >= 0
    else {
      throw ReadOnlyMappingError.invalidField("market.quote.values")
    }
    return MarketQuote(
      stockCode: stockCode,
      time: date,
      lastPrice: lastPrice,
      open: open,
      high: high,
      low: low,
      preClose: preClose,
      change: change,
      changePercent: changePercent,
      volume: volume,
      amount: amount,
      turnoverRate: turnoverRate
    )
  }

  static func instrument(
    stockCode: String,
    market: String?,
    instrumentID: String,
    name: String?,
    abbreviation: String?,
    exchangeCode: String?,
    previousClose: Double?,
    upperLimit: Double?,
    lowerLimit: Double?,
    priceTick: Double?,
    isTrading: Bool?,
    quote: MarketQuote?
  ) throws -> MarketInstrument {
    try ReadOnlyModelValidator.requireNonempty(stockCode, field: "market.instrument.stockCode")
    try ReadOnlyModelValidator.requireNonempty(
      instrumentID,
      field: "market.instrument.instrumentId"
    )
    try ReadOnlyModelValidator.requireFinite(
      [previousClose, upperLimit, lowerLimit, priceTick].compactMap { $0 },
      field: "market.instrument.values"
    )
    return MarketInstrument(
      stockCode: stockCode,
      market: market,
      instrumentID: instrumentID,
      name: name,
      abbreviation: abbreviation,
      exchangeCode: exchangeCode,
      previousClose: previousClose,
      upperLimit: upperLimit,
      lowerLimit: lowerLimit,
      priceTick: priceTick,
      isTrading: isTrading,
      quote: quote
    )
  }
}
