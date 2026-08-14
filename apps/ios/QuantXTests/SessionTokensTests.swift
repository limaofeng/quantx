import Foundation
import XCTest

@testable import QuantX

final class SessionTokensTests: XCTestCase {
  func testSessionTokensRoundTripWithoutLoggingPayload() throws {
    let tokens = SessionTokens(
      accessToken: "access",
      refreshToken: "refresh",
      accessTokenExpiresAt: Date(timeIntervalSince1970: 1_800_000_000),
      refreshTokenExpiresAt: Date(timeIntervalSince1970: 1_802_000_000),
      deviceSessionID: "device-session"
    )

    let data = try JSONEncoder().encode(tokens)
    let decoded = try JSONDecoder().decode(SessionTokens.self, from: data)

    XCTAssertEqual(decoded, tokens)
  }
}
