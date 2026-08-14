import XCTest

@testable import QuantX

@MainActor
final class AppModelManualOrderTests: XCTestCase {
  func testMarketDetailCreatesOneShotPrefilledTradeDraft() {
    let model = makeModel(
      permissions: [],
      portfolioAccountID: "ACCOUNT-1",
      manualOrder: ManualOrderLoaderSpy(preview: makePreview()),
      authentication: ManualOrderAuthenticationSpy()
    )

    model.openManualOrder(instrumentCode: "600519.SH", direction: .sell)

    XCTAssertEqual(model.selectedTab, .trade)
    let draft = model.consumePendingManualOrderDraft()
    XCTAssertEqual(draft?.instrumentCode, "600519.SH")
    XCTAssertEqual(draft?.direction, .sell)
    XCTAssertNil(model.consumePendingManualOrderDraft())
  }

  func testManualOrderBindsPrimaryAccountAndAuthenticatesBeforeConfirmation() async throws {
    let idempotencyKey = UUID()
    let preview = makePreview(idempotencyKey: idempotencyKey)
    let manualOrder = ManualOrderLoaderSpy(preview: preview)
    let authentication = ManualOrderAuthenticationSpy()
    let model = makeModel(
      permissions: ["portfolio:read", "trade:manual"],
      portfolioAccountID: "ACCOUNT-1",
      manualOrder: manualOrder,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    let loaded = try await model.previewManualOrder(
      instrumentCode: "600519.sh",
      direction: .buy,
      quoteType: .limit,
      volume: 100,
      limitPrice: 1_500,
      idempotencyKey: idempotencyKey
    )
    XCTAssertEqual(loaded.accountID, "ACCOUNT-1")
    XCTAssertEqual(manualOrder.lastRequest?.accountID, "ACCOUNT-1")
    XCTAssertEqual(manualOrder.lastRequest?.idempotencyKey, idempotencyKey)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)

    let result = try await model.confirmManualOrder(loaded)

    XCTAssertEqual(result.status, "QUEUED")
    XCTAssertEqual(authentication.tradeAuthorizationCount, 1)
    XCTAssertEqual(manualOrder.confirmationCount, 1)
    XCTAssertFalse(model.manualOrderInProgress)
  }

  func testDirectTradePermissionCannotUseManualOrderContract() async {
    let manualOrder = ManualOrderLoaderSpy(preview: makePreview())
    let model = makeModel(
      permissions: ["portfolio:read", "trade:direct", "mutation:write"],
      portfolioAccountID: "ACCOUNT-1",
      manualOrder: manualOrder,
      authentication: ManualOrderAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    do {
      _ = try await model.previewManualOrder(
        instrumentCode: "600519.SH",
        direction: .buy,
        quoteType: .best,
        volume: 100,
        limitPrice: nil,
        idempotencyKey: UUID()
      )
      XCTFail("缺少 trade:manual 时不应请求手动委托预览")
    } catch let error as ManualOrderRepositoryError {
      XCTAssertEqual(
        error,
        .rejected(
          code: "MANUAL_ORDER_UNAVAILABLE",
          message: "当前会话没有 trade:manual 手动交易权限"
        )
      )
    } catch {
      XCTFail("收到意外错误：\(error)")
    }
    XCTAssertEqual(manualOrder.previewCount, 0)
  }

  func testExpiredManualOrderPreviewFailsBeforeBiometrics() async {
    let authentication = ManualOrderAuthenticationSpy()
    let manualOrder = ManualOrderLoaderSpy(
      preview: makePreview(expiresAt: Date().addingTimeInterval(-1))
    )
    let model = makeModel(
      permissions: ["portfolio:read", "trade:manual"],
      portfolioAccountID: "ACCOUNT-1",
      manualOrder: manualOrder,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    await XCTAssertThrowsManualOrderError {
      _ = try await model.confirmManualOrder(manualOrder.previewResult)
    }
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
    XCTAssertEqual(manualOrder.confirmationCount, 0)
  }

  func testCapConfirmationNeverRestoresRequestedVolume() async throws {
    let preview = makePreview(
      requestedVolume: 150,
      finalVolume: 100,
      riskAction: "CAP"
    )
    let manualOrder = ManualOrderLoaderSpy(preview: preview)
    let model = makeModel(
      permissions: ["portfolio:read", "trade:manual"],
      portfolioAccountID: "ACCOUNT-1",
      manualOrder: manualOrder,
      authentication: ManualOrderAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    _ = try await model.confirmManualOrder(preview)

    XCTAssertEqual(manualOrder.lastConfirmedPreview?.requestedVolume, 150)
    XCTAssertEqual(manualOrder.lastConfirmedPreview?.finalVolume, 100)
    XCTAssertTrue(manualOrder.lastConfirmedPreview?.wasCapped == true)
  }

  func testUnverifiedMainAccountCannotCreatePreview() async {
    let manualOrder = ManualOrderLoaderSpy(preview: makePreview())
    let model = makeModel(
      permissions: ["portfolio:read", "trade:manual"],
      authorizedAccountIDs: ["ACCOUNT-1"],
      portfolioAccountID: "ACCOUNT-2",
      manualOrder: manualOrder,
      authentication: ManualOrderAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    await XCTAssertThrowsManualOrderError {
      _ = try await model.previewManualOrder(
        instrumentCode: "600519.SH",
        direction: .sell,
        quoteType: .best,
        volume: 100,
        limitPrice: nil,
        idempotencyKey: UUID()
      )
    }
    XCTAssertEqual(manualOrder.previewCount, 0)
  }

  private func makeModel(
    permissions: [String],
    authorizedAccountIDs: [String] = ["ACCOUNT-1"],
    portfolioAccountID: String,
    manualOrder: ManualOrderLoaderSpy,
    authentication: ManualOrderAuthenticationSpy
  ) -> AppModel {
    let user = SessionUser(
      id: "user-1",
      username: "operator",
      displayName: "Operator",
      permissions: permissions,
      authorizedAccountIDs: authorizedAccountIDs
    )
    return AppModel(
      configuration: APIConfiguration(
        environment: .staging,
        graphQLHTTPURL: URL(string: "https://quantx.test/graphql")!,
        graphQLWebSocketURL: URL(string: "wss://quantx.test/graphql")!,
        healthURL: URL(string: "https://quantx.test/health")!,
        authBaseURL: URL(string: "https://quantx.test")!,
        accountDataEnabled: true
      ),
      sessionClient: ManualOrderSessionService(user: user),
      tokenStore: ManualOrderTokenStore(tokens: SessionTokens(
        accessToken: "access-token",
        refreshToken: "refresh-token",
        accessTokenExpiresAt: Date().addingTimeInterval(600),
        refreshTokenExpiresAt: Date().addingTimeInterval(3_600),
        deviceSessionID: "device-session-1"
      )),
      localAuthentication: authentication,
      portfolioLoaderFactory: { _ in
        ManualOrderPortfolioLoader(snapshot: Self.makePortfolio(accountID: portfolioAccountID))
      },
      manualOrderLoaderFactory: { _ in manualOrder }
    )
  }

  private func makePreview(
    idempotencyKey: UUID = UUID(),
    expiresAt: Date = Date().addingTimeInterval(60),
    requestedVolume: Int = 100,
    finalVolume: Int = 100,
    riskAction: String = "ALLOW"
  ) -> ManualOrderPreviewTicket {
    ManualOrderPreviewTicket(
      id: "challenge-1",
      confirmationToken: "memory-only-token",
      accountID: "ACCOUNT-1",
      instrumentCode: "600519.SH",
      direction: .buy,
      quoteType: .limit,
      requestedVolume: requestedVolume,
      finalVolume: finalVolume,
      limitPrice: 1_500,
      referencePrice: 1_499,
      estimatedAmount: 150_000,
      estimatedFees: 18,
      availableCash: 200_000,
      availableVolume: nil,
      idempotencyKey: idempotencyKey,
      executionMode: "LIVE",
      quoteTimestamp: Date(),
      challengeExpiresAt: expiresAt,
      riskDecisionID: "risk-decision-1",
      riskAction: riskAction,
      riskReasonCode: riskAction == "CAP" ? "ORDER_SIZER_CAP" : "ALLOW",
      riskReasonDetail: riskAction == "CAP"
        ? "请求数量已按 A 股合法规则缩减"
        : "统一风控允许请求数量",
      warnings: ["确认时仍会重新风控"]
    )
  }

  private static func makePortfolio(accountID: String) -> PortfolioSnapshot {
    let date = Date()
    return PortfolioSnapshot(
      account: PortfolioAccount(
        id: accountID,
        name: "主账户",
        type: "STOCK",
        totalAsset: 300_000,
        cash: 200_000,
        frozenCash: 0,
        marketValue: 100_000,
        totalProfitLoss: 0,
        profitLossPercent: 0,
        updatedAt: date
      ),
      metrics: PortfolioMetrics(
        accountID: accountID,
        accountName: "主账户",
        totalAsset: 300_000,
        cash: 200_000,
        marketValue: 100_000,
        totalProfitLoss: 0,
        totalProfitLossPercent: 0,
        todayProfitLoss: 0,
        todayProfitLossPercent: 0,
        positionCount: 0,
        updatedAt: date
      ),
      positions: [],
      fetchedAt: date
    )
  }
}

@MainActor
private final class ManualOrderLoaderSpy: ManualOrderLoading {
  let previewResult: ManualOrderPreviewTicket
  private(set) var previewCount = 0
  private(set) var confirmationCount = 0
  private(set) var lastRequest: ManualOrderRequest?
  private(set) var lastConfirmedPreview: ManualOrderPreviewTicket?

  init(preview: ManualOrderPreviewTicket) {
    previewResult = preview
  }

  func preview(
    _ request: ManualOrderRequest,
    authorizedAccountIDs _: Set<String>
  ) async throws -> ManualOrderPreviewTicket {
    previewCount += 1
    lastRequest = request
    return previewResult
  }

  func confirm(_ preview: ManualOrderPreviewTicket) async throws
    -> ManualOrderQueueConfirmation
  {
    confirmationCount += 1
    lastConfirmedPreview = preview
    return ManualOrderQueueConfirmation(
      challengeID: preview.id,
      clientOrderID: "client-order-1",
      status: "QUEUED"
    )
  }
}

@MainActor
private final class ManualOrderPortfolioLoader: PortfolioLoading {
  let snapshot: PortfolioSnapshot

  init(snapshot: PortfolioSnapshot) {
    self.snapshot = snapshot
  }

  func load(authorizedAccountIDs _: Set<String>) async throws -> PortfolioLoadResult {
    .snapshot(snapshot)
  }
}

@MainActor
private final class ManualOrderAuthenticationSpy: LocalAuthenticationProviding {
  private(set) var tradeAuthorizationCount = 0

  func unlock(reason _: String) async throws {}

  func authorizeTrade(reason _: String) async throws {
    tradeAuthorizationCount += 1
  }
}

private actor ManualOrderTokenStore: SessionTokenStore {
  private var tokens: SessionTokens?

  init(tokens: SessionTokens) {
    self.tokens = tokens
  }

  func load() -> SessionTokens? { tokens }
  func save(_ tokens: SessionTokens) { self.tokens = tokens }
  func delete() { tokens = nil }
}

private actor ManualOrderSessionService: SessionServing {
  let user: SessionUser

  init(user: SessionUser) {
    self.user = user
  }

  func login(
    username _: String,
    password _: String,
    deviceName _: String,
    requestedAccountID _: String?
  ) throws -> AuthenticatedSession {
    throw SessionClient.ClientError.invalidResponse
  }

  func refresh(refreshToken _: String) throws -> AuthenticatedSession {
    throw SessionClient.ClientError.invalidResponse
  }

  func current(accessToken _: String) throws -> SessionUser { user }
  func logout(accessToken _: String, allDevices _: Bool) throws {}
}

@MainActor
private func XCTAssertThrowsManualOrderError(
  _ expression: () async throws -> Void,
  file: StaticString = #filePath,
  line: UInt = #line
) async {
  do {
    try await expression()
    XCTFail("Expected error to be thrown", file: file, line: line)
  } catch {}
}
