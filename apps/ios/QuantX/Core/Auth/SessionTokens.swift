import Foundation

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
