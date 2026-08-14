#if DEBUG
import Foundation

enum DebugRealBackendUITestSession {
  enum BootstrapError: Error, Equatable {
    case invalidPayload
    case expired
  }

  static let launchArgument = "-QuantXRealBackendUITesting"
  static let grantEnvironmentKey = "QUANTX_IOS_REAL_UI_GRANT"

  private struct Grant: Decodable {
    let accessToken: String
    let accessTokenExpiresAt: Date
    let deviceSessionID: String
    let user: SessionUser

    private enum CodingKeys: String, CodingKey {
      case accessToken
      case accessTokenExpiresAt
      case deviceSessionID = "deviceSessionId"
      case user
    }
  }

  static func make(
    arguments: [String],
    environment: [String: String],
    now: Date = Date()
  ) throws -> AuthenticatedSession? {
    guard arguments.contains(launchArgument) else { return nil }
    guard let encoded = environment[grantEnvironmentKey],
      let data = Data(base64Encoded: encoded)
    else {
      throw BootstrapError.invalidPayload
    }

    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .iso8601
    guard let grant = try? decoder.decode(Grant.self, from: data),
      !grant.accessToken.isEmpty,
      !grant.deviceSessionID.isEmpty,
      !grant.user.id.isEmpty,
      !grant.user.authorizedAccountIDs.isEmpty
    else {
      throw BootstrapError.invalidPayload
    }
    guard grant.accessTokenExpiresAt > now.addingTimeInterval(30) else {
      throw BootstrapError.expired
    }

    return AuthenticatedSession(
      tokens: SessionTokens(
        accessToken: grant.accessToken,
        refreshToken: "",
        accessTokenExpiresAt: grant.accessTokenExpiresAt,
        refreshTokenExpiresAt: grant.accessTokenExpiresAt,
        deviceSessionID: grant.deviceSessionID
      ),
      user: grant.user
    )
  }
}
#endif
