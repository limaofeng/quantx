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
  let executionMode: String
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
