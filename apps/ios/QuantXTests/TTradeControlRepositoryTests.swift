import Foundation
import XCTest

@testable import QuantX

@MainActor
final class TTradeControlRepositoryTests: XCTestCase {
  func testPreviewMapperAcceptsOnlyExactKnownBoundContext() throws {
    let now = Date(timeIntervalSince1970: 1_786_752_000)
    let request = makeRequest(action: .activateCanary)
    let preview = try mapPreview(request: request, now: now)

    XCTAssertEqual(preview.action, .activateCanary)
    XCTAssertEqual(preview.accountID, request.accountID)
    XCTAssertEqual(preview.policyVersion, request.policyVersion)
    XCTAssertEqual(preview.snapshotID, request.snapshotID)
    XCTAssertEqual(preview.targetStage, .canary)
    XCTAssertEqual(preview.reason, "activate canary")
    XCTAssertEqual(preview.sessionContextID, context.sessionContextID)
  }

  func testPreviewMapperRejectsUnknownActionTargetAndEmptyTypedValues() {
    let now = Date(timeIntervalSince1970: 1_786_752_000)
    let request = makeRequest(action: .activateCanary)
    assertPreviewRejected(request: request, now: now, responseAction: "FUTURE_ACTION")
    assertPreviewRejected(request: request, now: now, target: "FUTURE_STAGE")
    assertPreviewRejected(request: request, now: now, token: "")
    assertPreviewRejected(request: request, now: now, fingerprint: "")
  }

  func testIdempotentPreviewWithoutReissuedTokenFailsClosed() {
    let request = makeRequest(action: .beginControlledWindow)
    XCTAssertThrowsError(
      try mapPreview(request: request, tokenIssued: false, token: nil)
    ) { error in
      XCTAssertEqual(error as? TTradeControlError, .duplicatePreviewUnavailable)
    }
  }

  func testPreviewExpiryAndExactCheckMismatchAreRejected() {
    let now = Date(timeIntervalSince1970: 1_786_752_000)
    let request = makeRequest(action: .activateLive)
    XCTAssertThrowsError(
      try mapPreview(
        request: request,
        now: now,
        expiresAt: iso(now.addingTimeInterval(-1))
      )
    ) { error in
      XCTAssertEqual(error as? TTradeControlError, .challengeExpired)
    }
    XCTAssertThrowsError(
      try mapPreview(request: request, now: now, checks: [])
    )
  }

  func testKillPreviewRequiresExactNonemptyReasonButNoReadinessChecks() throws {
    let now = Date(timeIntervalSince1970: 1_786_752_000)
    let request = makeRequest(action: .killSwitch, reason: "Agent 离线，立即人工处置")
    let preview = try mapPreview(request: request, now: now, checks: [])

    XCTAssertEqual(preview.reason, request.reason)
    XCTAssertTrue(preview.checks.isEmpty)
    XCTAssertEqual(preview.readinessStatus, "RISK_REDUCTION_READY")

    let emptyReason = makeRequest(action: .killSwitch, reason: "")
    XCTAssertThrowsError(try mapPreview(request: emptyReason, now: now, checks: []))
  }

  func testConfirmationRequiresExactAppliedShapeAndTreatsDispatchAsUncertain() throws {
    let preview = try mapPreview(request: makeRequest(action: .activateLive))
    let confirmation = try TTradeControlRepository.mapConfirmation(
      success: true,
      code: "LIVE_ACTIVATION_APPLIED",
      message: "已应用",
      challengeID: preview.id,
      accountID: preview.accountID,
      action: preview.action.rawValue,
      challengeConsumed: true,
      operationStatus: "APPLIED",
      readinessAccountID: preview.accountID,
      preview: preview
    )
    XCTAssertEqual(confirmation.operationStatus, "APPLIED")

    XCTAssertThrowsError(
      try TTradeControlRepository.mapConfirmation(
        success: false,
        code: "T_TRADE_CONTROL_DISPATCHING",
        message: "处理中",
        challengeID: preview.id,
        accountID: preview.accountID,
        action: preview.action.rawValue,
        challengeConsumed: true,
        operationStatus: "DISPATCHING",
        readinessAccountID: nil,
        preview: preview
      )
    ) { error in
      XCTAssertEqual(error as? TTradeControlError, .resultUncertain)
    }
  }

  func testSnapshotAllowsOfflineAgentForKillButPreservesFailClosedFields() throws {
    let snapshot = try makeSnapshot(
      agentStatus: "OFFLINE",
      agentMode: "",
      protocolVersion: "",
      ready: false,
      checks: []
    )
    XCTAssertEqual(snapshot.agentStatus, "OFFLINE")
    XCTAssertEqual(snapshot.protocolVersion, "")
    XCTAssertFalse(snapshot.ready)
    XCTAssertEqual(snapshot.policyVersion, 7)
    XCTAssertEqual(snapshot.accountID, "ACCOUNT-1")
  }

  private var context: TTradeControlRepositoryContext {
    TTradeControlRepositoryContext(
      userID: "user-1",
      deviceSessionID: "session-1",
      activeAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"],
      sessionContextID: UUID(uuidString: "11111111-2222-3333-4444-555555555555")!
    )
  }

  private func makeRequest(
    action: TTradeSafetyAction,
    reason: String? = nil
  ) -> TTradeControlPreviewRequest {
    let checks = action == .killSwitch ? [] : [Self.agentCheck]
    return TTradeControlPreviewRequest(
      accountID: "ACCOUNT-1",
      action: action,
      policyVersion: 7,
      snapshotID: action == .killSwitch ? "" : "snapshot-1",
      targetStage: action.targetStage,
      reason: reason ?? action.serverReason,
      idempotencyKey: UUID(uuidString: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")!,
      expectedStage: "SHADOW",
      expectedReadinessStatus: action == .killSwitch ? "RISK_REDUCTION_READY" : "READY",
      expectedChecks: checks,
      snapshotBinding: "ACCOUNT-1|7|snapshot-1|hash|SHADOW|READY|0||0"
    )
  }

  private func mapPreview(
    request: TTradeControlPreviewRequest,
    now: Date = Date(timeIntervalSince1970: 1_786_752_000),
    responseAction: String? = nil,
    target: String? = nil,
    tokenIssued: Bool = true,
    token: String? = "memory-only-token",
    fingerprint: String = String(repeating: "a", count: 64),
    expiresAt: String? = nil,
    checks: [TTradeSafetyCheck]? = nil
  ) throws -> TTradeControlPreviewTicket {
    try TTradeControlRepository.mapPreview(
      challengeID: "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb",
      confirmationToken: token,
      tokenIssued: tokenIssued,
      responseAccountID: request.accountID,
      responseAction: responseAction ?? request.action.rawValue,
      responsePolicyVersion: request.policyVersion,
      responseSnapshotID: request.snapshotID,
      responseTargetStage: target ?? request.targetStage?.rawValue,
      responseReason: request.reason,
      currentStage: request.expectedStage,
      readinessStatus: request.expectedReadinessStatus,
      readinessFingerprint: fingerprint,
      expiresAt: expiresAt ?? iso(now.addingTimeInterval(60)),
      challengeStatus: "PENDING",
      operationStatus: "PENDING",
      checks: checks ?? request.expectedChecks,
      warnings: ["确认不代表委托或成交"],
      request: request,
      context: context,
      now: now
    )
  }

  private func assertPreviewRejected(
    request: TTradeControlPreviewRequest,
    now: Date,
    responseAction: String? = nil,
    target: String? = nil,
    token: String? = "memory-only-token",
    fingerprint: String = String(repeating: "a", count: 64),
    file: StaticString = #filePath,
    line: UInt = #line
  ) {
    XCTAssertThrowsError(
      try mapPreview(
        request: request,
        now: now,
        responseAction: responseAction,
        target: target,
        token: token,
        fingerprint: fingerprint
      ),
      file: file,
      line: line
    )
  }

  private func makeSnapshot(
    agentStatus: String,
    agentMode: String,
    protocolVersion: String,
    ready: Bool,
    checks: [TTradeSafetyCheck]
  ) throws -> TTradeControlSnapshot {
    let now = Date(timeIntervalSince1970: 1_786_752_000)
    return try TTradeControlRepository.mapSnapshot(
      requestedAccountID: "ACCOUNT-1",
      monitorAccountID: "ACCOUNT-1",
      monitorEnabled: false,
      monitorMode: "live",
      monitorStage: "SHADOW",
      monitorKillSwitch: false,
      pendingSignalCount: 0,
      activeBatchCount: 0,
      drainingCount: 0,
      positionSnapshotSource: "QMT_AGENT",
      positionSnapshotReportedAt: iso(now.addingTimeInterval(-20)),
      positionSnapshotReceivedAt: iso(now.addingTimeInterval(-19)),
      positionSnapshotComplete: true,
      positionSnapshotError: nil,
      projectionGeneratedAt: iso(now.addingTimeInterval(-5)),
      readinessAccountID: "ACCOUNT-1",
      ready: ready,
      status: ready ? "READY" : "BLOCKED",
      preparationReady: ready,
      automationReady: ready,
      readinessStage: "SHADOW",
      engineStatus: "READY",
      agentStatus: agentStatus,
      agentDeviceID: nil,
      agentMode: agentMode,
      protocolVersion: protocolVersion,
      reconcileStatus: "READY",
      readinessKillSwitch: false,
      policyVersion: 7,
      canApprove: ready,
      canActivateLive: ready,
      blockedReasons: ready ? [] : ["Agent 离线"],
      preparationBlockedReasons: ready ? [] : ["Agent 离线"],
      checks: checks,
      snapshotID: "snapshot-1",
      snapshotHash: String(repeating: "b", count: 64),
      snapshotAt: iso(now.addingTimeInterval(-20)),
      reconciliationAgeSeconds: 20,
      controlledWindowActive: false,
      controlledWindowSnapshotID: nil,
      controlledWindowStartedAt: nil,
      queuedCommandCount: 0,
      queueDelaySeconds: 0,
      deadLetterCount: 0,
      unresolvedCriticalAlertCount: 0,
      manualCoexistence: false,
      externalOrderCount: 0,
      externalTradeCount: 0,
      newExternalOrderCount: 0,
      newExternalTradeCount: 0,
      workingExternalOrderCount: 0,
      journalIntegrity: "HEALTHY",
      journalSizeBytes: 1,
      journalPendingReports: 0,
      lastBackupAt: iso(now.addingTimeInterval(-60)),
      checkedAt: iso(now),
      now: now
    )
  }

  private static let agentCheck = TTradeSafetyCheck(
    code: "LIVE_AGENT_READY",
    passed: true,
    message: "Agent 已就绪",
    scope: "PREPARATION"
  )

  private func iso(_ date: Date) -> String {
    ISO8601DateFormatter().string(from: date)
  }
}
