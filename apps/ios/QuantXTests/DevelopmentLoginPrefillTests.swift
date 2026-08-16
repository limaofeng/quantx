import XCTest

@testable import QuantX

final class DevelopmentLoginPrefillTests: XCTestCase {
  func testDebugPrefillLoadsMachineLocalEnvironment() {
    let prefill = DevelopmentLoginPrefill.load(
      environment: [
        DevelopmentLoginPrefill.usernameEnvironmentKey: "  quantx-developer  ",
        DevelopmentLoginPrefill.passwordEnvironmentKey: "development-password",
      ],
      bundleInfo: [:]
    )

    XCTAssertEqual(prefill.username, "quantx-developer")
    XCTAssertEqual(prefill.password, "development-password")
    XCTAssertTrue(prefill.isConfigured)
  }

  func testDebugPrefillFallsBackToMachineLocalBundleSettings() {
    let prefill = DevelopmentLoginPrefill.load(
      environment: [:],
      bundleInfo: [
        DevelopmentLoginPrefill.usernameBundleKey: "bundle-developer",
        DevelopmentLoginPrefill.passwordBundleKey: "bundle-password",
      ]
    )

    XCTAssertEqual(prefill.username, "bundle-developer")
    XCTAssertEqual(prefill.password, "bundle-password")
    XCTAssertTrue(prefill.isConfigured)
  }

  func testUnresolvedBuildSettingsDoNotPopulateLoginFields() {
    let prefill = DevelopmentLoginPrefill.load(
      environment: [
        DevelopmentLoginPrefill.usernameEnvironmentKey:
          "$(QUANTX_IOS_DEVELOPMENT_USERNAME)",
        DevelopmentLoginPrefill.passwordEnvironmentKey:
          "$(QUANTX_IOS_DEVELOPMENT_PASSWORD)",
      ],
      bundleInfo: [
        DevelopmentLoginPrefill.usernameBundleKey:
          "$(QUANTX_IOS_DEVELOPMENT_USERNAME)",
        DevelopmentLoginPrefill.passwordBundleKey:
          "$(QUANTX_IOS_DEVELOPMENT_PASSWORD)",
      ]
    )

    XCTAssertEqual(prefill, DevelopmentLoginPrefill(username: "", password: ""))
    XCTAssertFalse(prefill.isConfigured)
  }
}
