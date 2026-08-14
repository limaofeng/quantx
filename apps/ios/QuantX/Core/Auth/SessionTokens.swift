import Foundation

enum NativeSessionScope: String, CaseIterable, Sendable {
  case portfolioRead = "portfolio:read"
  case marketRead = "market:read"
  case ordersRead = "orders:read"
  case strategyRead = "strategy:read"
  case systemStatusRead = "system-status:read"
  case watchlistWrite = "watchlist:write"
  case tradeManual = "trade:manual"
  case tradeApprove = "trade:approve"
  case liquidationControl = "liquidation:control"
  case strategyControl = "strategy:control"
  case tTradeControl = "t-trade:control"
  case limitUpControl = "limit-up:control"
  case notificationManage = "notification:manage"

  static let v1Requested: [Self] = [
    .portfolioRead,
    .marketRead,
    .ordersRead,
    .strategyRead,
    .systemStatusRead,
    .watchlistWrite,
    .tradeManual,
    .tradeApprove,
    .liquidationControl,
    .strategyControl,
    .tTradeControl,
    .limitUpControl,
    .notificationManage,
  ]

  static let v1RequestedValues = v1Requested.map(\.rawValue)
  static let v1AllowedValues = Set(v1RequestedValues)
}

struct SessionTokens: Codable, Equatable, Sendable {
  let accessToken: String
  let refreshToken: String
  let accessTokenExpiresAt: Date
  let refreshTokenExpiresAt: Date
  let deviceSessionID: String
}

protocol SessionTokenStore: Sendable {
  func load() async throws -> SessionTokens?
  func save(_ tokens: SessionTokens) async throws
  func delete() async throws
}
