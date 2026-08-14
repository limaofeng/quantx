import Foundation
import XCTest

@testable import QuantX

final class PushNotificationConfigurationTests: XCTestCase {
  func testRuntimeConfigurationAcceptsOnlyExplicitAPNsEnvironment() {
    XCTAssertEqual(
      configuration(environment: "SANDBOX")?.environment,
      .sandbox
    )
    XCTAssertEqual(
      configuration(environment: "PRODUCTION")?.environment,
      .production
    )
    XCTAssertNil(configuration(environment: "debug"))
    XCTAssertNil(configuration(environment: "staging"))
    XCTAssertNil(configuration(environment: "development"))
    XCTAssertNil(configuration(environment: ""))
  }

  func testBuildConfigurationsPinDebugToSandboxAndOthersToProduction() throws {
    let iosRoot = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent()
      .deletingLastPathComponent()
    let debug = try String(
      contentsOf: iosRoot.appendingPathComponent("Config/Debug.xcconfig"),
      encoding: .utf8
    )
    let staging = try String(
      contentsOf: iosRoot.appendingPathComponent("Config/Staging.xcconfig"),
      encoding: .utf8
    )
    let release = try String(
      contentsOf: iosRoot.appendingPathComponent("Config/Release.xcconfig"),
      encoding: .utf8
    )
    let info = try String(
      contentsOf: iosRoot.appendingPathComponent("QuantX/Resources/Info.plist"),
      encoding: .utf8
    )
    let entitlements = try String(
      contentsOf: iosRoot.appendingPathComponent("QuantX/Resources/QuantX.entitlements"),
      encoding: .utf8
    )

    XCTAssertTrue(debug.contains("QUANTX_APNS_API_ENVIRONMENT = SANDBOX"))
    XCTAssertTrue(debug.contains("QUANTX_APNS_ENTITLEMENT_ENVIRONMENT = development"))
    XCTAssertTrue(staging.contains("QUANTX_APNS_API_ENVIRONMENT = PRODUCTION"))
    XCTAssertTrue(staging.contains("QUANTX_APNS_ENTITLEMENT_ENVIRONMENT = production"))
    XCTAssertTrue(release.contains("QUANTX_APNS_API_ENVIRONMENT = PRODUCTION"))
    XCTAssertTrue(release.contains("QUANTX_APNS_ENTITLEMENT_ENVIRONMENT = production"))
    XCTAssertTrue(info.contains("<key>QuantXAPNsEnvironment</key>"))
    XCTAssertTrue(info.contains("$(QUANTX_APNS_API_ENVIRONMENT)"))
    XCTAssertTrue(entitlements.contains("<key>aps-environment</key>"))
    XCTAssertTrue(entitlements.contains("$(QUANTX_APNS_ENTITLEMENT_ENVIRONMENT)"))
  }

  private func configuration(environment: String) -> PushNotificationRuntimeConfiguration? {
    PushNotificationRuntimeConfiguration.validated(
      appBundleID: "com.limaofeng.quantx",
      shortVersion: "1.0",
      build: "1",
      environmentValue: environment
    )
  }
}
