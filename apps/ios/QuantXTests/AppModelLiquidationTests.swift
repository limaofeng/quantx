import SwiftUI
import XCTest

@testable import QuantX

@MainActor
final class AppModelLiquidationTests: XCTestCase {
  func testPaperPreviewNeedsLiquidationScopeButNotTradeApproval() async throws {
    let loader = LiquidationLoaderSpy()
    let authentication = LiquidationAuthenticationSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    XCTAssertNil(model.liquidationStore.previewUnavailableReason(for: .paper))
    XCTAssertNotNil(model.liquidationStore.confirmationUnavailableReason(for: .paper))
    XCTAssertNotNil(model.liquidationStore.previewUnavailableReason(for: .live))

    let idempotencyKey = UUID()
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.sh"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper,
      idempotencyKey: idempotencyKey
    )

    XCTAssertEqual(preview.accountID, "ACCOUNT-1")
    XCTAssertEqual(loader.lastRequest?.accountID, "ACCOUNT-1")
    XCTAssertEqual(loader.lastRequest?.instrumentCodes, ["600519.SH"])
    XCTAssertEqual(loader.lastRequest?.executionMode, .paper)
    XCTAssertEqual(loader.lastRequest?.idempotencyKey, idempotencyKey)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
  }

  func testLivePreviewRequiresTradeApprovalAndBiometricAvailability() async {
    let loader = LiquidationLoaderSpy()
    let authentication = LiquidationAuthenticationSpy(tradeAuthorizationAvailable: false)
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)

    await xctAssertThrowsLiquidationError {
      _ = try await model.liquidationStore.preview(
        scope: .single,
        instrumentCodes: ["600519.SH"],
        completionStrategy: .availableNow,
        conflictStrategy: .unallocatedOnly,
        executionMode: .live
      )
    }

    XCTAssertEqual(loader.previewCount, 0)
    XCTAssertTrue(
      model.liquidationStore.previewUnavailableReason(for: .live)?.contains("Face ID") == true
    )
  }

  func testEveryConfirmationAuthenticatesBeforeTransmitting() async throws {
    let events = LiquidationEventLog()
    let loader = LiquidationLoaderSpy(events: events)
    let authentication = LiquidationAuthenticationSpy(events: events)
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.SH"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )
    var transmissionStarted = false
    var recoveryAuthorization: LiquidationResultRecoveryAuthorization?

    let first = try await model.liquidationStore.confirm(
      preview,
      recoveryAuthorization: nil,
      onTransmissionStarted: { authorization in
        transmissionStarted = true
        recoveryAuthorization = authorization
      }
    )
    let second = try await model.liquidationStore.confirm(
      preview,
      recoveryAuthorization: recoveryAuthorization,
      onTransmissionStarted: { _ in }
    )

    XCTAssertEqual(first.status, .pending)
    XCTAssertEqual(second.status, .pending)
    XCTAssertTrue(transmissionStarted)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 2)
    XCTAssertEqual(loader.confirmationCount, 2)
    XCTAssertEqual(events.values, ["biometric", "confirm", "biometric", "confirm"])
  }

  func testUnsentExpiredChallengeCannotRecoverOrReachBiometrics() async throws {
    let loader = LiquidationLoaderSpy(previewLifetime: -1)
    let authentication = LiquidationAuthenticationSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.SH"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )

    await xctAssertThrowsLiquidationError {
      _ = try await model.liquidationStore.confirm(
        preview,
        recoveryAuthorization: nil,
        onTransmissionStarted: { _ in }
      )
    }
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
    XCTAssertEqual(loader.confirmationCount, 0)

  }

  func testUncertainSentRequestCanRecoverAfterExpiryWithNewBiometrics() async throws {
    let loader = LiquidationLoaderSpy(
      previewLifetime: 0.2,
      confirmTransportFailures: 1
    )
    let authentication = LiquidationAuthenticationSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.SH"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )
    var recoveryAuthorization: LiquidationResultRecoveryAuthorization?

    do {
      _ = try await model.liquidationStore.confirm(
        preview,
        recoveryAuthorization: nil,
        onTransmissionStarted: { recoveryAuthorization = $0 }
      )
      XCTFail("传输结果不确定时不应返回成功")
    } catch let error as LiquidationStoreError {
      XCTAssertEqual(error, .resultUncertain)
    }
    XCTAssertNotNil(recoveryAuthorization)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 1)
    XCTAssertEqual(loader.confirmationCount, 1)

    try await Task.sleep(for: .milliseconds(250))
    XCTAssertTrue(preview.isExpired())
    let recovered = try await model.liquidationStore.confirm(
      preview,
      recoveryAuthorization: recoveryAuthorization,
      onTransmissionStarted: { recoveryAuthorization = $0 }
    )

    XCTAssertEqual(recovered.status, .pending)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 2)
    XCTAssertEqual(loader.confirmationCount, 2)
  }

  func testBackgroundInvalidatesInMemoryChallengeContext() async throws {
    let loader = LiquidationLoaderSpy()
    let authentication = LiquidationAuthenticationSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: authentication
    )
    await model.restoreSession(requireLocalUnlock: false)
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.SH"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )

    model.handleScenePhase(.background)

    XCTAssertNotEqual(preview.contextID, model.liquidationStore.challengeContextID)
    XCTAssertNotNil(model.liquidationStore.confirmationUnavailableReason(for: preview))
    await xctAssertThrowsLiquidationError {
      _ = try await model.liquidationStore.confirm(
        preview,
        recoveryAuthorization: nil,
        onTransmissionStarted: { _ in }
      )
    }
    XCTAssertEqual(loader.confirmationCount, 0)
  }

  func testAccountIdentityChangeInvalidatesChallengeContext() async throws {
    let loader = LiquidationLoaderSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control", "trade:approve"],
      loader: loader,
      authentication: LiquidationAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)
    let preview = try await model.liquidationStore.preview(
      scope: .single,
      instrumentCodes: ["600519.SH"],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )

    model.liquidationStore.activate(
      identity: LiquidationStore.SessionIdentity(
        userID: "user-1",
        deviceSessionID: "device-session-1",
        activeAccountID: "ACCOUNT-2",
        authorizedAccountIDs: ["ACCOUNT-2"],
        grantedScopes: ["portfolio:read", "liquidation:control", "trade:approve"]
      ),
      repository: loader
    )

    XCTAssertNotEqual(preview.contextID, model.liquidationStore.challengeContextID)
    await xctAssertThrowsLiquidationError {
      _ = try await model.liquidationStore.confirm(
        preview,
        recoveryAuthorization: nil,
        onTransmissionStarted: { _ in }
      )
    }
    XCTAssertEqual(loader.confirmationCount, 0)
  }

  func testAllScopeContextContainsOnlyCurrentPositiveVolumePositions() async throws {
    let loader = LiquidationLoaderSpy()
    let model = makeModel(
      scopes: ["portfolio:read", "liquidation:control"],
      loader: loader,
      authentication: LiquidationAuthenticationSpy()
    )
    await model.restoreSession(requireLocalUnlock: false)

    _ = try await model.liquidationStore.preview(
      scope: .all,
      instrumentCodes: [],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper
    )

    XCTAssertEqual(loader.lastContext?.portfolioInstrumentCodes, ["600519.SH"])
    XCTAssertEqual(loader.lastRequest?.instrumentCodes, [])
  }

  private func makeModel(
    scopes: [String],
    loader: LiquidationLoaderSpy,
    authentication: LiquidationAuthenticationSpy
  ) -> AppModel {
    let user = SessionUser(
      id: "user-1",
      username: "operator",
      displayName: "Operator",
      permissions: scopes,
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
      sessionClient: LiquidationSessionService(user: user),
      tokenStore: LiquidationTokenStore(
        tokens: SessionTokens(
          accessToken: "access-token",
          refreshToken: "refresh-token",
          accessTokenExpiresAt: Date().addingTimeInterval(600),
          refreshTokenExpiresAt: Date().addingTimeInterval(3_600),
          deviceSessionID: "device-session-1"
        )
      ),
      localAuthentication: authentication,
      portfolioLoaderFactory: { _ in
        LiquidationPortfolioLoader(snapshot: Self.makePortfolio())
      },
      liquidationLoaderFactory: { _ in loader }
    )
  }

  private static func makePortfolio() -> PortfolioSnapshot {
    let date = Date()
    return PortfolioSnapshot(
      account: PortfolioAccount(
        id: "ACCOUNT-1",
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
        accountID: "ACCOUNT-1",
        accountName: "主账户",
        totalAsset: 300_000,
        cash: 200_000,
        marketValue: 100_000,
        totalProfitLoss: 0,
        totalProfitLossPercent: 0,
        todayProfitLoss: 0,
        todayProfitLossPercent: 0,
        positionCount: 2,
        updatedAt: date
      ),
      positions: [
        PortfolioPosition(
          id: "position-1",
          accountID: "ACCOUNT-1",
          stockCode: "600519.SH",
          instrumentName: "贵州茅台",
          volume: 100,
          availableVolume: 100,
          averagePrice: 1_400,
          lastPrice: 1_500,
          marketValue: 150_000,
          marketValuePercent: 50,
          profitLoss: 10_000,
          profitRate: 7.14,
          updatedAt: date
        ),
        PortfolioPosition(
          id: "position-zero",
          accountID: "ACCOUNT-1",
          stockCode: "000001.SZ",
          instrumentName: "平安银行",
          volume: 0,
          availableVolume: 0,
          averagePrice: nil,
          lastPrice: 10,
          marketValue: 0,
          marketValuePercent: 0,
          profitLoss: 0,
          profitRate: 0,
          updatedAt: date
        ),
      ],
      fetchedAt: date
    )
  }
}

@MainActor
private final class LiquidationLoaderSpy: LiquidationLoading {
  private let previewLifetime: TimeInterval
  private let events: LiquidationEventLog?
  private var confirmTransportFailures: Int
  private(set) var previewCount = 0
  private(set) var confirmationCount = 0
  private(set) var lastRequest: LiquidationPreviewRequest?
  private(set) var lastContext: LiquidationRepositoryContext?

  init(
    previewLifetime: TimeInterval = 60,
    confirmTransportFailures: Int = 0,
    events: LiquidationEventLog? = nil
  ) {
    self.previewLifetime = previewLifetime
    self.confirmTransportFailures = confirmTransportFailures
    self.events = events
  }

  func preview(
    _ request: LiquidationPreviewRequest,
    context: LiquidationRepositoryContext
  ) async throws -> LiquidationPreviewTicket {
    previewCount += 1
    lastRequest = request
    lastContext = context
    let item = LiquidationPreviewItem(
      instrumentCode: "600519.SH",
      instrumentName: "贵州茅台",
      totalVolume: 100,
      availableVolume: 100,
      frozenVolume: 0,
      t1UnavailableVolume: 0,
      protectedVolume: 0,
      pendingSellVolume: 0,
      maxProtectedVolume: 100,
      included: true,
      reasonCode: "INCLUDED",
      reasonDetail: "服务端纳入退出计划预览",
      positionUpdatedAt: Date(),
      conflicts: []
    )
    return LiquidationPreviewTicket(
      id: UUID().uuidString.lowercased(),
      confirmationToken: "memory-only-token",
      contextID: context.contextID,
      groupID: "group-1",
      accountID: request.accountID,
      scope: request.scope,
      instrumentCodes: request.instrumentCodes,
      completionStrategy: request.completionStrategy,
      conflictStrategy: request.conflictStrategy,
      executionMode: request.executionMode,
      idempotencyKey: request.idempotencyKey,
      snapshotVersion: String(repeating: "e", count: 64),
      accountUpdatedAt: Date(),
      rolloutSnapshotID: request.executionMode == .live ? "rollout-1" : nil,
      rolloutSnapshotHash: request.executionMode == .live ? "rollout-hash-1" : nil,
      challengeExpiresAt: Date().addingTimeInterval(previewLifetime),
      includedCount: 1,
      skippedCount: 0,
      items: [item],
      warnings: ["确认时仍会重新校验"]
    )
  }

  func confirm(
    _ preview: LiquidationPreviewTicket,
    context _: LiquidationRepositoryContext,
    resultRecovery _: Bool
  ) async throws -> LiquidationConfirmation {
    confirmationCount += 1
    events?.values.append("confirm")
    if confirmTransportFailures > 0 {
      confirmTransportFailures -= 1
      throw ReadOnlyRepositoryError.transport
    }
    return LiquidationConfirmation(
      success: true,
      code: "LIQUIDATION_QUEUED",
      message: "queued",
      challengeID: preview.id,
      groupID: preview.groupID,
      commandID: "command-1",
      status: .pending,
      createdCount: 0,
      failedCount: 0,
      plans: []
    )
  }
}

@MainActor
private final class LiquidationAuthenticationSpy: LocalAuthenticationProviding {
  let tradeAuthorizationAvailable: Bool
  private let events: LiquidationEventLog?
  private(set) var tradeAuthorizationCount = 0

  init(
    tradeAuthorizationAvailable: Bool = true,
    events: LiquidationEventLog? = nil
  ) {
    self.tradeAuthorizationAvailable = tradeAuthorizationAvailable
    self.events = events
  }

  func unlock(reason _: String) async throws {}

  func authorizeTrade(reason _: String) async throws {
    tradeAuthorizationCount += 1
    events?.values.append("biometric")
  }
}

@MainActor
private final class LiquidationEventLog {
  var values: [String] = []
}

@MainActor
private final class LiquidationPortfolioLoader: PortfolioLoading {
  let snapshot: PortfolioSnapshot

  init(snapshot: PortfolioSnapshot) {
    self.snapshot = snapshot
  }

  func load(authorizedAccountIDs _: Set<String>) async throws -> PortfolioLoadResult {
    .snapshot(snapshot)
  }
}

private actor LiquidationTokenStore: SessionTokenStore {
  private var tokens: SessionTokens?

  init(tokens: SessionTokens) {
    self.tokens = tokens
  }

  func load() -> SessionTokens? { tokens }
  func save(_ tokens: SessionTokens) { self.tokens = tokens }
  func delete() { tokens = nil }
}

private actor LiquidationSessionService: SessionServing {
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
private func xctAssertThrowsLiquidationError(
  _ expression: () async throws -> Void,
  file: StaticString = #filePath,
  line: UInt = #line
) async {
  do {
    try await expression()
    XCTFail("Expected error to be thrown", file: file, line: line)
  } catch {}
}
