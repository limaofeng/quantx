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

    await xctAssertThrowsErrorAsync {
      _ = try await model.confirmTradeApproval(preview)
    }
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
  }

  func testTTradePreviewCarriesTheObservedCandidateExpectation() async throws {
    let expectation = TTradeCandidateApprovalExpectation(
      signalVersion: 8,
      candidateID: "candidate-1",
      candidateFingerprint: "fingerprint-1",
      candidateStateVersion: 3,
      configVersion: 4,
      policyVersion: "t_trade_opportunity_v3.0.0"
    )
    let approval = TradeApprovalLoaderSpy(
      preview: makePreview(kind: .tTradeEntry, expectation: expectation)
    )
    let model = makeModel(
      permissions: ["trade:approve"],
      approval: approval,
      authentication: TradeAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    let loaded = try await model.previewTTradeEntryApproval(
      runID: "run-1",
      intentID: "intent-1",
      expectation: expectation
    )

    XCTAssertEqual(approval.receivedExpectation, expectation)
    XCTAssertEqual(loaded.tTradeExpectation, expectation)
  }

  func testTTradeSignalApprovalFailsClosedForStaleOrIncompatibleSnapshot() {
    let expectation = TTradeCandidateApprovalExpectation(
      signalVersion: 8,
      candidateID: "candidate-1",
      candidateFingerprint: "fingerprint-1",
      candidateStateVersion: 3,
      configVersion: 4,
      policyVersion: "t_trade_opportunity_v3.0.0"
    )
    let valid = makeSignal(expectation: expectation)
    let stale = makeSignal(
      expectation: expectation,
      expiresAt: Date().addingTimeInterval(-1)
    )
    let incompatible = makeSignal(
      expectation: nil,
      compatibilityMessage: "信号协议版本不兼容，当前信号保持只读"
    )
    let missingIdentity = makeSignal(expectation: nil)
    let notAwaiting = makeSignal(
      expectation: expectation,
      candidateStatus: .latched
    )

    XCTAssertNil(valid.approvalUnavailableReason())
    XCTAssertNotNil(stale.approvalUnavailableReason())
    XCTAssertNotNil(incompatible.approvalUnavailableReason())
    XCTAssertNotNil(missingIdentity.approvalUnavailableReason())
    XCTAssertNotNil(notAwaiting.approvalUnavailableReason())
    XCTAssertEqual(
      TTradeCandidateStatus(serverValue: "future_state"),
      .unknown("FUTURE_STATE")
    )
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
        environment: .production,
        graphQLHTTPURL: URL(string: "https://quantx.test/graphql")!,
        graphQLWebSocketURL: URL(string: "wss://quantx.test/graphql")!,
        healthURL: URL(string: "https://quantx.test/health")!,
        authBaseURL: URL(string: "https://quantx.test")!,
        accountDataEnabled: true
      ),
      sessionClient: TradeSessionService(user: user),
      tokenStore: TradeTokenStore(
        tokens: SessionTokens(
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
    expiresAt: Date = Date().addingTimeInterval(60),
    kind: TradeApprovalKind = .strategyTradeIntent,
    expectation: TTradeCandidateApprovalExpectation? = nil
  ) -> TradeApprovalPreview {
    TradeApprovalPreview(
      id: "challenge-1",
      confirmationToken: "one-time-token",
      kind: kind,
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
      warnings: ["确认后仍需统一风控"],
      tTradeExpectation: expectation
    )
  }

  private func makeSignal(
    expectation: TTradeCandidateApprovalExpectation?,
    expiresAt: Date = Date().addingTimeInterval(60),
    compatibilityMessage: String? = nil,
    candidateStatus: TTradeCandidateStatus = .awaitingApproval
  ) -> TTradeSignalItem {
    TTradeSignalItem(
      id: "run-1",
      runID: "run-1",
      stockCode: "600000.SH",
      candidateStatus: candidateStatus,
      signalPrice: 10,
      pullbackPercent: -1.5,
      reboundPercent: 0.8,
      opportunityScore: 80,
      candidateThreshold: 70,
      dataHealth: "READY",
      dominantPhase: "PULLBACK_CANDIDATE_LATCHED",
      firstBlocker: nil,
      sourceAt: Date(),
      evaluatedAt: Date(),
      candidateExpiresAt: expiresAt,
      pendingEntryIntentID: "intent-1",
      approvalExpectation: expectation,
      compatibilityMessage: compatibilityMessage
    )
  }
}

@MainActor
private final class TradeApprovalLoaderSpy: TradeApprovalLoading {
  let preview: TradeApprovalPreview
  private(set) var previewCount = 0
  private(set) var confirmationCount = 0
  private(set) var receivedExpectation: TTradeCandidateApprovalExpectation?

  init(preview: TradeApprovalPreview) {
    self.preview = preview
  }

  func previewTTradeEntry(
    runID _: String,
    intentID _: String,
    expectation: TTradeCandidateApprovalExpectation,
    authorizedAccountIDs _: Set<String>
  ) async throws -> TradeApprovalPreview {
    previewCount += 1
    receivedExpectation = expectation
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
private func xctAssertThrowsErrorAsync(
  _ expression: () async throws -> Void,
  file: StaticString = #filePath,
  line: UInt = #line
) async {
  do {
    try await expression()
    XCTFail("Expected error to be thrown", file: file, line: line)
  } catch {}
}
