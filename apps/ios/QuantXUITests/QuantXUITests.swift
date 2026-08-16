import UIKit
import XCTest

final class QuantXUITests: XCTestCase {
  override func setUpWithError() throws {
    continueAfterFailure = false
  }

  @MainActor
  func testLaunchShowsProductDashboard() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["今日概览"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["账户概览暂不可用"].exists)
  }

  @MainActor
  func testPortfolioTabShowsConnectionFailureWithoutUsingRealAccount() throws {
    let app = makeApp()
    app.launch()

    let portfolioTab = app.tabBars.buttons["资产"]
    XCTAssertTrue(portfolioTab.waitForExistence(timeout: 5))
    portfolioTab.tap()

    XCTAssertTrue(app.staticTexts["无法读取持仓"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["UI 测试未连接账户服务"].exists)
  }

  @MainActor
  func testDashboardPassesAccessibilityAudit() throws {
    let app = makeApp()
    app.launch()

    XCTAssertTrue(app.staticTexts["今日概览"].waitForExistence(timeout: 5))
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

    XCTAssertTrue(app.staticTexts["今日概览"].waitForExistence(timeout: 5))
    XCTAssertTrue(app.staticTexts["账户概览暂不可用"].exists)

    scrollToElement(app.staticTexts["执行总览"], in: app, preloadSwipes: 2)
    XCTAssertTrue(app.staticTexts["执行总览"].exists)
    XCTAssertTrue(app.buttons["策略执行"].exists)
    XCTAssertTrue(app.buttons["今日动态"].exists)

    scrollToElement(app.staticTexts["交易助手"], in: app, preloadSwipes: 1)
    XCTAssertTrue(app.staticTexts["交易助手"].exists)
    XCTAssertTrue(app.buttons["做T助手"].exists)
    XCTAssertTrue(app.buttons["打板助手"].exists)

    let portfolioTab = app.tabBars.buttons["资产"]
    XCTAssertTrue(portfolioTab.isHittable)
    portfolioTab.tap()
    XCTAssertTrue(app.staticTexts["无法读取持仓"].waitForExistence(timeout: 3))
  }

  @MainActor
  func testAssistantCardsOpenDedicatedSafeFailureScreens() throws {
    let app = makeApp()
    app.launch()

    let tTrade = app.buttons["做T助手"]
    scrollToElement(tTrade, in: app, preloadSwipes: 2)
    XCTAssertTrue(tTrade.isHittable)
    tTrade.tap()
    XCTAssertTrue(app.staticTexts["无法读取做T助手"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["UI 测试未连接做T服务"].exists)

    app.navigationBars.buttons.firstMatch.tap()

    let limitUp = app.buttons["打板助手"]
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

    XCTAssertTrue(app.staticTexts["今日概览"].waitForExistence(timeout: 5))

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
  func testPrimaryNavigationMatchesPersonalQuantProduct() throws {
    let app = makeApp()
    app.launch()

    for title in ["今日", "行情", "交易", "量化", "资产"] {
      XCTAssertTrue(app.tabBars.buttons[title].waitForExistence(timeout: 5))
    }
    XCTAssertFalse(app.tabBars.buttons["设置"].exists)

    app.tabBars.buttons["交易"].tap()
    XCTAssertTrue(app.staticTexts["统一交易安全链路"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["手动交易"].exists)

    app.tabBars.buttons["行情"].tap()
    XCTAssertTrue(app.staticTexts["A 股行情工作台"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["无法读取行情"].exists)
  }

  @MainActor
  func testManualOrderTicketFailsClosedUntilServerCapabilityLoads() throws {
    let app = makeApp()
    app.launch()

    app.tabBars.buttons["交易"].tap()
    let buyButton = app.buttons["买入"]
    XCTAssertTrue(buyButton.waitForExistence(timeout: 5))
    buyButton.tap()

    XCTAssertTrue(app.staticTexts["委托票据"].waitForExistence(timeout: 3))
    XCTAssertTrue(app.staticTexts["当前主账户"].exists)
    XCTAssertFalse(app.buttons["对手方最优价"].exists)
    let previewButton = app.buttons["获取服务器预览"]
    XCTAssertTrue(previewButton.exists)
    XCTAssertFalse(previewButton.isEnabled)
    XCTAssertFalse(app.buttons["确认买入"].exists)
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
  func testLoginUsesServerSelectedSingleAccountWithoutAccountInput() throws {
    let app = XCUIApplication()
    app.launchArguments.append("-QuantXLoginUITesting")
    app.launchEnvironment["QUANTX_IOS_DEVELOPMENT_USERNAME"] = "quantx-ui-test"
    app.launchEnvironment["QUANTX_IOS_DEVELOPMENT_PASSWORD"] = "local-ui-test-password"
    app.launch()

    XCTAssertTrue(app.staticTexts["登录 QuantX"].waitForExistence(timeout: 5))
    XCTAssertEqual(app.textFields["用户名"].value as? String, "quantx-ui-test")
    XCTAssertTrue(app.secureTextFields["密码"].exists)
    XCTAssertFalse(app.buttons["粘贴用户名"].exists)
    XCTAssertFalse(app.buttons["粘贴密码"].exists)
    XCTAssertFalse(app.textFields["login-requested-account-id"].exists)
    XCTAssertTrue(app.staticTexts["服务端会按当前用户授权自动绑定唯一主账户。"].exists)
    XCTAssertTrue(app.buttons["登录并加载数据"].isEnabled)
    let developmentPrefillNotice = app.staticTexts.matching(
      NSPredicate(format: "label CONTAINS %@", "本机 Xcode 配置预填")
    ).firstMatch
    scrollToElement(developmentPrefillNotice, in: app)
    XCTAssertTrue(developmentPrefillNotice.exists)
    XCTAssertFalse(app.tabBars.firstMatch.exists)

    let usernameField = app.textFields["用户名"]
    UIPasteboard.general.string = "pasted-username"
    scrollToElement(usernameField, in: app)
    usernameField.press(forDuration: 1)

    let pasteUsernameButton = app.buttons["粘贴用户名"]
    XCTAssertTrue(pasteUsernameButton.waitForExistence(timeout: 3))
    pasteUsernameButton.tap()
    XCTAssertEqual(usernameField.value as? String, "pasted-username")
    XCTAssertFalse(pasteUsernameButton.exists)
  }

  @MainActor
  func testWatchlistWithoutWriteScopeIsExplicitlyReadOnly() throws {
    let app = XCUIApplication()
    app.launchArguments.append(contentsOf: [
      "-QuantXUITesting",
      "-QuantXWatchlistReadOnlyUITesting",
    ])
    app.launch()

    XCTAssertTrue(app.staticTexts["自选维护不可用"].waitForExistence(timeout: 5))
    XCTAssertTrue(
      app.staticTexts["当前会话没有 watchlist:write 权限，自选保持只读"].exists
    )
    XCTAssertTrue(app.staticTexts["贵州茅台"].exists)
    XCTAssertTrue(app.staticTexts["平安银行"].exists)
    XCTAssertFalse(app.buttons["管理"].exists)
  }

  @MainActor
  private func makeApp() -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments.append("-QuantXUITesting")
    return app
  }

  @MainActor
  private func scrollToElement(
    _ element: XCUIElement,
    in app: XCUIApplication,
    preloadSwipes: Int = 0
  ) {
    let scrollView = app.scrollViews.firstMatch
    let tabBar = app.tabBars.firstMatch
    for _ in 0..<preloadSwipes {
      scrollView.swipeUp()
    }
    for _ in 0..<8 {
      if element.waitForExistence(timeout: 0.25) {
        let elementFrame = element.frame
        let visibleBottom = tabBar.exists ? tabBar.frame.minY : scrollView.frame.maxY
        let isFullyVisible =
          elementFrame.width > 0
          && elementFrame.height > 0
          && elementFrame.minY >= scrollView.frame.minY
          && elementFrame.maxY <= visibleBottom
        if isFullyVisible { return }
      }
      scrollView.swipeUp()
    }
  }
}
