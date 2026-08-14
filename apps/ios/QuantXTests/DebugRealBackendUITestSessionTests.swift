import Foundation
import XCTest

@testable import QuantX

final class DebugRealBackendUITestSessionTests: XCTestCase {
  func testIgnoresPayloadWithoutExplicitLaunchArgument() throws {
    let session = try DebugRealBackendUITestSession.make(
      arguments: [],
      environment: [DebugRealBackendUITestSession.grantEnvironmentKey: "not-base64"]
    )
    XCTAssertNil(session)
  }

  func testRejectsMalformedPayload() {
    XCTAssertThrowsError(
      try DebugRealBackendUITestSession.make(
        arguments: [DebugRealBackendUITestSession.launchArgument],
        environment: [DebugRealBackendUITestSession.grantEnvironmentKey: "not-base64"]
      )
    ) { error in
      XCTAssertEqual(
        error as? DebugRealBackendUITestSession.BootstrapError,
        .invalidPayload
      )
    }
  }

  func testCreatesShortLivedInMemorySessionFromExplicitGrant() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let payload = """
      {
        "accessToken": "ephemeral-development-access-token",
        "accessTokenExpiresAt": "2027-01-15T08:01:00Z",
        "deviceSessionId": "development-device-session",
        "user": {
          "id": "development-user",
          "username": "development",
          "displayName": "Development",
          "permissions": ["portfolio:read", "strategy:read", "orders:read"],
          "authorizedAccountIds": ["300000013250"]
        }
      }
      """
    let session = try XCTUnwrap(
      DebugRealBackendUITestSession.make(
        arguments: [DebugRealBackendUITestSession.launchArgument],
        environment: [
          DebugRealBackendUITestSession.grantEnvironmentKey:
            try XCTUnwrap(payload.data(using: .utf8)).base64EncodedString()
        ],
        now: now
      )
    )

    XCTAssertEqual(session.user.id, "development-user")
    XCTAssertEqual(session.user.authorizedAccountIDs, ["300000013250"])
    XCTAssertEqual(session.tokens.refreshToken, "")
    XCTAssertEqual(session.tokens.deviceSessionID, "development-device-session")
  }
}
