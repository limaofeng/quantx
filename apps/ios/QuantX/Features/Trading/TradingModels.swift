import Foundation

struct OrderRecord: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let systemID: String
  let stockCode: String
  let stockName: String
  let side: String
  let status: String
  let statusMessage: String?
  let price: Double
  let volume: Int
  let tradedVolume: Int
  let tradedPrice: Double
  let strategyName: String?
  let remark: String?
  let submittedAt: Date

  var displayName: String {
    stockName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      ? stockCode
      : stockName
  }

  var sideDisplayName: String {
    switch side {
    case "BUY": "买入"
    case "SELL": "卖出"
    default: "未知方向"
    }
  }

  var remainingVolume: Int {
    max(0, volume - tradedVolume)
  }

  var brokerOrderID: Int? {
    guard let value = Int(id), value > 0, value <= Int(Int32.max) else { return nil }
    return value
  }

  var canCancel: Bool {
    brokerOrderID != nil
      && ["REPORTED", "PART_SUCC"].contains(status)
      && remainingVolume > 0
  }

  var statusDisplayName: String {
    switch status {
    case "UNREPORTED": "未报"
    case "WAIT_REPORTING": "等待报送"
    case "REPORTED": "券商已报"
    case "REPORTED_CANCEL": "已报待撤"
    case "PARTSUCC_CANCEL": "部分成交后撤单"
    case "PART_CANCEL": "部分撤单"
    case "CANCELED": "已撤单"
    case "PART_SUCC": "部分成交"
    case "SUCCEEDED": "全部成交"
    case "JUNK": "废单或拒单"
    default: "未知（\(status)）"
    }
  }
}

struct OrderCancellationRequest: Equatable, Sendable {
  let accountID: String
  let orderID: Int
  let idempotencyKey: UUID
}

struct OrderCancellationQueueConfirmation: Equatable, Sendable {
  let orderID: Int
  let clientOrderID: String
  let status: String

  static let title = "撤单命令已排队"
  static let message = "等待券商委托投影更新；排队不代表订单已经撤销。"
}

struct TradeRecord: Equatable, Hashable, Identifiable, Sendable {
  let id: String
  let accountID: String
  let orderID: Int
  let orderSystemID: String
  let stockCode: String
  let stockName: String
  let orderType: Int
  let direction: Int?
  let price: Double
  let volume: Int
  let amount: Double
  let timestamp: Int
  let executedAt: Date?
  let strategyName: String?
  let remark: String?

  var displayName: String {
    stockName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      ? stockCode
      : stockName
  }

  var sideDisplayName: String {
    switch orderType {
    case 23: "买入"
    case 24: "卖出"
    default: "未知方向（\(orderType)）"
    }
  }
}

struct TradingActivitySnapshot: Equatable, Sendable {
  let accountID: String
  let todayOrders: [OrderRecord]
  let todayTrades: [TradeRecord]
  let historyOrders: [OrderRecord]
  let historyTrades: [TradeRecord]
  let historyStartDate: Date
  let historyEndDate: Date
  let fetchedAt: Date
}

enum TradingActivityState: Equatable, Sendable {
  case unavailable(String)
  case idle
  case loading
  case noAccount
  case loaded(TradingActivitySnapshot, refreshWarning: String?)
  case failed(String)

  var snapshot: TradingActivitySnapshot? {
    guard case .loaded(let snapshot, _) = self else { return nil }
    return snapshot
  }

  var failureMessage: String? {
    guard case .failed(let message) = self else { return nil }
    return message
  }
}

extension OrderRecord {
  init(graphQL order: QuantXAPI.IOSTodayOrdersQuery.Data.TodayOrder) throws {
    try self.init(
      id: order.id,
      systemID: order.sysid,
      stockCode: order.stockCode,
      stockName: order.stockName,
      side: order.type.rawValue,
      status: order.status.rawValue,
      statusMessage: order.statusMsg,
      price: order.price,
      volume: order.volume,
      tradedVolume: order.tradedVolume,
      tradedPrice: order.tradedPrice,
      strategyName: order.strategyName,
      remark: order.orderRemark,
      submittedAt: order.time
    )
  }

  init(graphQL order: QuantXAPI.IOSHistoryOrdersQuery.Data.HistoryOrder) throws {
    try self.init(
      id: order.id,
      systemID: order.sysid,
      stockCode: order.stockCode,
      stockName: order.stockName,
      side: order.type.rawValue,
      status: order.status.rawValue,
      statusMessage: order.statusMsg,
      price: order.price,
      volume: order.volume,
      tradedVolume: order.tradedVolume,
      tradedPrice: order.tradedPrice,
      strategyName: order.strategyName,
      remark: order.orderRemark,
      submittedAt: order.time
    )
  }

  private init(
    id: String,
    systemID: String,
    stockCode: String,
    stockName: String,
    side: String,
    status: String,
    statusMessage: String?,
    price: Double,
    volume: Int,
    tradedVolume: Int,
    tradedPrice: Double,
    strategyName: String?,
    remark: String?,
    submittedAt: String
  ) throws {
    try ReadOnlyModelValidator.requireNonempty(id, field: "order.id")
    try ReadOnlyModelValidator.requireNonempty(stockCode, field: "order.stockCode")
    try ReadOnlyModelValidator.requireNonnegative(
      [volume, tradedVolume],
      field: "order.volume"
    )
    guard tradedVolume <= volume else {
      throw ReadOnlyMappingError.invalidField("order.tradedVolume")
    }
    try ReadOnlyModelValidator.requireFinite(
      [price, tradedPrice],
      field: "order.price"
    )
    self.id = id
    self.systemID = systemID
    self.stockCode = stockCode
    self.stockName = stockName
    self.side = side
    self.status = status
    self.statusMessage = statusMessage
    self.price = price
    self.volume = volume
    self.tradedVolume = tradedVolume
    self.tradedPrice = tradedPrice
    self.strategyName = strategyName
    self.remark = remark
    self.submittedAt = try ReadOnlyModelValidator.requireDate(
      submittedAt,
      field: "order.time"
    )
  }
}

extension TradeRecord {
  init(graphQL trade: QuantXAPI.IOSTodayTradesQuery.Data.TodayTrade) throws {
    try self.init(
      accountID: trade.accountId,
      id: trade.tradedId,
      orderID: trade.orderId,
      orderSystemID: trade.orderSysid,
      stockCode: trade.stockCode,
      stockName: trade.stockName,
      orderType: trade.orderType,
      direction: trade.direction,
      price: trade.tradedPrice,
      volume: trade.tradedVolume,
      amount: trade.tradedAmount,
      timestamp: trade.tradedTime,
      strategyName: trade.strategyName,
      remark: trade.orderRemark
    )
  }

  init(graphQL trade: QuantXAPI.IOSHistoryTradesQuery.Data.HistoryTrade) throws {
    try self.init(
      accountID: trade.accountId,
      id: trade.tradedId,
      orderID: trade.orderId,
      orderSystemID: trade.orderSysid,
      stockCode: trade.stockCode,
      stockName: trade.stockName,
      orderType: trade.orderType,
      direction: trade.direction,
      price: trade.tradedPrice,
      volume: trade.tradedVolume,
      amount: trade.tradedAmount,
      timestamp: trade.tradedTime,
      strategyName: trade.strategyName,
      remark: trade.orderRemark
    )
  }

  private init(
    accountID: String,
    id: String,
    orderID: Int,
    orderSystemID: String,
    stockCode: String,
    stockName: String,
    orderType: Int,
    direction: Int?,
    price: Double,
    volume: Int,
    amount: Double,
    timestamp: Int,
    strategyName: String?,
    remark: String?
  ) throws {
    try ReadOnlyModelValidator.requireNonempty(accountID, field: "trade.accountId")
    try ReadOnlyModelValidator.requireNonempty(id, field: "trade.id")
    try ReadOnlyModelValidator.requireNonempty(stockCode, field: "trade.stockCode")
    try ReadOnlyModelValidator.requireNonnegative(
      [orderID, volume, timestamp],
      field: "trade.integer"
    )
    try ReadOnlyModelValidator.requireFinite([price, amount], field: "trade.amount")
    self.accountID = accountID
    self.id = id
    self.orderID = orderID
    self.orderSystemID = orderSystemID
    self.stockCode = stockCode
    self.stockName = stockName
    self.orderType = orderType
    self.direction = direction
    self.price = price
    self.volume = volume
    self.amount = amount
    self.timestamp = timestamp
    executedAt = TradingTimestampParser.parse(timestamp)
    self.strategyName = strategyName
    self.remark = remark
  }
}

enum TradingTimestampParser {
  static func parse(_ value: Int) -> Date? {
    guard value > 0 else { return nil }
    let seconds = value > 10_000_000_000 ? TimeInterval(value) / 1_000 : TimeInterval(value)
    let date = Date(timeIntervalSince1970: seconds)
    guard date.timeIntervalSince1970 > 0 else { return nil }
    return date
  }
}
