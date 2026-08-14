import XCTest

@testable import QuantX

final class ExitPlanModelsTests: XCTestCase {
  func testStructuredJSONPreservesNestedArraysObjectsAndScalarTypes() {
    let value = ExitPlanStructuredValue(
      graphQL: .array([
        GraphQLJSON(
          object: [
            "enabled": .boolean(true),
            "threshold": .number(1.25),
            "volume": .integer(100),
          ]
        ),
        .string("TARGET_PRICE"),
      ])
    )

    XCTAssertEqual(value.topLevelFields.count, 2)
    XCTAssertTrue(value.summary.contains("enabled：是"))
    XCTAssertTrue(value.summary.contains("TARGET_PRICE"))
  }

  func testUnknownServerEnumsRemainVisibleButFailClosed() {
    let status = ExitPlanStatus(serverValue: "future_state")
    let mode = ExitPlanExecutionMode(serverValue: "future_mode")

    XCTAssertEqual(status.title, "未知状态（FUTURE_STATE）")
    XCTAssertFalse(status.isAuthorizable)
    XCTAssertEqual(mode.title, "未知模式（FUTURE_MODE）")
  }

  func testAuthorizationStateRequiresLiveFlagExactVersionAndFutureExpiry() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let authorized = makePlan(
      autoExitAuthorized: true,
      authorizationVersion: 7,
      authorizationExpiry: now.addingTimeInterval(60),
      configVersion: 7
    )
    XCTAssertEqual(
      authorized.authorizationState(at: now),
      .authorized(expiresAt: now.addingTimeInterval(60))
    )

    let stale = makePlan(
      autoExitAuthorized: true,
      authorizationVersion: 6,
      authorizationExpiry: now.addingTimeInterval(60),
      configVersion: 7
    )
    XCTAssertEqual(stale.authorizationState(at: now), .staleVersion(authorizedVersion: 6))

    let expired = makePlan(
      autoExitAuthorized: true,
      authorizationVersion: 7,
      authorizationExpiry: now,
      configVersion: 7
    )
    XCTAssertEqual(expired.authorizationState(at: now), .expired(expiredAt: now))
  }

  func testPaperNeverPresentsLiveAuthorizationEvenWithMalformedFlag() {
    let plan = makePlan(
      mode: .paper,
      autoExitAuthorized: true,
      authorizationVersion: 7,
      authorizationExpiry: Date().addingTimeInterval(60),
      configVersion: 7
    )

    XCTAssertEqual(plan.authorizationState, .notApplicable)
  }

  func testProgressIsBoundedForDisplay() {
    XCTAssertEqual(makePlan(protected: 0, exited: 100).progressFraction, 0)
    XCTAssertEqual(makePlan(protected: 100, exited: 150).progressFraction, 1)
  }

  func testAuthorizationReviewExpiresExactlyAtTimelineBoundary() {
    let expiry = Date(timeIntervalSince1970: 1_800_000_000)
    let review = ExitPlanAuthorizationReview(
      id: "challenge-1",
      accountID: "ACCOUNT-1",
      planID: "plan-1",
      instrumentCode: "600519.SH",
      bucket: "core",
      sourceType: "MANUAL",
      executionMode: .live,
      configVersion: 7,
      protectedVolume: 500,
      exitedVolume: 100,
      remainingVolume: 400,
      rules: .object([]),
      t1Policy: "WAIT_UNTIL_SELLABLE",
      executionPolicy: .object([]),
      position: ExitPlanAuthorizationPositionSnapshot(
        totalVolume: 1_000,
        availableVolume: 800,
        frozenVolume: 0,
        yesterdayVolume: 800,
        t1UnavailableVolume: 200,
        updatedAt: nil
      ),
      otherProtections: [],
      readiness: .object([]),
      authorizationFingerprint: String(repeating: "a", count: 64),
      authorizationExpiresAt: expiry.addingTimeInterval(86_400),
      challengeExpiresAt: expiry,
      warnings: []
    )

    XCTAssertFalse(review.isChallengeExpired(at: expiry.addingTimeInterval(-0.001)))
    XCTAssertTrue(review.isChallengeExpired(at: expiry))
  }

  private func makePlan(
    mode: ExitPlanExecutionMode = .live,
    autoExitAuthorized: Bool = false,
    authorizationVersion: Int? = nil,
    authorizationExpiry: Date? = nil,
    configVersion: Int = 7,
    protected: Int = 500,
    exited: Int = 100
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
      autoExitAuthorized: autoExitAuthorized,
      autoExitAuthorizationConfigVersion: authorizationVersion,
      autoExitAuthorizationExpiresAt: authorizationExpiry,
      configVersion: configVersion,
      completionStrategy: "UNTIL_SNAPSHOT_CLEARED",
      completionNote: nil,
      protectedVolume: protected,
      exitedVolume: exited,
      remainingVolume: max(0, protected - exited),
      entryAveragePrice: 1_500,
      rules: .object([]),
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
      updatedAt: nil
    )
  }
}
