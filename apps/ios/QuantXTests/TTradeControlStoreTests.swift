import Foundation
import XCTest

@testable import QuantX

@MainActor
final class TTradeControlStoreTests: XCTestCase {
  func testEveryHighRiskActionRequiresItsOwnBiometricConfirmation() async throws {
    let cases: [(TTradeSafetyAction, String)] = [
      (.beginControlledWindow, ""),
      (.activateCanary, ""),
      (.activateLive, ""),
      (.killSwitch, "Agent 离线，转人工处置"),
    ]

    for (action, reason) in cases {
      let harness = await makeHarness()
      try await harness.store.preview(action: action, reason: reason)
      let preview = try XCTUnwrap(harness.store.pendingControl)
      try await harness.store.confirm(preview)

      XCTAssertEqual(
        harness.authentication.reasons,
        ["确认\(action.title)：账户 •••• NT-1"]
      )
      XCTAssertEqual(harness.repository.confirmCalls.map(\.action), [action])
      XCTAssertNil(harness.store.pendingControl)
      XCTAssertNotNil(harness.store.successMessage)
    }
  }

  func testKillRemainsConfirmableAfterPolicySnapshotAgentAndReadinessChange() async throws {
    let initial = makeSnapshot()
    let changed = makeSnapshot(
      policyVersion: 9,
      snapshotID: "snapshot-9",
      snapshotHash: String(repeating: "9", count: 64),
      stage: "PAUSED",
      status: "BLOCKED",
      agentStatus: "OFFLINE",
      protocolVersion: "",
      ready: false,
      checks: []
    )
    let repository = TTradeControlRepositorySpy(snapshots: [initial, changed, changed])
    let harness = await makeHarness(repository: repository)
    try await harness.store.preview(
      action: .killSwitch,
      reason: "Agent 离线，立即人工接管"
    )
    let preview = try XCTUnwrap(harness.store.pendingControl)

    await harness.store.refresh()
    XCTAssertEqual(harness.store.state.snapshot?.policyVersion, 9)
    XCTAssertEqual(harness.store.state.snapshot?.agentStatus, "OFFLINE")
    try await harness.store.confirm(preview)

    XCTAssertEqual(repository.confirmCalls.count, 1)
    XCTAssertEqual(harness.authentication.reasons.count, 1)
  }

  func testOrdinaryControlRejectsSnapshotBindingChangeBeforeBiometrics() async throws {
    let changed = makeSnapshot(
      policyVersion: 8,
      snapshotID: "snapshot-8",
      snapshotHash: String(repeating: "8", count: 64)
    )
    let repository = TTradeControlRepositorySpy(
      snapshots: [makeSnapshot(), changed]
    )
    let harness = await makeHarness(repository: repository)
    try await harness.store.preview(action: .beginControlledWindow)
    let preview = try XCTUnwrap(harness.store.pendingControl)
    await harness.store.refresh()

    do {
      try await harness.store.confirm(preview)
      XCTFail("普通控制不得跨安全快照确认")
    } catch {
      XCTAssertEqual(error as? TTradeControlError, .contextChanged)
    }
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
    XCTAssertTrue(repository.confirmCalls.isEmpty)
  }

  func testAccountSessionScopeActionAndReasonChangesInvalidateTicket() async throws {
    let harness = await makeHarness()
    try await harness.store.preview(
      action: .killSwitch,
      reason: "原始精确原因"
    )
    let preview = try XCTUnwrap(harness.store.pendingControl)

    let changedReason = copy(preview, reason: "被修改的原因")
    await assertContextRejected(changedReason, harness: harness)
    let changedAction = copy(preview, action: .activateLive)
    await assertContextRejected(changedAction, harness: harness)

    harness.store.activate(
      identity: identity(
        deviceSessionID: "session-2",
        accountID: "ACCOUNT-2",
        scopes: ["strategy:read", "t-trade:control"]
      ),
      repository: harness.repository
    )
    XCTAssertNil(harness.store.pendingControl)
    await assertContextRejected(preview, harness: harness)
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
  }

  func testExpiredChallengeIsRejectedBeforeBiometrics() async throws {
    let repository = TTradeControlRepositorySpy(snapshots: [makeSnapshot()])
    repository.previewExpiresAt = Date().addingTimeInterval(-1)
    let harness = await makeHarness(repository: repository)
    try await harness.store.preview(action: .activateLive)
    let preview = try XCTUnwrap(harness.store.pendingControl)

    do {
      try await harness.store.confirm(preview)
      XCTFail("过期凭据不得确认")
    } catch {
      XCTAssertEqual(error as? TTradeControlError, .challengeExpired)
    }
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
  }

  func testDuplicatePreviewTapIsDebouncedBeforeSecondNetworkCall() async throws {
    let repository = TTradeControlRepositorySpy(snapshots: [makeSnapshot()])
    repository.previewDelayNanoseconds = 150_000_000
    let harness = await makeHarness(repository: repository)

    let first = Task { try await harness.store.preview(action: .activateCanary) }
    while repository.previewCalls.isEmpty { await Task.yield() }
    do {
      try await harness.store.preview(action: .activateCanary)
      XCTFail("重复点击不得发起第二次预览")
    } catch {
      XCTAssertEqual(error as? TTradeControlError, .alreadyInProgress)
    }
    try await first.value
    XCTAssertEqual(repository.previewCalls.count, 1)
  }

  func testPreview401RefreshesAtMostOnceAndUsesNewRepository() async throws {
    let firstRepository = TTradeControlRepositorySpy(snapshots: [makeSnapshot()])
    firstRepository.previewErrors = [ReadOnlyRepositoryError.unauthenticated]
    let secondRepository = TTradeControlRepositorySpy(snapshots: [makeSnapshot()])
    let harness = await makeHarness(repository: firstRepository)
    harness.runtime.refreshHandler = {
      harness.store.activate(
        identity: self.identity(),
        repository: secondRepository
      )
    }

    try await harness.store.preview(action: .activateCanary)

    XCTAssertEqual(harness.runtime.refreshCount, 1)
    XCTAssertEqual(firstRepository.previewCalls.count, 1)
    XCTAssertEqual(secondRepository.previewCalls.count, 1)
    XCTAssertNotNil(harness.store.pendingControl)
  }

  func testConfirmTransportFailureIsUncertainAndRefreshesTruthWithoutFakeSuccess() async throws {
    let repository = TTradeControlRepositorySpy(
      snapshots: [makeSnapshot(), makeSnapshot()]
    )
    repository.confirmError = ReadOnlyRepositoryError.transport
    let harness = await makeHarness(repository: repository)
    try await harness.store.preview(action: .activateLive)
    let preview = try XCTUnwrap(harness.store.pendingControl)

    do {
      try await harness.store.confirm(preview)
      XCTFail("传输中断不得显示成功")
    } catch {
      XCTAssertEqual(error as? TTradeControlError, .resultUncertain)
    }

    XCTAssertNil(harness.store.successMessage)
    XCTAssertNotNil(harness.store.errorMessage)
    XCTAssertNil(harness.store.pendingControl)
    XCTAssertEqual(harness.runtime.projectionRefreshCount, 1)
    XCTAssertGreaterThanOrEqual(repository.loadCount, 2)
  }

  func testPauseNeedsOnlyNarrowScopeAndNeverRequestsBiometrics() async throws {
    let harness = await makeHarness(
      scopes: ["strategy:read", "t-trade:control"]
    )

    XCTAssertNil(harness.store.pauseUnavailableReason)
    XCTAssertNotNil(harness.store.controlUnavailableReason(for: .activateLive))
    try await harness.store.pauseEntries(reason: "停止新入场，保留退出保护")

    XCTAssertEqual(harness.repository.pauseCalls.count, 1)
    XCTAssertEqual(
      harness.repository.pauseCalls.first?.reason,
      "停止新入场，保留退出保护"
    )
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
  }

  func testReleaseRequiresBiometricsAndSeparatesQueuedFromSuccess() async throws {
    let release = TAssistantReleaseSpy()
    let harness = await makeHarness(releaseRepository: release)
    try await harness.store.previewRelease(release.draft)
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
    try await harness.store.confirmRelease()
    XCTAssertEqual(harness.authentication.reasons.count, 1)
    XCTAssertEqual(harness.store.releaseCommandID, "command-1")
    XCTAssertNil(harness.store.releaseStatus)
    try await harness.store.refreshReleaseStatus()
    XCTAssertEqual(harness.store.releaseStatus?.phase, .pending)
    XCTAssertNil(harness.store.successMessage)
  }

  func testReleaseLostConfirmationCanQueryOriginalChallenge() async throws {
    let release = TAssistantReleaseSpy()
    release.loseConfirmation = true
    let harness = await makeHarness(releaseRepository: release)
    try await harness.store.previewRelease(release.draft)
    do { try await harness.store.confirmRelease(); XCTFail("expected transport failure") }
    catch {}
    XCTAssertNotNil(harness.store.releaseTicket)
    do { try await harness.store.previewRelease(release.draft); XCTFail("must retain original") }
    catch {}
    try await harness.store.refreshReleaseStatus()
    XCTAssertEqual(harness.store.releaseCommandID, "command-1")
    XCTAssertEqual(release.confirmCount, 1)
  }

  func testReleaseCannotConfirmAfterLockOrDeviceChange() async throws {
    let release = TAssistantReleaseSpy()
    let harness = await makeHarness(releaseRepository: release)
    try await harness.store.previewRelease(release.draft)
    harness.runtime.localSessionLocked = true
    do { try await harness.store.confirmRelease(); XCTFail("locked") } catch {}
    XCTAssertEqual(release.confirmCount, 0)
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
    harness.store.activate(identity: identity(deviceSessionID: "other"),
      repository: harness.repository, releaseRepository: release)
    XCTAssertNil(harness.store.releaseTicket)
  }

  func testReleaseStatusSurvivesLocalLockWithoutConfirmationCredential() async throws {
    let release = TAssistantReleaseSpy()
    let harness = await makeHarness(releaseRepository: release)
    try await harness.store.previewRelease(release.draft)
    try await harness.store.confirmRelease()
    let reference = harness.store.releaseReference
    harness.runtime.localSessionLocked = true
    harness.store.invalidateChallengeContext()
    XCTAssertNil(harness.store.releaseTicket)
    XCTAssertEqual(harness.store.releaseReference, reference)
    do { try await harness.store.refreshReleaseStatus(); XCTFail("locked") } catch {}
    harness.runtime.localSessionLocked = false
    try await harness.store.refreshReleaseStatus()
    XCTAssertEqual(harness.store.releaseStatus?.phase, .pending)
    do { try await harness.store.confirmRelease(); XCTFail("credential cleared") } catch {}
    XCTAssertEqual(release.confirmCount, 1)
    harness.store.clearSession()
    XCTAssertNil(harness.store.releaseReference)
  }

  func testNewStoreDiscoversAndRecoversWithoutCredentialOrBiometrics() async throws {
    let release = TAssistantReleaseSpy()
    let harness = await makeHarness(releaseRepository: release)
    XCTAssertNil(harness.store.releaseReference)
    try await harness.store.loadReleaseOperations()
    let operation = try XCTUnwrap(harness.store.recentReleaseOperations.first)
    try await harness.store.recoverReleaseOperation(operation)
    XCTAssertNil(harness.store.releaseTicket)
    XCTAssertEqual(harness.store.releaseStatus?.phase, .pending)
    XCTAssertEqual(release.confirmCount, 0)
    XCTAssertTrue(harness.authentication.reasons.isEmpty)
  }

  func testReleaseDocumentRequiresExactVersionKeysAndTypedBudget() throws {
    let request: [String: Any] = [
      "accountId": "ACCOUNT-1", "sourceExecutionId": "paper", "configVersionId": "target",
      "expectedConfigHash": String(repeating: "a", count: 64), "expectedHeadVersion": 1,
      "evaluationId": "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa",
      "expectedReportHash": String(repeating: "b", count: 64),
      "expectedPolicyHash": String(repeating: "c", count: 64),
      "windowStart": "2026-09-10T09:00:00+08:00", "windowEnd": "2026-09-10T10:00:00+08:00",
    ]
    func data(_ value: [String: Any], schema: String = "quantx.t-assistant-release-request.v1") throws -> Data {
      try JSONSerialization.data(withJSONObject: ["schema": schema, "request": value])
    }
    let draft = try TAssistantReleaseDraft.decodeReleaseDocument(data(request))
    XCTAssertEqual(draft.headVersion, 1)
    XCTAssertEqual(draft.windowEnd.timeIntervalSince(draft.windowStart), 3600)
    XCTAssertThrowsError(try TAssistantReleaseDraft.decodeReleaseDocument(data(request, schema: "unknown")))
    var changed = request
    changed["actorId"] = "forged"
    XCTAssertThrowsError(try TAssistantReleaseDraft.decodeReleaseDocument(data(changed)))
    changed = request
    changed["expectedHeadVersion"] = true
    XCTAssertThrowsError(try TAssistantReleaseDraft.decodeReleaseDocument(data(changed)))
    XCTAssertThrowsError(try TAssistantReleaseDraft.decodeReleaseDocument(Data(repeating: 32, count: 16_385)))
  }

  func testReleaseStatusRejectsUnprovenSuccessAndUnknownPhase() {
    for phase in ["SUCCEEDED", "NEW_PHASE"] {
      XCTAssertThrowsError(try TAssistantReleaseStatus.validated(challengeID: "challenge",
        phase: phase, commandID: "command", executionID: nil, executionStatus: nil,
        reasonCode: nil, expectedChallengeID: "challenge"))
    }
  }

  func testAccountControlUsesOwnVersionAndBiometricConfirmation() async throws {
    let account = AccountControlSpy()
    let harness = await makeHarness(accountRepository: account,
      scopes: ["strategy:read", "account-execution:control", "trade:approve"])
    for action in [NativeAccountControlAction.beginControlledWindow, .killSwitch] {
      try await harness.store.previewAccountControl(action, reason: "人工处置")
      let ticket = try XCTUnwrap(harness.store.accountControlTicket)
      XCTAssertEqual(ticket.stateVersion, 100)
      try await harness.store.confirmAccountControl(ticket)
      XCTAssertNil(harness.store.accountControlTicket)
    }
    XCTAssertEqual(account.confirmCount, 2)
    XCTAssertEqual(harness.authentication.reasons.count, 2)
    XCTAssertTrue(harness.repository.confirmCalls.isEmpty)
  }

  func testAccountControlRequiresAccountPermissionAndRechecksAfterBiometrics() async throws {
    let denied = await makeHarness(accountRepository: AccountControlSpy())
    do { try await denied.store.previewAccountControl(.killSwitch, reason: "停止"); XCTFail("scope") } catch {}
    XCTAssertTrue(denied.authentication.reasons.isEmpty)
    let account = AccountControlSpy()
    let harness = await makeHarness(accountRepository: account,
      scopes: ["strategy:read", "account-execution:control", "trade:approve"])
    try await harness.store.previewAccountControl(.killSwitch, reason: "停止")
    let ticket = try XCTUnwrap(harness.store.accountControlTicket)
    harness.authentication.authorizationHandler = { harness.runtime.localSessionLocked = true }
    do { try await harness.store.confirmAccountControl(ticket); XCTFail("locked during biometrics") } catch {}
    XCTAssertEqual(account.confirmCount, 0)
  }

  func testLegacyPreparationAndExpiredUncertainConfirmationReuseOriginalIdentity() async throws {
    let legacy = LegacyMaintenanceSpy()
    var now = legacy.now
    let harness = await makeHarness(legacyRepository: legacy, legacyNow: { now })
    legacy.failPrepare = true
    do { try await harness.store.prepareLegacyInventory(legacy.scope); XCTFail("expected timeout") } catch {}
    let preparationID = try XCTUnwrap(harness.store.legacyPreparationID)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    XCTAssertEqual(legacy.preparations, [preparationID, preparationID])
    try await harness.store.refreshLegacyStatus()
    try await harness.store.previewLegacyDrain(windowStart: now.addingTimeInterval(-1), windowEnd: now.addingTimeInterval(120))
    let ticket = try XCTUnwrap(harness.store.legacyTicket)
    legacy.failConfirm = true
    do { try await harness.store.confirmLegacyDrain(); XCTFail("expected timeout") } catch {}
    XCTAssertTrue(harness.store.legacyConfirmationAttempted)
    XCTAssertEqual(harness.store.legacyTicket, ticket)
    XCTAssertThrowsError(try harness.store.discardLegacyReview())
    do { try await harness.store.refreshLegacyStatus(); XCTFail("must not substitute preparation status") } catch {}
    now = now.addingTimeInterval(70)
    try await harness.store.confirmLegacyDrain()
    XCTAssertEqual(legacy.confirmations, [ticket, ticket])
    XCTAssertEqual(harness.authentication.reasons.count, 2)
    XCTAssertNil(harness.store.successMessage)
    XCTAssertNil(harness.store.legacyTicket)
    try await harness.store.refreshLegacyStatus()
    XCTAssertEqual(harness.store.legacyStatus?.status, .succeeded)
    XCTAssertNotNil(harness.store.successMessage)
  }

  func testLegacyUnconfirmedReviewCanBeReplacedWithoutSendingDrain() async throws {
    let legacy = LegacyMaintenanceSpy()
    let harness = await makeHarness(legacyRepository: legacy)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    let original = harness.store.legacyPreparationID
    try harness.store.discardLegacyReview()
    XCTAssertNil(harness.store.legacyScope)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    XCTAssertNotEqual(harness.store.legacyPreparationID, original)
    XCTAssertTrue(legacy.confirmations.isEmpty)
  }

  func testLegacyLocalLockDuringBiometricsPreventsSubmission() async throws {
    let legacy = LegacyMaintenanceSpy()
    let harness = await makeHarness(legacyRepository: legacy)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    try await harness.store.refreshLegacyStatus()
    try await harness.store.previewLegacyDrain(windowStart: legacy.now.addingTimeInterval(-1), windowEnd: legacy.now.addingTimeInterval(120))
    harness.authentication.authorizationHandler = { harness.store.invalidateChallengeContext() }
    do { try await harness.store.confirmLegacyDrain(); XCTFail("expected context rejection") } catch {}
    XCTAssertTrue(legacy.confirmations.isEmpty)
    XCTAssertNil(harness.store.legacyTicket)
    XCTAssertFalse(harness.store.legacyConfirmationAttempted)
  }

  func testLegacySessionChangeWhileResponseArrivesCannotRepopulateState() async throws {
    let legacy = LegacyMaintenanceSpy()
    let harness = await makeHarness(legacyRepository: legacy)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    try await harness.store.refreshLegacyStatus()
    try await harness.store.previewLegacyDrain(windowStart: legacy.now.addingTimeInterval(-1), windowEnd: legacy.now.addingTimeInterval(120))
    legacy.onConfirm = { harness.store.clearSession() }
    do { try await harness.store.confirmLegacyDrain(); XCTFail("expected context rejection") } catch {}
    XCTAssertNil(harness.store.legacyCommandID)
    XCTAssertNil(harness.store.legacyTicket)
    XCTAssertNil(harness.store.legacyScope)
  }

  func testLegacyUnknownConfirmationCanRecoverAfterLocalLockWithoutToken() async throws {
    let legacy = LegacyMaintenanceSpy()
    let harness = await makeHarness(legacyRepository: legacy)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    try await harness.store.refreshLegacyStatus()
    try await harness.store.previewLegacyDrain(windowStart: legacy.now.addingTimeInterval(-1), windowEnd: legacy.now.addingTimeInterval(120))
    let identity = harness.store.legacyChallengeID
    legacy.failConfirm = true
    do { try await harness.store.confirmLegacyDrain(); XCTFail("expected timeout") } catch {}
    harness.store.invalidateChallengeContext()
    XCTAssertNil(harness.store.legacyTicket)
    XCTAssertEqual(harness.store.legacyChallengeID, identity)
    try await harness.store.recoverLegacyConfirmation()
    XCTAssertEqual(harness.store.legacyCommandID, legacy.commandID)
    XCTAssertNil(harness.store.successMessage)
    try await harness.store.refreshLegacyStatus()
    XCTAssertEqual(harness.store.legacyStatus?.status, .succeeded)
    XCTAssertEqual(legacy.confirmations.count, 1)
  }

  func testLegacyUnknownConfirmationRequiresAuthoritativeUnconsumedExpiryToRestart() async throws {
    let legacy = LegacyMaintenanceSpy()
    let harness = await makeHarness(legacyRepository: legacy)
    try await harness.store.prepareLegacyInventory(legacy.scope)
    try await harness.store.refreshLegacyStatus()
    try await harness.store.previewLegacyDrain(windowStart: legacy.now.addingTimeInterval(-1), windowEnd: legacy.now.addingTimeInterval(120))
    legacy.failConfirm = true
    do { try await harness.store.confirmLegacyDrain(); XCTFail("expected timeout") } catch {}
    harness.store.invalidateChallengeContext()
    legacy.recoveryPhase = .awaitingConfirmation
    try await harness.store.recoverLegacyConfirmation()
    XCTAssertThrowsError(try harness.store.discardLegacyReview())
    legacy.recoveryPhase = .expired
    try await harness.store.recoverLegacyConfirmation()
    XCTAssertFalse(harness.store.legacyConfirmationAttempted)
    XCTAssertNil(harness.store.legacyTicket)
    XCTAssertNil(harness.store.legacyChallengeID)
    XCTAssertNoThrow(try harness.store.discardLegacyReview())
  }

  private func makeHarness(
    repository: TTradeControlRepositorySpy? = nil,
    releaseRepository: TAssistantReleaseSpy? = nil,
    legacyRepository: LegacyMaintenanceSpy? = nil,
    legacyNow: @escaping @MainActor () -> Date = { Date() },
    accountRepository: AccountControlSpy? = nil,
    authentication: TTradeControlAuthenticationSpy = TTradeControlAuthenticationSpy(),
    scopes: Set<String> = ["strategy:read", "t-trade:control", "trade:approve"]
  ) async -> (
    store: TTradeControlStore,
    repository: TTradeControlRepositorySpy,
    runtime: TTradeControlRuntimeSpy,
    authentication: TTradeControlAuthenticationSpy
  ) {
    let repository = repository ?? TTradeControlRepositorySpy(snapshots: [makeSnapshot()])
    let runtime = TTradeControlRuntimeSpy()
    let store = TTradeControlStore(localAuthentication: authentication, legacyNow: legacyNow)
    store.configure(
      contextProvider: { runtime.context },
      refreshSession: { try await runtime.refresh() },
      refreshAssistantProjection: { runtime.projectionRefreshCount += 1 }
    )
    store.activate(identity: identity(scopes: scopes), repository: repository,
      releaseRepository: releaseRepository, legacyRepository: legacyRepository, accountRepository: accountRepository)
    await store.refresh()
    return (store, repository, runtime, authentication)
  }

  private func identity(
    deviceSessionID: String = "session-1",
    accountID: String = "ACCOUNT-1",
    scopes: Set<String> = ["strategy:read", "t-trade:control", "trade:approve"]
  ) -> TTradeControlStore.SessionIdentity {
    TTradeControlStore.SessionIdentity(
      userID: "user-1",
      deviceSessionID: deviceSessionID,
      activeAccountID: accountID,
      authorizedAccountIDs: [accountID],
      grantedScopes: scopes
    )
  }

  private func assertContextRejected(
    _ preview: TTradeControlPreviewTicket,
    harness: (
      store: TTradeControlStore,
      repository: TTradeControlRepositorySpy,
      runtime: TTradeControlRuntimeSpy,
      authentication: TTradeControlAuthenticationSpy
    )
  ) async {
    do {
      try await harness.store.confirm(preview)
      XCTFail("上下文变化不得确认")
    } catch {
      XCTAssertEqual(error as? TTradeControlError, .contextChanged)
    }
    XCTAssertTrue(harness.repository.confirmCalls.isEmpty)
  }

  private func copy(
    _ value: TTradeControlPreviewTicket,
    action: TTradeSafetyAction? = nil,
    reason: String? = nil
  ) -> TTradeControlPreviewTicket {
    TTradeControlPreviewTicket(
      id: value.id,
      confirmationToken: value.confirmationToken,
      sessionContextID: value.sessionContextID,
      userID: value.userID,
      deviceSessionID: value.deviceSessionID,
      accountID: value.accountID,
      action: action ?? value.action,
      policyVersion: value.policyVersion,
      snapshotID: value.snapshotID,
      targetStage: action?.targetStage ?? value.targetStage,
      reason: reason ?? value.reason,
      currentStage: value.currentStage,
      readinessStatus: value.readinessStatus,
      readinessFingerprint: value.readinessFingerprint,
      expiresAt: value.expiresAt,
      checks: value.checks,
      warnings: value.warnings,
      snapshotBinding: value.snapshotBinding
    )
  }

  private func makeSnapshot(
    policyVersion: Int = 7,
    snapshotID: String = "snapshot-1",
    snapshotHash: String = String(repeating: "a", count: 64),
    stage: String = "SHADOW",
    status: String = "READY",
    agentStatus: String = "READY",
    protocolVersion: String = "1.2",
    ready: Bool = true,
    checks: [TTradeSafetyCheck] = [
      TTradeSafetyCheck(
        code: "LIVE_AGENT_READY",
        passed: true,
        message: "Agent 已就绪",
        scope: "PREPARATION"
      )
    ]
  ) -> TTradeControlSnapshot {
    let now = Date()
    return TTradeControlSnapshot(
      accountID: "ACCOUNT-1",
      monitorEnabled: false,
      monitorMode: "LIVE",
      stage: stage,
      ready: ready,
      status: status,
      preparationReady: ready,
      automationReady: ready,
      engineStatus: ready ? "READY" : "BLOCKED",
      agentStatus: agentStatus,
      agentDeviceID: "device-1",
      agentMode: agentStatus == "READY" ? "LIVE" : "",
      protocolVersion: protocolVersion,
      reconcileStatus: ready ? "READY" : "BLOCKED",
      killSwitch: false,
      policyVersion: policyVersion,
      canApprove: ready,
      canActivateLive: ready,
      blockedReasons: ready ? [] : ["当前未就绪"],
      preparationBlockedReasons: ready ? [] : ["当前未就绪"],
      checks: checks,
      snapshotID: snapshotID,
      snapshotHash: snapshotHash,
      snapshotAt: now,
      reconciliationAgeSeconds: 1,
      controlledWindowActive: false,
      controlledWindowSnapshotID: nil,
      controlledWindowStartedAt: nil,
      positionSnapshotSource: "QMT_AGENT",
      positionSnapshotReportedAt: now,
      positionSnapshotReceivedAt: now,
      positionSnapshotComplete: true,
      positionSnapshotError: nil,
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
      lastBackupAt: now,
      pendingSignalCount: 0,
      activeBatchCount: 0,
      drainingCount: 0,
      projectionGeneratedAt: now,
      checkedAt: now,
      fetchedAt: now
    )
  }
}

@MainActor
private final class TTradeControlRepositorySpy: TTradeControlLoading {
  struct PauseCall: Equatable {
    let accountID: String
    let reason: String
  }

  private var snapshots: [TTradeControlSnapshot]
  private(set) var loadCount = 0
  private(set) var previewCalls: [TTradeControlPreviewRequest] = []
  private(set) var confirmCalls: [TTradeControlPreviewTicket] = []
  private(set) var pauseCalls: [PauseCall] = []
  var previewErrors: [Error] = []
  var confirmError: Error?
  var previewExpiresAt = Date().addingTimeInterval(60)
  var previewDelayNanoseconds: UInt64 = 0

  init(snapshots: [TTradeControlSnapshot]) {
    self.snapshots = snapshots
  }

  func loadControlState(
    accountID: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlSnapshot {
    let index = min(loadCount, snapshots.count - 1)
    loadCount += 1
    return snapshots[index]
  }

  func previewControl(
    _ request: TTradeControlPreviewRequest,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlPreviewTicket {
    previewCalls.append(request)
    if previewDelayNanoseconds > 0 {
      try await Task.sleep(nanoseconds: previewDelayNanoseconds)
    }
    if !previewErrors.isEmpty { throw previewErrors.removeFirst() }
    return TTradeControlPreviewTicket(
      id: UUID().uuidString.lowercased(),
      confirmationToken: "memory-only-token",
      sessionContextID: context.sessionContextID,
      userID: context.userID,
      deviceSessionID: context.deviceSessionID,
      accountID: request.accountID,
      action: request.action,
      policyVersion: request.policyVersion,
      snapshotID: request.snapshotID,
      targetStage: request.targetStage,
      reason: request.reason,
      currentStage: request.expectedStage,
      readinessStatus: request.expectedReadinessStatus,
      readinessFingerprint: String(repeating: "f", count: 64),
      expiresAt: previewExpiresAt,
      checks: request.expectedChecks,
      warnings: ["确认不代表委托或成交"],
      snapshotBinding: request.snapshotBinding
    )
  }

  func confirmControl(
    _ preview: TTradeControlPreviewTicket,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlConfirmation {
    confirmCalls.append(preview)
    if let confirmError { throw confirmError }
    return TTradeControlConfirmation(
      challengeID: preview.id,
      accountID: preview.accountID,
      action: preview.action,
      code: "APPLIED",
      operationStatus: "APPLIED",
      message: "服务端已应用账户级控制"
    )
  }

  func pauseEntries(
    accountID: String,
    reason: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradePauseConfirmation {
    pauseCalls.append(PauseCall(accountID: accountID, reason: reason))
    return TTradePauseConfirmation(
      accountID: accountID,
      code: "ENTRIES_PAUSED",
      message: "已停止新买入，现有批次继续受保护"
    )
  }
}

@MainActor
private final class TTradeControlAuthenticationSpy: LocalAuthenticationProviding {
  var tradeAuthorizationAvailable = true
  private(set) var reasons: [String] = []
  var authorizationHandler: (() -> Void)?

  func unlock(reason: String) async throws {}

  func authorizeTrade(reason: String) async throws {
    reasons.append(reason)
    authorizationHandler?()
  }
}

@MainActor
private final class TTradeControlRuntimeSpy {
  var accountID: String? = "ACCOUNT-1"
  var localSessionLocked = false
  var accountDataEnabled = true
  var refreshCount = 0
  var projectionRefreshCount = 0
  var refreshHandler: (@MainActor () async throws -> Void)?

  var context: TTradeControlRuntimeContext {
    TTradeControlRuntimeContext(
      accountID: accountID,
      localSessionLocked: localSessionLocked,
      accountDataEnabled: accountDataEnabled
    )
  }

  func refresh() async throws {
    refreshCount += 1
    try await refreshHandler?()
  }
}


@MainActor
private final class TAssistantReleaseSpy: TAssistantReleaseLoading {
  var confirmCount = 0
  var loseConfirmation = false
  let draft = TAssistantReleaseDraft(accountID: "ACCOUNT-1", sourceExecutionID: "paper",
    configVersionID: "target", configHash: String(repeating: "a", count: 64), headVersion: 1,
    evaluationID: UUID().uuidString.lowercased(), reportHash: String(repeating: "b", count: 64),
    policyHash: String(repeating: "c", count: 64), windowStart: Date(),
    windowEnd: Date().addingTimeInterval(600))

  func recentOperations(context: TTradeControlRepositoryContext) async throws -> [TAssistantReleaseOperation] {
    [TAssistantReleaseOperation(reference: TAssistantReleaseReference(
      challengeID: "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", context: context),
      configVersionID: "target", createdAt: Date())]
  }

  func preview(_ draft: TAssistantReleaseDraft, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseTicket {
    try draft.validate(context: context)
    return TAssistantReleaseTicket(challengeID: UUID().uuidString.lowercased(),
      confirmationToken: "secret", expiresAt: Date().addingTimeInterval(60), draft: draft, context: context)
  }
  func confirm(_ ticket: TAssistantReleaseTicket, context: TTradeControlRepositoryContext)
    async throws -> String {
    confirmCount += 1
    if loseConfirmation { throw URLError(.networkConnectionLost) }
    return "command-1"
  }
  func status(_ reference: TAssistantReleaseReference, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseStatus {
    try TAssistantReleaseStatus.validated(challengeID: reference.challengeID, phase: "PENDING",
      commandID: "command-1", executionID: nil, executionStatus: nil, reasonCode: nil,
      expectedChallengeID: reference.challengeID)
  }
}


@MainActor
private final class AccountControlSpy: AccountExecutionControlLoading {
  var confirmCount = 0
  func preview(action: NativeAccountControlAction, reason: String, context: TTradeControlRepositoryContext)
    async throws -> NativeAccountControlTicket {
    NativeAccountControlTicket(id: UUID().uuidString, token: "secret", context: context,
      action: action, stateVersion: 100, snapshotID: action == .killSwitch ? "" : "account-snapshot",
      reason: reason, expiresAt: Date().addingTimeInterval(60), summary: "账户控制", blockedReasons: [])
  }
  func confirm(_ ticket: NativeAccountControlTicket, context: TTradeControlRepositoryContext)
    async throws -> String {
    confirmCount += 1
    return "账户操作已应用"
  }
}

@MainActor
private final class LegacyMaintenanceSpy: TAssistantLegacyMaintenanceLoading {
  let now = Date()
  let scope = TAssistantLegacyScope(accountID: "ACCOUNT-1", configID: "head", runID: "legacy", headVersion: 1)
  let commandID = UUID().uuidString.lowercased()
  var failPrepare = false
  var failConfirm = false
  var preparations: [UUID] = []
  var confirmations: [TAssistantLegacyDrainTicket] = []
  var onConfirm: (() -> Void)?
  var recoveryPhase: TAssistantLegacyConfirmationRecovery.Phase = .pending

  func recover(challengeID: String, inventory: TAssistantLegacyInventory, context: TTradeControlRepositoryContext) async throws -> TAssistantLegacyConfirmationRecovery {
    .init(phase: recoveryPhase, commandID: [.awaitingConfirmation, .expired].contains(recoveryPhase) ? nil : commandID)
  }

  func prepare(_ scope: TAssistantLegacyScope, requestID: UUID, context: TTradeControlRepositoryContext) async throws -> String {
    preparations.append(requestID)
    if failPrepare { failPrepare = false; throw URLError(.timedOut) }
    return requestID.uuidString.lowercased()
  }
  func operation(_ identity: String, context: TTradeControlRepositoryContext) async throws -> TAssistantLegacyOperation {
    var manifest: [String: GraphQLJSON] = ["schema": .string("legacy-t-obligations.v1"),
      "account_id": .string(scope.accountID), "config_id": .string(scope.configID),
      "run_id": .string(scope.runID), "head_version": .integer(scope.headVersion)]
    for key in ["intents", "pending", "correlations", "commands", "runtime_events", "batches", "exit_plans", "retained_client_order_ids", "unsubmitted_intent_ids_for_review"] {
      manifest[key] = .array([])
    }
    var evidence: GraphQLJSON = .init(object: ["manifest": .init(object: manifest),
      "manifest_hash": .string(String(repeating: "a", count: 64)), "inventory_operation_id": .string("legacy-inventory:\(identity)")])
    if identity == commandID {
      let inventory = confirmations[0].inventory
      var request = scope.fields
      request.removeValue(forKey: "account_id")
      request["inventory_operation_id"] = .string(inventory.operationID)
      request["inventory_hash"] = .string(inventory.hash)
      evidence = .init(object: ["run_id": .string(scope.runID), "request": .init(object: request)])
    }
    return .init(commandID: identity, status: .succeeded, evidence: evidence)
  }
  func preview(_ inventory: TAssistantLegacyInventory, windowStart: Date, windowEnd: Date, context: TTradeControlRepositoryContext) async throws -> TAssistantLegacyDrainTicket {
    .init(challengeID: UUID().uuidString.lowercased(), token: "original-secret", expiresAt: now.addingTimeInterval(60), inventory: inventory, windowStart: windowStart, windowEnd: windowEnd, context: context)
  }
  func confirm(_ ticket: TAssistantLegacyDrainTicket, context: TTradeControlRepositoryContext) async throws -> String {
    confirmations.append(ticket)
    onConfirm?()
    if failConfirm { failConfirm = false; throw URLError(.timedOut) }
    return commandID
  }
}
