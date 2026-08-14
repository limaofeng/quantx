import Foundation

enum ManualOrderDirection: String, CaseIterable, Identifiable, Sendable {
  case buy = "BUY"
  case sell = "SELL"

  var id: Self { self }

  var title: String {
    switch self {
    case .buy: "买入"
    case .sell: "卖出"
    }
  }

  var graphQLValue: QuantXAPI.ManualOrderSide {
    switch self {
    case .buy: .buy
    case .sell: .sell
    }
  }
}

enum ManualOrderQuoteType: String, CaseIterable, Identifiable, Sendable {
  case limit = "LIMIT"
  case best = "BEST"

  var id: Self { self }

  var title: String {
    switch self {
    case .limit: "限价"
    case .best: "对手方最优价"
    }
  }

  var graphQLValue: QuantXAPI.ManualOrderPriceType {
    switch self {
    case .limit: .limit
    case .best: .best
    }
  }
}

enum ManualOrderExecutionMode: String, CaseIterable, Identifiable, Sendable {
  case paper = "PAPER"
  case live = "LIVE"

  var id: Self { self }

  var title: String {
    switch self {
    case .paper: "模拟盘"
    case .live: "实盘"
    }
  }

  var graphQLValue: QuantXAPI.ManualOrderExecutionMode {
    switch self {
    case .paper: .paper
    case .live: .live
    }
  }
}

enum ManualOrderInstrument {
  static func canonicalCode(_ value: String) throws -> String {
    let normalized = value
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    guard
      normalized.range(
        of: #"^[0-9]{6}\.(SH|SZ|BJ)$"#,
        options: .regularExpression
      ) != nil
    else {
      throw ManualOrderRepositoryError.invalidRequest(
        "请输入带市场后缀的 A 股代码，例如 600519.SH"
      )
    }
    return normalized
  }

  static func isCanonicalCode(_ value: String) -> Bool {
    (try? canonicalCode(value)) == value
  }

  static func isBeijing(_ canonicalCode: String) -> Bool {
    canonicalCode.hasSuffix(".BJ")
  }
}

struct ManualOrderEntryCapabilities: Equatable, Sendable {
  let accountID: String
  let instrumentCode: String
  let canManualTrade: Bool
  let executionModes: Set<ManualOrderExecutionMode>
  let supportedDirections: Set<ManualOrderDirection>
  let supportedQuoteTypes: Set<ManualOrderQuoteType>
  let liveReady: Bool
  let liveBlockedReasons: [String]
  let warnings: [String]

  var defaultExecutionMode: ManualOrderExecutionMode { .paper }

  var selectableExecutionModes: [ManualOrderExecutionMode] {
    var modes: [ManualOrderExecutionMode] = [.paper]
    if canSelectLive { modes.append(.live) }
    return modes
  }

  var canSelectLive: Bool {
    canManualTrade && executionModes.contains(.live) && liveReady
  }

  func supports(
    direction: ManualOrderDirection,
    quoteType: ManualOrderQuoteType,
    executionMode: ManualOrderExecutionMode
  ) -> Bool {
    guard canManualTrade,
      supportedDirections.contains(direction),
      supportedQuoteTypes.contains(quoteType),
      executionModes.contains(executionMode)
    else {
      return false
    }
    if executionMode == .live, !liveReady { return false }
    if quoteType == .best, ManualOrderInstrument.isBeijing(instrumentCode) {
      return false
    }
    return true
  }
}

enum ManualOrderCapabilityState: Equatable, Sendable {
  case idle
  case loading(instrumentCode: String)
  case loaded(ManualOrderEntryCapabilities)
  case failed(instrumentCode: String, message: String)

  var capabilities: ManualOrderEntryCapabilities? {
    guard case .loaded(let value) = self else { return nil }
    return value
  }
}

struct ManualOrderDraftLink: Equatable, Identifiable, Sendable {
  let id: UUID
  let instrumentCode: String
  let direction: ManualOrderDirection
}

struct ManualOrderRequest: Equatable, Sendable {
  let accountID: String
  let instrumentCode: String
  let direction: ManualOrderDirection
  let quoteType: ManualOrderQuoteType
  let executionMode: ManualOrderExecutionMode
  let volume: Int
  let limitPrice: Double?
  let idempotencyKey: UUID

  var normalizedInstrumentCode: String {
    instrumentCode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
  }
}

struct ManualOrderPreviewTicket: Equatable, Identifiable, Sendable {
  let id: String
  let confirmationToken: String
  let accountID: String
  let instrumentCode: String
  let direction: ManualOrderDirection
  let quoteType: ManualOrderQuoteType
  let requestedVolume: Int
  let finalVolume: Int
  let limitPrice: Double?
  let referencePrice: Double
  let estimatedAmount: Double
  let estimatedFees: Double?
  let availableCash: Double
  let availableVolume: Int?
  let idempotencyKey: UUID
  let executionMode: ManualOrderExecutionMode
  let quoteTimestamp: Date
  let challengeExpiresAt: Date
  let riskDecisionID: String
  let riskAction: String
  let riskReasonCode: String
  let riskReasonDetail: String
  let warnings: [String]

  var wasCapped: Bool {
    riskAction == "CAP" && finalVolume < requestedVolume
  }

  func isExpired(at date: Date = Date()) -> Bool {
    challengeExpiresAt <= date
  }
}

struct ManualOrderQueueConfirmation: Equatable, Sendable {
  let challengeID: String
  let clientOrderID: String
  let status: String
}
