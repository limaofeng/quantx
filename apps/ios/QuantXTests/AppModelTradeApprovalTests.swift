import XCTest

@testable import QuantX

@MainActor
final class AppModelTradeApprovalTests: XCTestCase {
  func testConfirmationRequiresLocalBiometricsBeforeMutation() async throws {
    let preview = makePreview()
    let approval = TradeApprovalLoaderSpy(preview: preview)
    let authentication = TradeAuthenticationSpy()
    let model = makeModel(
      permissions: ["trade:approve"],
      approval: approval,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    let loaded = try await model.previewStrategyTradeIntentApproval(
      runID: preview.runID,
      intentID: preview.intentID
    )
    let result = try await model.confirmTradeApproval(loaded)

    XCTAssertEqual(result.challengeID, preview.id)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 1)
    XCTAssertEqual(approval.confirmationCount, 1)
    XCTAssertFalse(model.tradeApprovalInProgress)
  }

  func testMissingIndependentPermissionCannotRequestPreview() async {
    let approval = TradeApprovalLoaderSpy(preview: makePreview())
    let model = makeModel(
      permissions: ["strategy:read", "mutation:write"],
      approval: approval,
      authentication: TradeAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    do {
      _ = try await model.previewStrategyTradeIntentApproval(
        runID: "run-1",
        intentID: "intent-1"
      )
      XCTFail("缺少 trade:approve 时不应请求预览")
    } catch let error as TradeApprovalRepositoryError {
      XCTAssertEqual(
        error,
        .rejected(
          code: "TRADE_APPROVAL_UNAVAILABLE",
          message: "当前会话没有 trade:approve 权限"
        )
      )
    } catch {
      XCTFail("收到意外错误：\(error)")
    }
    XCTAssertEqual(approval.previewCount, 0)
  }

  func testExpiredPreviewFailsBeforeLocalAuthentication() async {
    let preview = makePreview(expiresAt: Date().addingTimeInterval(-1))
    let authentication = TradeAuthenticationSpy()
    let model = makeModel(
      permissions: ["trade:approve"],
      approval: TradeApprovalLoaderSpy(preview: preview),
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    await XCTAssertThrowsErrorAsync {
      _ = try await model.confirmTradeApproval(preview)
    }
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
  }

  private func makeModel(
    permissions: [String],
    approval: TradeApprovalLoaderSpy,
    authentication: TradeAuthenticationSpy
  ) -> AppModel {
    let user = SessionUser(
      id: "user-1",
      username: "operator",
      displayName: "Operator",
      permissions: permissions,
      authorizedAccountIDs: ["ACCOUNT-1"]
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
      sessionClient: TradeSessionService(user: user),
      tokenStore: TradeTokenStore(tokens: SessionTokens(
        accessToken: "access-token",
        refreshToken: "refresh-token",
        accessTokenExpiresAt: Date().addingTimeInterval(600),
        refreshTokenExpiresAt: Date().addingTimeInterval(3_600),
        deviceSessionID: "device-session-1"
      )),
      localAuthentication: authentication,
      tradeApprovalLoaderFactory: { _ in approval }
    )
  }

  private func makePreview(
    expiresAt: Date = Date().addingTimeInterval(60)
  ) -> TradeApprovalPreview {
    TradeApprovalPreview(
      id: "challenge-1",
      confirmationToken: "one-time-token",
      kind: .strategyTradeIntent,
      accountID: "ACCOUNT-1",
      runID: "run-1",
      intentID: "intent-1",
      instrumentCode: "600000.SH",
      side: "BUY",
      bucket: "swing",
      reason: "LIMIT_UP_BOARD_ENTRY",
      targetVolume: 100,
      referencePrice: 10,
      estimatedAmount: 1_000,
      signalExpiresAt: expiresAt,
      challengeExpiresAt: expiresAt,
      warnings: ["确认后仍需统一风控"]
    )
  }
}

@MainActor
private final class TradeApprovalLoaderSpy: TradeApprovalLoading {
  let preview: TradeApprovalPreview
  private(set) var previewCount = 0
  private(set) var confirmationCount = 0

  init(preview: TradeApprovalPreview) {
    self.preview = preview
  }

  func previewTTradeEntry(
    runID _: String,
    intentID _: String,
    authorizedAccountIDs _: Set<String>
  ) async throws -> TradeApprovalPreview {
    previewCount += 1
    return preview
  }

  func confirmTTradeEntry(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation
  {
    confirmationCount += 1
    return confirmation(preview)
  }

  func previewStrategyTradeIntent(
    runID _: String,
    intentID _: String,
    authorizedAccountIDs _: Set<String>
  ) async throws -> TradeApprovalPreview {
    previewCount += 1
    return preview
  }

  func confirmStrategyTradeIntent(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation
  {
    confirmationCount += 1
    return confirmation(preview)
  }

  private func confirmation(_ preview: TradeApprovalPreview) -> TradeApprovalConfirmation {
    TradeApprovalConfirmation(
      success: true,
      code: "APPROVED",
      message: "已进入统一执行链路",
      challengeID: preview.id
    )
  }
}

@MainActor
private final class TradeAuthenticationSpy: LocalAuthenticationProviding {
  private(set) var tradeAuthorizationCount = 0

  func unlock(reason _: String) async throws {}

  func authorizeTrade(reason _: String) async throws {
    tradeAuthorizationCount += 1
  }
}

private actor TradeTokenStore: SessionTokenStore {
  private var tokens: SessionTokens?

  init(tokens: SessionTokens) {
    self.tokens = tokens
  }

  func load() -> SessionTokens? { tokens }
  func save(_ tokens: SessionTokens) { self.tokens = tokens }
  func delete() { tokens = nil }
}

private actor TradeSessionService: SessionServing {
  let user: SessionUser

  init(user: SessionUser) {
    self.user = user
  }

  func login(
    username _: String,
    password _: String,
    deviceName _: String
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
private func XCTAssertThrowsErrorAsync(
  _ expression: () async throws -> Void,
  file: StaticString = #filePath,
  line: UInt = #line
) async {
  do {
    try await expression()
    XCTFail("Expected error to be thrown", file: file, line: line)
  } catch {}
}
