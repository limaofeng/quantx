import XCTest

final class QuantXUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
  }

  @MainActor
  func testLaunchShowsProductDashboard() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["投资概览"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["账户概览暂不可用"].exists)
  }

  @MainActor
  func testPortfolioTabShowsConnectionFailureWithoutUsingRealAccount() throws {
    let app = makeApp()
    app.launch()

    let portfolioTab = app.tabBars.buttons["持仓"]
    XCTAssertTrue(portfolioTab.waitForExistence(timeout: 5))
    portfolioTab.tap()

    XCTAssertTrue(app.staticTexts["无法读取持仓"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["UI 测试未连接账户服务"].exists)
  }

  @MainActor
  func testDashboardPassesAccessibilityAudit() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["投资概览"].waitForExistence(timeout: 5))
    try app.performAccessibilityAudit(
      for: [
        .contrast,
        .elementDetection,
        .hitRegion,
        .sufficientElementDescription,
        .textClipped,
        .trait,
      ]
    )
  }

  @MainActor
  func testDashboardContentRemainsReachableAtAccessibilityTextSize() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["投资概览"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["账户概览暂不可用"].exists)

    XCTAssertTrue(app.staticTexts["运行监控"].exists)
    XCTAssertTrue(app.staticTexts["策略监控"].exists)
    XCTAssertTrue(app.staticTexts["今日动态"].exists)

    scrollToElement(app.staticTexts["交易助手"], in: app)
    XCTAssertTrue(app.staticTexts["交易助手"].exists)
    XCTAssertTrue(app.staticTexts["做T助手"].exists)
    XCTAssertTrue(app.staticTexts["打板助手"].exists)

    let portfolioTab = app.tabBars.buttons["持仓"]
    XCTAssertTrue(portfolioTab.isHittable)
    portfolioTab.tap()
    XCTAssertTrue(app.staticTexts["无法读取持仓"].waitForExistence(timeout: 3))
  }

  @MainActor
  func testAssistantCardsOpenDedicatedSafeFailureScreens() throws {
    let app = makeApp()
    app.launch()

    let tTrade = app.staticTexts["做T助手"]
    scrollToElement(tTrade, in: app)
    XCTAssertTrue(tTrade.isHittable)
    tTrade.tap()
    XCTAssertTrue(app.staticTexts["无法读取做T助手"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["UI 测试未连接做T服务"].exists)

    app.navigationBars.buttons.firstMatch.tap()

    let limitUp = app.staticTexts["打板助手"]
    scrollToElement(limitUp, in: app)
    XCTAssertTrue(limitUp.isHittable)
    limitUp.tap()
    XCTAssertTrue(app.staticTexts["无法读取打板助手"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["UI 测试未连接打板服务"].exists)
  }

  @MainActor
  func testScrollableContentExtendsBehindFloatingTabBar() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["投资概览"].waitForExistence(timeout: 5))

    let scrollView = app.scrollViews.firstMatch
    let tabBar = app.tabBars.firstMatch
    XCTAssertTrue(scrollView.exists)
    XCTAssertTrue(tabBar.exists)
    XCTAssertGreaterThan(
      scrollView.frame.maxY,
      tabBar.frame.minY,
      "滚动内容应延伸到浮动 Tab Bar 下方，不能在其上方留下空白区域"
    )
  }

  @MainActor
  func testTradeApprovalSheetShowsBoundScopeWithoutLeakingToken() throws {
    let app = XCUIApplication()
    app.launchArguments.append(contentsOf: [
      "-QuantXUITesting",
      "-QuantXTradeApprovalUITesting",
    ])
    app.launch()

    XCTAssertTrue(app.navigationBars["安全交易确认"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["核对后使用生物识别"].exists)
    XCTAssertTrue(app.staticTexts["证券代码 六 零 零 五 一 九，上海证券交易所"].exists)
    XCTAssertTrue(app.staticTexts["trade-approval-资金账户"].exists)
    XCTAssertTrue(app.staticTexts["trade-approval-目标数量"].exists)
    XCTAssertTrue(app.staticTexts["trade-approval-仓位归属"].exists)
    XCTAssertTrue(app.staticTexts["提交前仍会重新风控"].exists)
    XCTAssertTrue(app.buttons["Face ID / Touch ID 确认"].exists)
    let leakedTokenElement = app.staticTexts["secret-token-must-never-appear"]
    XCTAssertFalse(leakedTokenElement.exists, leakedTokenElement.debugDescription)

    try app.performAccessibilityAudit(
      for: [
        .contrast,
        .elementDetection,
        .hitRegion,
        .sufficientElementDescription,
        .textClipped,
        .trait,
      ]
    )
  }

  @MainActor
  private func makeApp() -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments.append("-QuantXUITesting")
    return app
  }

  @MainActor
  private func scrollToElement(_ element: XCUIElement, in app: XCUIApplication) {
    let scrollView = app.scrollViews.firstMatch
    for _ in 0..<5 where !element.isHittable {
      scrollView.swipeUp()
    }
  }
}
