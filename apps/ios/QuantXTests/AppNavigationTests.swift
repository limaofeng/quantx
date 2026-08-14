import XCTest

@testable import QuantX

final class AppNavigationTests: XCTestCase {
  func testPrimaryTabsMatchPersonalQuantInformationArchitecture() {
    XCTAssertEqual(AppTab.allCases, [.today, .market, .trade, .quant, .assets])
    XCTAssertEqual(AppTab.allCases.map(\.title), ["今日", "行情", "交易", "量化", "资产"])
    XCTAssertEqual(Set(AppTab.allCases.map(\.systemImage)).count, AppTab.allCases.count)
  }
}
