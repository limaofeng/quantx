import XCTest

@testable import QuantX

final class DevelopmentLoginPrefillTests: XCTestCase {
  func testDebugPrefillLoadsMachineLocalEnvironment() {
    let prefill = DevelopmentLoginPrefill.load(
      environment: [
        DevelopmentLoginPrefill.usernameEnvironmentKey: "  quantx-developer  ",
        DevelopmentLoginPrefill.passwordEnvironmentKey: "development-password",
      ]
    )

    XCTAssertEqual(prefill.username, "quantx-developer")
    XCTAssertEqual(prefill.password, "development-password")
    XCTAssertTrue(prefill.isConfigured)
  }

  func testUnresolvedBuildSettingsDoNotPopulateLoginFields() {
    let prefill = DevelopmentLoginPrefill.load(
      environment: [
        DevelopmentLoginPrefill.usernameEnvironmentKey:
          "$(QUANTX_IOS_DEVELOPMENT_USERNAME)",
        DevelopmentLoginPrefill.passwordEnvironmentKey:
          "$(QUANTX_IOS_DEVELOPMENT_PASSWORD)",
      ]
    )

    XCTAssertEqual(prefill, DevelopmentLoginPrefill(username: "", password: ""))
    XCTAssertFalse(prefill.isConfigured)
  }
}
