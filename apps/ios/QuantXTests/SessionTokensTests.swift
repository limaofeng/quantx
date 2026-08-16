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
    let json = try XCTUnwrap(
      JSONSerialization.jsonObject(with: data) as? [String: Any]
    )

    XCTAssertEqual(decoded, tokens)
    XCTAssertNil(json["activeAccountId"])
    XCTAssertNil(json["grantedScopes"])
  }

  func testNativeV1CapabilitiesAreMinimalAndExcludeBroadPermissions() {
    XCTAssertEqual(
      NativeSessionScope.v1CapabilityValues,
      [
        "portfolio:read",
        "market:read",
        "orders:read",
        "strategy:read",
        "system-status:read",
        "watchlist:write",
        "trade:manual",
        "trade:approve",
        "liquidation:control",
        "strategy:control",
        "t-trade:control",
        "limit-up:control",
        "notification:manage",
      ]
    )
    XCTAssertFalse(NativeSessionScope.v1CapabilityValues.contains("mutation:write"))
    XCTAssertFalse(NativeSessionScope.v1CapabilityValues.contains("trade:direct"))
    XCTAssertFalse(
      NativeSessionScope.v1CapabilityValues.contains { $0.hasPrefix("assistant:") }
    )
  }
}
