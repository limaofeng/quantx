import Foundation
import XCTest

@testable import QuantX

final class HealthSnapshotTests: XCTestCase {
  func testDecodesSafeHealthFields() throws {
    let payload = Data(
      """
      {
        "status": "healthy",
        "version": "2.0.0",
        "api_type": "GraphQL",
        "environment": "development",
        "realtime_enabled": true,
        "miniqmt": {
          "available": true,
          "connected": true,
          "connection_state": "account_verified",
          "account_connected": true
        }
      }
      """.utf8
    )

    let snapshot = try JSONDecoder().decode(HealthSnapshot.self, from: payload)

    XCTAssertEqual(snapshot.status, "healthy")
    XCTAssertTrue(snapshot.isReady)
    XCTAssertEqual(snapshot.apiType, "GraphQL")
    XCTAssertEqual(snapshot.miniQMT?.connectionState, "account_verified")
    XCTAssertEqual(snapshot.miniQMT?.accountConnected, true)
  }

  func testDecodesCurrentComponentHealthContract() throws {
    let payload = Data(
      """
      {
        "status": "ready",
        "profile": "full",
        "requiredComponents": ["api", "database", "qmtAgent"],
        "components": {
          "api": {"status": "ready"},
          "database": {"status": "ready"},
          "qmtAgent": {
            "status": "ready",
            "connectedDevices": 1,
            "onlineDevices": 1
          }
        }
      }
      """.utf8
    )

    let snapshot = try JSONDecoder().decode(HealthSnapshot.self, from: payload)

    XCTAssertTrue(snapshot.isReady)
    XCTAssertEqual(snapshot.profile, "full")
    XCTAssertNil(snapshot.apiType)
    XCTAssertNil(snapshot.realtimeEnabled)
    XCTAssertEqual(snapshot.requiredComponents, ["api", "database", "qmtAgent"])
    XCTAssertEqual(snapshot.components["qmtAgent"]?.onlineDevices, 1)
    XCTAssertEqual(snapshot.components["database"]?.isReady, true)
  }
}
