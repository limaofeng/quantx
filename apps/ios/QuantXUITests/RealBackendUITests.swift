import Foundation
import XCTest

final class RealBackendUITests: XCTestCase {
  private struct Grant: Decodable, Sendable {
    struct User: Decodable, Sendable {
      let authorizedAccountIds: [String]
    }

    let accessToken: String
    let user: User
  }

  override func setUpWithError() throws {
    continueAfterFailure = false
  }

  @MainActor
  func testRealBackendDashboardPortfolioAndAssistants() async throws {
    let environment = ProcessInfo.processInfo.environment
    guard environment["QUANTX_IOS_ALLOW_REAL_UI_TEST"] == "1",
      let rawBaseURL = environment["QUANTX_IOS_REAL_BACKEND_URL"],
      let baseURL = URL(string: rawBaseURL),
      let host = baseURL.host
    else {
      throw XCTSkip("仅在显式真实后端 UI 验收方案中运行")
    }
    guard baseURL.scheme == "http",
      host == "127.0.0.1" || host == "localhost"
        || host.hasPrefix("10.") || host.hasPrefix("192.168.")
    else {
      return XCTFail("真实 UI 验收只允许 RFC1918 或本机 HTTP 开发地址")
    }

    let (grant, rawGrant) = try await Self.createDevelopmentGrant(baseURL: baseURL)
    defer {
      Task {
        await Self.deleteDevelopmentSession(
          baseURL: baseURL,
          accessToken: grant.accessToken
        )
      }
    }

    let accountID = try XCTUnwrap(grant.user.authorizedAccountIds.first)
    let app = XCUIApplication()
    app.launchArguments.append("-QuantXRealBackendUITesting")
    app.launchEnvironment["QUANTX_IOS_REAL_UI_GRANT"] = rawGrant.base64EncodedString()
    app.launch()

    XCTAssertTrue(app.staticTexts["今日概览"].waitForExistence(timeout: 20))
    XCTAssertTrue(app.staticTexts["服务正常"].exists)
    let accountSummary = app.staticTexts.matching(
      NSPredicate(format: "label CONTAINS %@", accountID)
    ).firstMatch
    XCTAssertTrue(accountSummary.exists)
    attachScreenshot(app, name: "真实后端-今日")

    let portfolioTab = app.tabBars.buttons["资产"]
    XCTAssertTrue(portfolioTab.isHittable)
    portfolioTab.tap()
    XCTAssertTrue(app.staticTexts["全部持仓"].waitForExistence(timeout: 10))
    XCTAssertFalse(app.staticTexts["无法读取持仓"].exists)
    attachScreenshot(app, name: "真实后端-持仓")

    let homeTab = app.tabBars.buttons["今日"]
    homeTab.tap()
    let tTrade = app.staticTexts["做T助手"].firstMatch
    scrollToElement(tTrade, in: app)
    XCTAssertTrue(tTrade.isHittable)
    tTrade.tap()
    XCTAssertTrue(app.navigationBars["做T助手"].waitForExistence(timeout: 10))
    XCTAssertTrue(app.staticTexts["账户级做T监控"].waitForExistence(timeout: 10))
    XCTAssertFalse(app.staticTexts["无法读取做T助手"].exists)
    attachScreenshot(app, name: "真实后端-做T助手")

    app.navigationBars.buttons.firstMatch.tap()
    let limitUp = app.staticTexts["打板助手"].firstMatch
    scrollToElement(limitUp, in: app)
    XCTAssertTrue(limitUp.isHittable)
    limitUp.tap()
    XCTAssertTrue(app.navigationBars["打板助手"].waitForExistence(timeout: 10))
    let loadedWorkspace = app.staticTexts["统一执行边界"]
    let truthfulEmptyState = app.staticTexts["没有打板策略实例"]
    XCTAssertTrue(
      loadedWorkspace.waitForExistence(timeout: 10) || truthfulEmptyState.exists,
      "打板助手应展示真实工作台或明确的未配置状态"
    )
    XCTAssertFalse(app.staticTexts["无法读取打板助手"].exists)
    attachScreenshot(app, name: "真实后端-打板助手")

    await Self.deleteDevelopmentSession(
      baseURL: baseURL,
      accessToken: grant.accessToken
    )
  }

  nonisolated private static func createDevelopmentGrant(
    baseURL: URL
  ) async throws -> (Grant, Data) {
    var request = URLRequest(url: baseURL.appending(path: "auth/web/session/development"))
    request.httpMethod = "POST"
    request.setValue(baseURL.absoluteString, forHTTPHeaderField: "Origin")
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    let (data, response) = try await URLSession.shared.data(for: request)
    let http = try XCTUnwrap(response as? HTTPURLResponse)
    XCTAssertEqual(http.statusCode, 200)
    return (try JSONDecoder().decode(Grant.self, from: data), data)
  }

  nonisolated private static func deleteDevelopmentSession(
    baseURL: URL,
    accessToken: String
  ) async {
    var request = URLRequest(url: baseURL.appending(path: "auth/session"))
    request.httpMethod = "DELETE"
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
    _ = try? await URLSession.shared.data(for: request)
  }

  @MainActor
  private func scrollToElement(_ element: XCUIElement, in app: XCUIApplication) {
    let scrollView = app.scrollViews.firstMatch
    for _ in 0..<6 where !element.isHittable {
      scrollView.swipeUp()
    }
  }

  @MainActor
  private func attachScreenshot(_ app: XCUIApplication, name: String) {
    let attachment = XCTAttachment(screenshot: app.screenshot())
    attachment.name = name
    attachment.lifetime = .keepAlways
    add(attachment)
  }
}
