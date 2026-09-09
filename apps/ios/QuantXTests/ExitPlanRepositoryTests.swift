import XCTest

@testable import QuantX

@MainActor
final class ExitPlanRepositoryTests: XCTestCase {
  func testPlanMappingRequiresExactUniqueAccountAndPreservesUnknownEnums() throws {
    var raw = makeRawPlan()
    raw = replacing(raw, status: "FUTURE_STATE", environment: "FUTURE_MODE")

    let plan = try ExitPlanRepository.mapPlan(raw, context: makeContext())

    XCTAssertEqual(plan.accountID, "ACCOUNT-1")
    XCTAssertEqual(plan.status, .unknown("FUTURE_STATE"))
    XCTAssertEqual(plan.environment, .unknown("FUTURE_MODE"))
    XCTAssertEqual(plan.stateVersion, 9)
    XCTAssertEqual(
      plan.executionOwner,
      ExecutionOwnerRef(ownerType: .exitPlan, ownerID: "plan-1")
    )
    XCTAssertEqual(plan.rules.topLevelFields.first?.key, "rules")
  }

  func testPlanMappingRejectsCrossAccountResponse() {
    XCTAssertThrowsError(
      try ExitPlanRepository.mapPlan(
        replacing(makeRawPlan(), accountID: "ACCOUNT-2"),
        context: makeContext()
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .accountScopeMismatch)
    }
  }

  func testPlanMappingRejectsNonExitPlanExecutionOwner() {
    XCTAssertThrowsError(
      try ExitPlanRepository.mapPlan(
        replacing(
          makeRawPlan(),
          executionOwnerType: "STRATEGY_RUN",
          sourceExecutionOwnerType: "STRATEGY_RUN"
        ),
        context: makeContext()
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .invalidResponse)
    }
  }

  func testPlanMappingKeepsUnknownSourceOwnerVisibleForAudit() throws {
    let raw = replacing(
      makeRawPlan(),
      sourceExecutionOwnerType: "FUTURE_OWNER"
    )

    let plan = try ExitPlanRepository.mapPlan(raw, context: makeContext())

    XCTAssertEqual(
      plan.executionOwner,
      ExecutionOwnerRef(ownerType: .exitPlan, ownerID: "plan-1")
    )
    XCTAssertEqual(
      plan.sourceExecutionOwner,
      ExecutionOwnerRef(ownerType: .unknown("FUTURE_OWNER"), ownerID: "source-1")
    )
    XCTAssertNil(plan.strategyRunID)
  }

  func testPlanMappingAcceptsStrategyRunSourceOwner() throws {
    let raw = replacing(
      makeRawPlan(),
      sourceType: "T_TRADE_BATCH",
      strategyRunID: "t-run-1",
      executionOwnerType: "EXIT_PLAN",
      sourceExecutionOwnerType: "STRATEGY_RUN",
      sourceExecutionOwnerID: "t-run-1"
    )

    let plan = try ExitPlanRepository.mapPlan(raw, context: makeContext())

    XCTAssertEqual(plan.executionOwner.ownerType, .exitPlan)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerType, .strategyRun)
    XCTAssertEqual(plan.strategyRunID, "t-run-1")
  }

  func testPlanMappingAllowsStrategyRunSourceOwnerWithoutLegacyWitness() throws {
    let plan = try ExitPlanRepository.mapPlan(
      replacing(
        makeRawPlan(),
        sourceExecutionOwnerType: "STRATEGY_RUN",
        sourceExecutionOwnerID: "source-run-1"
      ),
      context: makeContext()
    )

    XCTAssertNil(plan.strategyRunID)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerType, .strategyRun)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerID, "source-run-1")
  }

  func testPlanMappingAcceptsStrategyRunSourceOwnerForBoardPromotion() throws {
    let raw = replacing(
      makeRawPlan(),
      sourceType: "FIRST_BOARD_PROMOTION_V2",
      strategyRunID: "first-board-run-1",
      executionOwnerType: "EXIT_PLAN",
      sourceExecutionOwnerType: "STRATEGY_RUN",
      sourceExecutionOwnerID: "first-board-run-1"
    )

    let plan = try ExitPlanRepository.mapPlan(raw, context: makeContext())

    XCTAssertEqual(plan.executionOwner.ownerType, .exitPlan)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerType, .strategyRun)
    XCTAssertEqual(plan.strategyRunID, "first-board-run-1")
  }

  func testPlanMappingAcceptsManualCommandSourceOwner() throws {
    let raw = replacing(makeRawPlan(), sourceType: "MANUAL_LIQUIDATION")

    let plan = try ExitPlanRepository.mapPlan(raw, context: makeContext())

    XCTAssertEqual(plan.executionOwner.ownerType, .exitPlan)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerType, .manualCommand)
    XCTAssertNil(plan.strategyRunID)
  }

  func testPlanMappingUsesTypedSourceOwnerForNonmanualSource() throws {
    let plan = try ExitPlanRepository.mapPlan(
      replacing(
        makeRawPlan(),
        sourceType: "T_TRADE_BATCH",
        sourceExecutionOwnerType: "T_ASSISTANT_EXECUTION",
        sourceExecutionOwnerID: "t-execution-1"
      ),
      context: makeContext()
    )

    XCTAssertEqual(plan.executionOwner.ownerType, .exitPlan)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerType, .tAssistantExecution)
    XCTAssertEqual(plan.sourceExecutionOwner.ownerID, "t-execution-1")
  }

  func testPlanMappingRejectsUnknownExecutionOwner() {
    XCTAssertThrowsError(
      try ExitPlanRepository.mapPlan(
        replacing(makeRawPlan(), executionOwnerType: "FUTURE_OWNER"),
        context: makeContext()
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .invalidResponse)
    }
  }

  func testPlanMappingRejectsExecutionOwnerIdThatDoesNotMatchPlan() {
    XCTAssertThrowsError(
      try ExitPlanRepository.mapPlan(
        replacing(makeRawPlan(), executionOwnerID: "another-plan"),
        context: makeContext()
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .invalidResponse)
    }
  }

  func testPlanMappingRejectsOverlongRunIdentityInsteadOfTreatingItAsMissing() {
    XCTAssertThrowsError(
      try ExitPlanRepository.mapPlan(
        replacing(
          makeRawPlan(),
          strategyRunID: String(repeating: "r", count: 161)
        ),
        context: makeContext()
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .invalidResponse)
    }
  }

  func testPlanMappingRejectsMalformedExactAuthorizationFields() {
    let raw = replacing(
      makeRawPlan(),
      environment: "paper",
      autoExitAuthorized: true,
      authorizationVersion: 7,
      authorizationExpiry: "2027-01-20T00:00:00Z"
    )

    XCTAssertThrowsError(try ExitPlanRepository.mapPlan(raw, context: makeContext())) {
      XCTAssertEqual($0 as? ExitPlanWorkspaceError, .invalidResponse)
    }
  }

  func testAuthorizationPreviewBindsRulesAccountPlanVersionPositionAndSession() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let plan = try ExitPlanRepository.mapPlan(makeRawPlan(), context: makeContext())
    let idempotencyKey = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!

    let ticket = try ExitPlanRepository.mapAuthorizationPreview(
      makeRawPreview(now: now),
      plan: plan,
      idempotencyKey: idempotencyKey,
      context: makeContext(),
      now: now
    )

    XCTAssertEqual(ticket.review.planID, plan.id)
    XCTAssertEqual(ticket.review.rules, plan.rules)
    XCTAssertEqual(ticket.review.position.t1UnavailableVolume, 200)
    XCTAssertEqual(ticket.review.otherProtections.first?.configVersion, 3)
    XCTAssertEqual(ticket.userID, "user-1")
    XCTAssertEqual(ticket.deviceSessionID, "session-1")
    XCTAssertEqual(ticket.sessionContextID, makeContext().sessionContextID)
    XCTAssertEqual(ticket.idempotencyKey, idempotencyKey)
  }

  func testAuthorizationPreviewRejectsRulesDifferentFromCurrentPlan() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let plan = try ExitPlanRepository.mapPlan(makeRawPlan(), context: makeContext())
    var preview = makeRawPreview(now: now)
    preview = replacing(
      preview,
      rules: GraphQLJSON(object: ["rules": .array([.string("MUTATED")])])
    )

    XCTAssertThrowsError(
      try ExitPlanRepository.mapAuthorizationPreview(
        preview,
        plan: plan,
        idempotencyKey: UUID(),
        context: makeContext(),
        now: now
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .contextChanged)
    }
  }

  func testAuthorizationPreviewRequiresLowercaseSHA256Fingerprint() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let plan = try ExitPlanRepository.mapPlan(makeRawPlan(), context: makeContext())
    for fingerprint in [
      String(repeating: "A", count: 64),
      String(repeating: "a", count: 63),
      String(repeating: "g", count: 64),
    ] {
      let preview = replacing(makeRawPreview(now: now), fingerprint: fingerprint)
      XCTAssertThrowsError(
        try ExitPlanRepository.mapAuthorizationPreview(
          preview,
          plan: plan,
          idempotencyKey: UUID(),
          context: makeContext(),
          now: now
        )
      ) { error in
        XCTAssertEqual(error as? ExitPlanWorkspaceError, .invalidResponse)
      }
    }
  }

  func testAuthorizationPreviewRejectsExpiredChallengeAndSnapshotMismatch() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let plan = try ExitPlanRepository.mapPlan(makeRawPlan(), context: makeContext())
    let expired = replacing(
      makeRawPreview(now: now),
      challengeExpiresAt: iso(now.addingTimeInterval(-1))
    )

    XCTAssertThrowsError(
      try ExitPlanRepository.mapAuthorizationPreview(
        expired,
        plan: plan,
        idempotencyKey: UUID(),
        context: makeContext(),
        now: now
      )
    )

    let wrongVolume = replacing(makeRawPreview(now: now), protectedVolume: 499)
    XCTAssertThrowsError(
      try ExitPlanRepository.mapAuthorizationPreview(
        wrongVolume,
        plan: plan,
        idempotencyKey: UUID(),
        context: makeContext(),
        now: now
      )
    ) { error in
      XCTAssertEqual(error as? ExitPlanWorkspaceError, .contextChanged)
    }
  }

  func testTicketCannotCrossDeviceSessionOrAccount() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let context = makeContext()
    let plan = try ExitPlanRepository.mapPlan(makeRawPlan(), context: context)
    let ticket = try ExitPlanRepository.mapAuthorizationPreview(
      makeRawPreview(now: now),
      plan: plan,
      idempotencyKey: UUID(),
      context: context,
      now: now
    )
    let changed = ExitPlanRepositoryContext(
      userID: context.userID,
      deviceSessionID: "session-2",
      activeAccountID: context.activeAccountID,
      authorizedAccountIDs: context.authorizedAccountIDs,
      sessionContextID: context.sessionContextID
    )

    XCTAssertThrowsError(try ExitPlanRepository.validate(ticket: ticket, context: changed)) {
      XCTAssertEqual($0 as? ExitPlanWorkspaceError, .contextChanged)
    }
  }

  private func makeContext() -> ExitPlanRepositoryContext {
    ExitPlanRepositoryContext(
      userID: "user-1",
      deviceSessionID: "session-1",
      activeAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"],
      sessionContextID: UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
    )
  }

  private func makeRawPlan() -> ExitPlanRawPlan {
    ExitPlanRawPlan(
      planID: "plan-1",
      groupID: "group-1",
      accountID: "ACCOUNT-1",
      instrumentCode: "600519.SH",
      bucket: "core",
      sourceType: "MANUAL_POSITION",
      sourceID: "source-1",
      strategyRunID: nil,
      enabled: true,
      status: "ACTIVE",
      environment: "live",
      autoExitAuthorized: false,
      autoExitAuthorizationConfigVersion: nil,
      autoExitAuthorizationExpiresAt: nil,
      configVersion: 7,
      stateVersion: 9,
      executionOwner: ExitPlanRawExecutionOwner(
        ownerType: "EXIT_PLAN",
        ownerID: "plan-1"
      ),
      sourceExecutionOwner: ExitPlanRawExecutionOwner(
        ownerType: "MANUAL_COMMAND",
        ownerID: "source-1"
      ),
      completionStrategy: "UNTIL_SNAPSHOT_CLEARED",
      completionNote: nil,
      protectedVolume: 500,
      exitedVolume: 100,
      remainingVolume: 400,
      entryAveragePrice: 1_500,
      rules: GraphQLJSON(
        object: [
          "rules": .array([
            GraphQLJSON(object: ["rule_type": .string("TARGET_PRICE")])
          ])
        ]
      ),
      metadata: GraphQLJSON(object: [:]),
      canEditRules: true,
      editRoute: "manual",
      phase: "MONITORING",
      dataQuality: "VALID",
      lastDecision: nil,
      peakPrice: 1_600,
      peakDrawdownPercent: 2,
      trailingFloorPercent: 3,
      pendingClientOrderID: nil,
      pendingIntentID: nil,
      lastEvaluatedAt: "2027-01-10T00:00:00Z",
      lastError: nil,
      recoveryAction: nil,
      recoveryMessage: nil,
      createdAt: "2027-01-01T00:00:00Z",
      updatedAt: "2027-01-10T00:00:00Z"
    )
  }

  private func makeRawPreview(now: Date) -> ExitPlanRawAuthorizationPreview {
    ExitPlanRawAuthorizationPreview(
      challengeID: "challenge-1",
      confirmationToken: "memory-only-token",
      accountID: "ACCOUNT-1",
      planID: "plan-1",
      instrumentCode: "600519.SH",
      bucket: "core",
      sourceType: "MANUAL_POSITION",
      executionMode: "LIVE",
      configVersion: 7,
      protectedVolume: 500,
      exitedVolume: 100,
      remainingVolume: 400,
      rules: makeRawPlan().rules,
      t1Policy: "WAIT_UNTIL_SELLABLE",
      executionPolicy: GraphQLJSON(object: ["price_type": .string("LIMIT")]),
      position: ExitPlanRawAuthorizationPosition(
        totalVolume: 1_000,
        availableVolume: 700,
        frozenVolume: 100,
        yesterdayVolume: 800,
        t1UnavailableVolume: 200,
        updatedAt: iso(now)
      ),
      otherProtections: [
        ExitPlanRawAuthorizationConflict(
          planID: "plan-2",
          sourceType: "STRATEGY",
          status: "ACTIVE",
          remainingVolume: 100,
          configVersion: 3,
          pending: true
        )
      ],
      readiness: GraphQLJSON(object: ["kill_switch": .boolean(false)]),
      authorizationFingerprint: String(repeating: "a", count: 64),
      authorizationExpiresAt: iso(now.addingTimeInterval(7 * 86_400)),
      challengeExpiresAt: iso(now.addingTimeInterval(60)),
      warnings: ["规则变化会使授权失效"]
    )
  }

  private func replacing(
    _ raw: ExitPlanRawPlan,
    accountID: String? = nil,
    sourceType: String? = nil,
    status: String? = nil,
    environment: String? = nil,
    strategyRunID: String? = nil,
    executionOwnerType: String? = nil,
    executionOwnerID: String? = nil,
    sourceExecutionOwnerType: String? = nil,
    sourceExecutionOwnerID: String? = nil,
    autoExitAuthorized: Bool? = nil,
    authorizationVersion: Int? = nil,
    authorizationExpiry: String? = nil
  ) -> ExitPlanRawPlan {
    ExitPlanRawPlan(
      planID: raw.planID,
      groupID: raw.groupID,
      accountID: accountID ?? raw.accountID,
      instrumentCode: raw.instrumentCode,
      bucket: raw.bucket,
      sourceType: sourceType ?? raw.sourceType,
      sourceID: raw.sourceID,
      strategyRunID: strategyRunID ?? raw.strategyRunID,
      enabled: raw.enabled,
      status: status ?? raw.status,
      environment: environment ?? raw.environment,
      autoExitAuthorized: autoExitAuthorized ?? raw.autoExitAuthorized,
      autoExitAuthorizationConfigVersion: authorizationVersion
        ?? raw.autoExitAuthorizationConfigVersion,
      autoExitAuthorizationExpiresAt: authorizationExpiry
        ?? raw.autoExitAuthorizationExpiresAt,
      configVersion: raw.configVersion,
      stateVersion: raw.stateVersion,
      executionOwner: ExitPlanRawExecutionOwner(
        ownerType: executionOwnerType ?? raw.executionOwner.ownerType,
        ownerID: executionOwnerID ?? raw.executionOwner.ownerID
      ),
      sourceExecutionOwner: ExitPlanRawExecutionOwner(
        ownerType: sourceExecutionOwnerType ?? raw.sourceExecutionOwner.ownerType,
        ownerID: sourceExecutionOwnerID ?? raw.sourceExecutionOwner.ownerID
      ),
      completionStrategy: raw.completionStrategy,
      completionNote: raw.completionNote,
      protectedVolume: raw.protectedVolume,
      exitedVolume: raw.exitedVolume,
      remainingVolume: raw.remainingVolume,
      entryAveragePrice: raw.entryAveragePrice,
      rules: raw.rules,
      metadata: raw.metadata,
      canEditRules: raw.canEditRules,
      editRoute: raw.editRoute,
      phase: raw.phase,
      dataQuality: raw.dataQuality,
      lastDecision: raw.lastDecision,
      peakPrice: raw.peakPrice,
      peakDrawdownPercent: raw.peakDrawdownPercent,
      trailingFloorPercent: raw.trailingFloorPercent,
      pendingClientOrderID: raw.pendingClientOrderID,
      pendingIntentID: raw.pendingIntentID,
      lastEvaluatedAt: raw.lastEvaluatedAt,
      lastError: raw.lastError,
      recoveryAction: raw.recoveryAction,
      recoveryMessage: raw.recoveryMessage,
      createdAt: raw.createdAt,
      updatedAt: raw.updatedAt
    )
  }

  private func replacing(
    _ raw: ExitPlanRawAuthorizationPreview,
    rules: GraphQLJSON? = nil,
    fingerprint: String? = nil,
    protectedVolume: Int? = nil,
    challengeExpiresAt: String? = nil
  ) -> ExitPlanRawAuthorizationPreview {
    ExitPlanRawAuthorizationPreview(
      challengeID: raw.challengeID,
      confirmationToken: raw.confirmationToken,
      accountID: raw.accountID,
      planID: raw.planID,
      instrumentCode: raw.instrumentCode,
      bucket: raw.bucket,
      sourceType: raw.sourceType,
      executionMode: raw.executionMode,
      configVersion: raw.configVersion,
      protectedVolume: protectedVolume ?? raw.protectedVolume,
      exitedVolume: raw.exitedVolume,
      remainingVolume: raw.remainingVolume,
      rules: rules ?? raw.rules,
      t1Policy: raw.t1Policy,
      executionPolicy: raw.executionPolicy,
      position: raw.position,
      otherProtections: raw.otherProtections,
      readiness: raw.readiness,
      authorizationFingerprint: fingerprint ?? raw.authorizationFingerprint,
      authorizationExpiresAt: raw.authorizationExpiresAt,
      challengeExpiresAt: challengeExpiresAt ?? raw.challengeExpiresAt,
      warnings: raw.warnings
    )
  }

  private func iso(_ date: Date) -> String {
    ISO8601DateFormatter().string(from: date)
  }
}
