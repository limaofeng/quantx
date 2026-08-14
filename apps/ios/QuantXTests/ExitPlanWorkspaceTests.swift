import XCTest

@testable import QuantX

@MainActor
final class ExitPlanWorkspaceTests: XCTestCase {
  func testReadRequiresOrdersScopeAndUniquePrimaryAccountBeforeNetwork() async {
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    let harness = makeHarness(repository: repository, scopes: ["liquidation:control"])

    await harness.store.refresh()

    XCTAssertEqual(repository.loadCount, 0)
    guard case .unavailable(let message) = harness.store.listState else {
      return XCTFail("缺少读取权限必须保持不可用")
    }
    XCTAssertTrue(message.contains("orders:read"))

    harness.store.activate(
      identity: makeIdentity(
        activeAccountID: "ACCOUNT-1",
        authorizedAccountIDs: ["ACCOUNT-1", "ACCOUNT-2"]
      ),
      repository: repository
    )
    await harness.store.refresh()
    XCTAssertEqual(repository.loadCount, 0)
  }

  func testList401RefreshesAtMostOnceThenUsesNewRepositoryContext() async {
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    repository.loadErrors = [.unauthenticated]
    let harness = makeHarness(repository: repository)

    await harness.store.refresh()

    XCTAssertEqual(repository.loadCount, 2)
    XCTAssertEqual(harness.runtime.sessionRefreshCount, 1)
    XCTAssertEqual(harness.store.listState.snapshot?.plans.count, 1)
  }

  func testRepeated401DoesNotLoop() async {
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    repository.loadErrors = [.unauthenticated, .unauthenticated]
    let harness = makeHarness(repository: repository)

    await harness.store.refresh()

    XCTAssertEqual(repository.loadCount, 2)
    XCTAssertEqual(harness.runtime.sessionRefreshCount, 1)
    guard case .failed = harness.store.listState else {
      return XCTFail("第二次 401 必须停止并显示失败")
    }
  }

  func testLivePreviewDoesNotAuthenticateUntilExplicitConfirmation() async throws {
    let authentication = ExitPlanAuthenticationSpy()
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    let harness = makeHarness(repository: repository, authentication: authentication)
    await harness.store.refresh()
    let plan = try XCTUnwrap(harness.store.listState.snapshot?.plans.first)

    try await harness.store.previewAuthorization(for: plan)

    XCTAssertEqual(repository.previewCount, 1)
    XCTAssertTrue(authentication.reasons.isEmpty)
    XCTAssertNotNil(harness.store.pendingAuthorization)

    try await harness.store.confirmPendingAuthorization()

    XCTAssertEqual(authentication.reasons.count, 1)
    XCTAssertEqual(repository.confirmCount, 1)
    XCTAssertEqual(harness.runtime.truthRefreshCount, 1)
    XCTAssertNil(harness.store.pendingAuthorization)
    XCTAssertNotNil(harness.store.successMessage)
  }

  func testConfirmation401RefreshesOnceAndRequiresBiometricsAgain() async throws {
    let authentication = ExitPlanAuthenticationSpy()
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    repository.confirmErrors = [.unauthenticated]
    let harness = makeHarness(repository: repository, authentication: authentication)
    await harness.store.refresh()
    let plan = try XCTUnwrap(harness.store.listState.snapshot?.plans.first)
    try await harness.store.previewAuthorization(for: plan)

    try await harness.store.confirmPendingAuthorization()

    XCTAssertEqual(repository.confirmCount, 2)
    XCTAssertEqual(harness.runtime.sessionRefreshCount, 1)
    XCTAssertEqual(authentication.reasons.count, 2)
    XCTAssertNotNil(harness.store.successMessage)
  }

  func testConfirmationTransportFailureIsUncertainAndRefreshesTruth() async throws {
    let authentication = ExitPlanAuthenticationSpy()
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    repository.confirmErrors = [.transport]
    let harness = makeHarness(repository: repository, authentication: authentication)
    await harness.store.refresh()
    let plan = try XCTUnwrap(harness.store.listState.snapshot?.plans.first)
    try await harness.store.previewAuthorization(for: plan)

    do {
      try await harness.store.confirmPendingAuthorization()
      XCTFail("传输结果不确定不得假成功")
    } catch {
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .resultUncertain)
    }

    XCTAssertEqual(authentication.reasons.count, 1)
    XCTAssertEqual(harness.runtime.truthRefreshCount, 1)
    XCTAssertNil(harness.store.pendingAuthorization)
    XCTAssertNil(harness.store.successMessage)
    XCTAssertTrue(harness.store.errorMessage?.contains("结果不确定") == true)
  }

  func testBackgroundInvalidatesMemoryOnlyTicketBeforeBiometrics() async throws {
    let authentication = ExitPlanAuthenticationSpy()
    let repository = ExitPlanLoaderSpy(plan: makePlan())
    let harness = makeHarness(repository: repository, authentication: authentication)
    await harness.store.refresh()
    let plan = try XCTUnwrap(harness.store.listState.snapshot?.plans.first)
    try await harness.store.previewAuthorization(for: plan)

    harness.store.invalidateAuthorizationContext()

    XCTAssertNil(harness.store.pendingAuthorization)
    do {
      try await harness.store.confirmPendingAuthorization()
      XCTFail("后台后不得消费旧挑战")
    } catch {
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .contextChanged)
    }
    XCTAssertTrue(authentication.reasons.isEmpty)
    XCTAssertEqual(repository.confirmCount, 0)
  }

  func testPlanVersionOrScopeChangeImmediatelyInvalidatesTicket() async throws {
    let repository = ExitPlanLoaderSpy(plan: makePlan(configVersion: 7))
    let harness = makeHarness(repository: repository)
    await harness.store.refresh()
    let plan = try XCTUnwrap(harness.store.listState.snapshot?.plans.first)
    try await harness.store.previewAuthorization(for: plan)

    repository.plan = makePlan(configVersion: 8)
    await harness.store.refresh()
    XCTAssertNil(harness.store.pendingAuthorization)

    try await harness.store.previewAuthorization(
      for: try XCTUnwrap(harness.store.listState.snapshot?.plans.first)
    )
    harness.store.activate(
      identity: makeIdentity(scopes: ["orders:read", "liquidation:control"]),
      repository: repository
    )
    XCTAssertNil(harness.store.pendingAuthorization)
  }

  func testPaperPlanAndUnknownModeStayReadOnlyWithoutPreviewRequest() async {
    for plan in [makePlan(mode: .paper), makePlan(mode: .unknown("FUTURE"))] {
      let repository = ExitPlanLoaderSpy(plan: plan)
      let harness = makeHarness(repository: repository)
      await harness.store.refresh()
      let loaded = harness.store.listState.snapshot!.plans[0]

      do {
        try await harness.store.previewAuthorization(for: loaded)
        XCTFail("PAPER 或未知模式不得请求实盘授权")
      } catch {
        XCTAssertNotNil(error as? ExitPlanWorkspaceError)
      }
      XCTAssertEqual(repository.previewCount, 0)
    }
  }

  private func makeHarness(
    repository: ExitPlanLoaderSpy,
    authentication: ExitPlanAuthenticationSpy = ExitPlanAuthenticationSpy(),
    scopes: Set<String> = ["orders:read", "liquidation:control", "trade:approve"]
  ) -> (store: ExitPlanWorkspace, runtime: ExitPlanRuntimeSpy) {
    let runtime = ExitPlanRuntimeSpy()
    let store = ExitPlanWorkspace(localAuthentication: authentication)
    store.configure(
      contextProvider: {
        ExitPlanRuntimeContext(
          accountID: runtime.accountID,
          localSessionLocked: runtime.locked,
          accountDataEnabled: runtime.enabled
        )
      },
      refreshSession: { runtime.sessionRefreshCount += 1 },
      refreshTradingTruth: { runtime.truthRefreshCount += 1 }
    )
    store.activate(identity: makeIdentity(scopes: scopes), repository: repository)
    return (store, runtime)
  }

  private func makeIdentity(
    activeAccountID: String? = "ACCOUNT-1",
    authorizedAccountIDs: Set<String> = ["ACCOUNT-1"],
    scopes: Set<String> = ["orders:read", "liquidation:control", "trade:approve"]
  ) -> ExitPlanWorkspace.SessionIdentity {
    ExitPlanWorkspace.SessionIdentity(
      userID: "user-1",
      deviceSessionID: "session-1",
      activeAccountID: activeAccountID,
      authorizedAccountIDs: authorizedAccountIDs,
      grantedScopes: scopes
    )
  }

  private func makePlan(
    mode: ExitPlanExecutionMode = .live,
    configVersion: Int = 7
  ) -> ExitPlanItem {
    ExitPlanItem(
      id: "plan-1",
      groupID: nil,
      accountID: "ACCOUNT-1",
      instrumentCode: "600519.SH",
      bucket: "core",
      sourceType: "MANUAL",
      sourceID: "source-1",
      strategyRunID: nil,
      enabled: true,
      status: .active,
      executionMode: mode,
      autoExitAuthorized: false,
      autoExitAuthorizationConfigVersion: nil,
      autoExitAuthorizationExpiresAt: nil,
      configVersion: configVersion,
      completionStrategy: "UNTIL_SNAPSHOT_CLEARED",
      completionNote: nil,
      protectedVolume: 500,
      exitedVolume: 100,
      remainingVolume: 400,
      entryAveragePrice: 1_500,
      rules: .object([.init(key: "rule_type", value: .string("TARGET_PRICE"))]),
      metadata: .object([]),
      canEditRules: false,
      editRoute: nil,
      phase: "MONITORING",
      dataQuality: "VALID",
      lastDecision: nil,
      peakPrice: 1_600,
      peakDrawdownPercent: 2,
      trailingFloorPercent: nil,
      pendingClientOrderID: nil,
      pendingIntentID: nil,
      lastEvaluatedAt: nil,
      lastError: nil,
      createdAt: nil,
      updatedAt: Date()
    )
  }
}

@MainActor
private final class ExitPlanRuntimeSpy {
  var accountID: String? = "ACCOUNT-1"
  var locked = false
  var enabled = true
  var sessionRefreshCount = 0
  var truthRefreshCount = 0
}

@MainActor
private final class ExitPlanAuthenticationSpy: LocalAuthenticationProviding {
  var tradeAuthorizationAvailable = true
  private(set) var reasons: [String] = []

  func unlock(reason: String) async throws {}

  func authorizeTrade(reason: String) async throws {
    reasons.append(reason)
  }
}

@MainActor
private final class ExitPlanLoaderSpy: ExitPlanLoading {
  var plan: ExitPlanItem
  var loadErrors: [ReadOnlyRepositoryError] = []
  var confirmErrors: [ReadOnlyRepositoryError] = []
  private(set) var loadCount = 0
  private(set) var previewCount = 0
  private(set) var confirmCount = 0

  init(plan: ExitPlanItem) {
    self.plan = plan
  }

  func loadPlans(context: ExitPlanRepositoryContext) async throws -> ExitPlanListSnapshot {
    loadCount += 1
    if !loadErrors.isEmpty { throw loadErrors.removeFirst() }
    return ExitPlanListSnapshot(
      accountID: context.activeAccountID,
      plans: [plan],
      capabilities: ExitPlanCapabilitiesSnapshot(
        ruleTypes: [],
        completionStrategies: ["UNTIL_SNAPSHOT_CLEARED"],
        conflictStrategies: ["UNALLOCATED_ONLY"],
        executionModes: ["paper", "live"],
        ruleSemantics: "所有规则由服务端统一评估"
      ),
      fetchedAt: Date()
    )
  }

  func loadDetail(
    plan: ExitPlanItem,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanDetailSnapshot {
    ExitPlanDetailSnapshot(
      plan: plan,
      capacity: ExitPlanHoldingCapacitySnapshot(
        accountID: context.activeAccountID,
        instrumentCode: plan.instrumentCode,
        totalVolume: 1_000,
        availableVolume: 800,
        frozenVolume: 0,
        protectedVolume: 500,
        pendingVolume: 0,
        unallocatedVolume: 500,
        conflicts: []
      ),
      events: [],
      fetchedAt: Date()
    )
  }

  func previewAuthorization(
    plan: ExitPlanItem,
    idempotencyKey: UUID,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationTicket {
    previewCount += 1
    let expiry = Date().addingTimeInterval(7 * 86_400)
    let review = ExitPlanAuthorizationReview(
      id: "challenge-\(previewCount)",
      accountID: context.activeAccountID,
      planID: plan.id,
      instrumentCode: plan.instrumentCode,
      bucket: plan.bucket,
      sourceType: plan.sourceType,
      executionMode: .live,
      configVersion: plan.configVersion,
      protectedVolume: plan.protectedVolume,
      exitedVolume: plan.exitedVolume,
      remainingVolume: plan.remainingVolume,
      rules: plan.rules,
      t1Policy: "WAIT_UNTIL_SELLABLE",
      executionPolicy: .object([]),
      position: ExitPlanAuthorizationPositionSnapshot(
        totalVolume: 1_000,
        availableVolume: 800,
        frozenVolume: 0,
        yesterdayVolume: 800,
        t1UnavailableVolume: 200,
        updatedAt: Date()
      ),
      otherProtections: [],
      readiness: .object([]),
      authorizationFingerprint: String(repeating: "a", count: 64),
      authorizationExpiresAt: expiry,
      challengeExpiresAt: Date().addingTimeInterval(60),
      warnings: []
    )
    return ExitPlanAuthorizationTicket(
      review: review,
      confirmationToken: "memory-only-token",
      idempotencyKey: idempotencyKey,
      userID: context.userID,
      deviceSessionID: context.deviceSessionID,
      sessionContextID: context.sessionContextID
    )
  }

  func confirmAuthorization(
    _ ticket: ExitPlanAuthorizationTicket,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationConfirmation {
    confirmCount += 1
    if !confirmErrors.isEmpty { throw confirmErrors.removeFirst() }
    return ExitPlanAuthorizationConfirmation(
      challengeID: ticket.review.id,
      planID: ticket.review.planID,
      configVersion: ticket.review.configVersion,
      authorizationExpiresAt: ticket.review.authorizationExpiresAt,
      auditEventID: "audit-1",
      message: "精确授权已确认"
    )
  }
}
