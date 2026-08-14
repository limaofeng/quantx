import XCTest

@testable import QuantX

@MainActor
final class LiquidationRepositoryTests: XCTestCase {
  func testPreviewRequestRequiresUniquePrimaryAccountAndExactScopeShape() throws {
    let context = makeContext(codes: ["600519.SH", "000001.SZ"])
    let request = makeRequest(scope: .single, codes: ["600519.SH"])

    XCTAssertNoThrow(try LiquidationRepository.validate(request, context: context))

    XCTAssertThrowsError(
      try LiquidationRepository.validate(
        makeRequest(scope: .single, codes: ["600519.SH", "000001.SZ"]),
        context: context
      )
    )
    XCTAssertThrowsError(
      try LiquidationRepository.validate(
        makeRequest(scope: .selected, codes: ["600519.SH", "600519.SH"]),
        context: context
      )
    )
    XCTAssertThrowsError(
      try LiquidationRepository.validate(
        makeRequest(scope: .all, codes: ["600519.SH"]),
        context: context
      )
    )
    XCTAssertThrowsError(
      try LiquidationRepository.validate(
        request,
        context: LiquidationRepositoryContext(
          activeAccountID: "ACCOUNT-1",
          authorizedAccountIDs: ["ACCOUNT-1", "ACCOUNT-2"],
          portfolioInstrumentCodes: context.portfolioInstrumentCodes,
          contextID: context.contextID
        )
      )
    )
    let oversizedPortfolio = Set(
      (0..<201).map { String(format: "%06d.SH", $0) }
    )
    XCTAssertThrowsError(
      try LiquidationRepository.validate(
        makeRequest(scope: .all, codes: []),
        context: makeContext(codes: oversizedPortfolio)
      )
    )
  }

  func testPreviewItemRejectsClientUnsafeQuantityRelationships() throws {
    let conflict = try LiquidationRepository.mapConflict(
      planID: "plan-existing",
      sourceType: "STRATEGY",
      status: "ACTIVE",
      remainingVolume: 100,
      configVersion: 2,
      pending: false
    )

    XCTAssertThrowsError(
      try LiquidationRepository.mapItem(
        instrumentCode: "600519.SH",
        instrumentName: "贵州茅台",
        totalVolume: 100,
        availableVolume: 100,
        frozenVolume: 0,
        t1UnavailableVolume: 0,
        protectedVolume: 100,
        pendingSellVolume: 0,
        maxProtectedVolume: 101,
        included: true,
        reasonCode: "INCLUDED",
        reasonDetail: "纳入",
        positionUpdatedAt: "2026-08-15T00:00:00Z",
        conflicts: [conflict]
      )
    )

    XCTAssertThrowsError(
      try LiquidationRepository.mapItem(
        instrumentCode: "600519.SH",
        instrumentName: "贵州茅台",
        totalVolume: 300,
        availableVolume: 200,
        frozenVolume: 100,
        t1UnavailableVolume: 100,
        protectedVolume: 100,
        pendingSellVolume: 0,
        maxProtectedVolume: 100,
        included: true,
        reasonCode: "INCLUDED",
        reasonDetail: "纳入",
        positionUpdatedAt: "2026-08-15T00:00:00Z",
        conflicts: [conflict]
      )
    )
  }

  func testAllScopeUsesSignedItemsAsTheCompletePortfolioSet() throws {
    let context = makeContext(codes: ["000001.SZ", "600519.SH"])
    let request = makeRequest(scope: .all, codes: [])
    let items = [
      try makeItem(code: "000001.SZ", maxProtectedVolume: 100),
      try makeItem(code: "600519.SH", maxProtectedVolume: 100),
    ]

    let preview = try LiquidationRepository.mapPreview(
      challengeID: UUID().uuidString.lowercased(),
      confirmationToken: "memory-only-token",
      groupID: "group-1",
      accountID: "ACCOUNT-1",
      scope: .all,
      instrumentCodes: [],
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper,
      idempotencyKey: request.idempotencyKeyValue,
      snapshotVersion: String(repeating: "a", count: 64),
      accountUpdatedAt: "2026-08-15T00:00:00Z",
      rolloutSnapshotID: nil,
      rolloutSnapshotHash: nil,
      challengeExpiresAt: "2035-08-15T00:00:00Z",
      includedCount: 2,
      skippedCount: 0,
      items: items,
      warnings: [],
      request: request,
      context: context
    )

    XCTAssertTrue(preview.instrumentCodes.isEmpty)
    XCTAssertEqual(Set(preview.signedInstrumentCodes), context.portfolioInstrumentCodes)

    XCTAssertThrowsError(
      try LiquidationRepository.mapPreview(
        challengeID: UUID().uuidString.lowercased(),
        confirmationToken: "memory-only-token",
        groupID: "group-1",
        accountID: "ACCOUNT-1",
        scope: .all,
        instrumentCodes: [],
        completionStrategy: .availableNow,
        conflictStrategy: .unallocatedOnly,
        executionMode: .paper,
        idempotencyKey: request.idempotencyKeyValue,
        snapshotVersion: String(repeating: "b", count: 64),
        accountUpdatedAt: "2026-08-15T00:00:00Z",
        rolloutSnapshotID: nil,
        rolloutSnapshotHash: nil,
        challengeExpiresAt: "2035-08-15T00:00:00Z",
        includedCount: 1,
        skippedCount: 0,
        items: [items[0]],
        warnings: [],
        request: request,
        context: context
      )
    )
  }

  func testLivePreviewFailsClosedWithoutRolloutSnapshot() throws {
    let context = makeContext(codes: ["600519.SH"])
    let request = makeRequest(scope: .single, codes: ["600519.SH"], mode: .live)

    XCTAssertThrowsError(
      try LiquidationRepository.mapPreview(
        challengeID: UUID().uuidString.lowercased(),
        confirmationToken: "memory-only-token",
        groupID: "group-1",
        accountID: "ACCOUNT-1",
        scope: .single,
        instrumentCodes: ["600519.SH"],
        completionStrategy: .availableNow,
        conflictStrategy: .unallocatedOnly,
        executionMode: .live,
        idempotencyKey: request.idempotencyKeyValue,
        snapshotVersion: String(repeating: "c", count: 64),
        accountUpdatedAt: "2026-08-15T00:00:00Z",
        rolloutSnapshotID: nil,
        rolloutSnapshotHash: nil,
        challengeExpiresAt: "2035-08-15T00:00:00Z",
        includedCount: 1,
        skippedCount: 0,
        items: [try makeItem(code: "600519.SH", maxProtectedVolume: 100)],
        warnings: [],
        request: request,
        context: context
      )
    )
  }

  func testPartialConfirmationPreservesSuccessfulAndFailedPlanResults() throws {
    let preview = makeDirectPreview(
      items: [
        try makeItem(code: "000001.SZ", maxProtectedVolume: 100),
        try makeItem(code: "600519.SH", maxProtectedVolume: 200),
      ]
    )
    let created = try LiquidationRepository.mapPlan(
      instrumentCode: "000001.SZ",
      success: true,
      planID: "plan-created",
      protectedVolume: 100,
      conflictPlanIDs: [],
      error: nil,
      preview: preview
    )
    let rejected = try LiquidationRepository.mapPlan(
      instrumentCode: "600519.SH",
      success: false,
      planID: nil,
      protectedVolume: nil,
      conflictPlanIDs: [],
      error: "Engine 风控拒绝",
      preview: preview
    )

    let result = try LiquidationRepository.mapConfirmation(
      success: false,
      code: "LIQUIDATION_PARTIAL",
      message: "部分完成",
      challengeID: preview.id,
      groupID: preview.groupID,
      commandID: "command-1",
      status: "SUCCEEDED",
      createdCount: 1,
      failedCount: 1,
      plans: [created, rejected],
      preview: preview
    )

    XCTAssertTrue(result.isPartial)
    XCTAssertEqual(result.status, .succeeded)
    XCTAssertEqual(result.plans.filter(\.success).map(\.instrumentCode), ["000001.SZ"])
    XCTAssertEqual(result.plans.filter { !$0.success }.map(\.instrumentCode), ["600519.SH"])
  }

  func testPendingConfirmationOnlyMeansEngineCommandQueued() throws {
    let preview = makeDirectPreview(
      items: [try makeItem(code: "600519.SH", maxProtectedVolume: 100)]
    )

    let result = try LiquidationRepository.mapConfirmation(
      success: true,
      code: "LIQUIDATION_QUEUED",
      message: "queued",
      challengeID: preview.id,
      groupID: preview.groupID,
      commandID: "command-1",
      status: "PENDING",
      createdCount: 0,
      failedCount: 0,
      plans: [],
      preview: preview
    )

    XCTAssertEqual(result.status.title, "Engine 命令已排队")
    XCTAssertTrue(result.status.allowsRecovery)
    XCTAssertEqual(result.createdCount, 0)
  }

  func testResultRecoveryKeepsAccountAndContextBoundAfterPositionClears() throws {
    let item = try makeItem(code: "600519.SH", maxProtectedVolume: 100)
    let original = makeDirectPreview(items: [item])
    let context = LiquidationRepositoryContext(
      activeAccountID: original.accountID,
      authorizedAccountIDs: [original.accountID],
      portfolioInstrumentCodes: [],
      contextID: original.contextID
    )

    XCTAssertThrowsError(
      try LiquidationRepository.validateConfirmationContext(
        original,
        context: context,
        resultRecovery: false
      )
    )
    XCTAssertNoThrow(
      try LiquidationRepository.validateConfirmationContext(
        original,
        context: context,
        resultRecovery: true
      )
    )
    XCTAssertThrowsError(
      try LiquidationRepository.validateConfirmationContext(
        original,
        context: LiquidationRepositoryContext(
          activeAccountID: "ACCOUNT-2",
          authorizedAccountIDs: ["ACCOUNT-2"],
          portfolioInstrumentCodes: [],
          contextID: original.contextID
        ),
        resultRecovery: true
      )
    )
  }

  private func makeContext(codes: Set<String>) -> LiquidationRepositoryContext {
    LiquidationRepositoryContext(
      activeAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"],
      portfolioInstrumentCodes: codes,
      contextID: UUID()
    )
  }

  private func makeRequest(
    scope: LiquidationScope,
    codes: [String],
    mode: LiquidationExecutionMode = .paper
  ) -> LiquidationPreviewRequest {
    LiquidationPreviewRequest(
      accountID: "ACCOUNT-1",
      scope: scope,
      instrumentCodes: codes,
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: mode,
      idempotencyKey: UUID()
    )
  }

  private func makeItem(
    code: String,
    maxProtectedVolume: Int
  ) throws -> LiquidationPreviewItem {
    try LiquidationRepository.mapItem(
      instrumentCode: code,
      instrumentName: nil,
      totalVolume: maxProtectedVolume,
      availableVolume: maxProtectedVolume,
      frozenVolume: 0,
      t1UnavailableVolume: 0,
      protectedVolume: 0,
      pendingSellVolume: 0,
      maxProtectedVolume: maxProtectedVolume,
      included: maxProtectedVolume > 0,
      reasonCode: "INCLUDED",
      reasonDetail: "服务端纳入退出计划预览",
      positionUpdatedAt: "2026-08-15T00:00:00Z",
      conflicts: []
    )
  }

  private func makeDirectPreview(items: [LiquidationPreviewItem]) -> LiquidationPreviewTicket {
    LiquidationPreviewTicket(
      id: UUID().uuidString.lowercased(),
      confirmationToken: "memory-only-token",
      contextID: UUID(),
      groupID: "group-1",
      accountID: "ACCOUNT-1",
      scope: .selected,
      instrumentCodes: items.map(\.instrumentCode).sorted(),
      completionStrategy: .availableNow,
      conflictStrategy: .unallocatedOnly,
      executionMode: .paper,
      idempotencyKey: UUID(),
      snapshotVersion: String(repeating: "d", count: 64),
      accountUpdatedAt: Date(),
      rolloutSnapshotID: nil,
      rolloutSnapshotHash: nil,
      challengeExpiresAt: Date().addingTimeInterval(60),
      includedCount: items.count,
      skippedCount: 0,
      items: items,
      warnings: []
    )
  }
}
