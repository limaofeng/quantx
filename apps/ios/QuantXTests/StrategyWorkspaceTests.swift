import XCTest

@testable import QuantX

@MainActor
final class StrategyWorkspaceTests: XCTestCase {
  func testParameterSaveCarriesExpectedVersionAndOnlyChangedAllowlistValues() async throws {
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [
        makeSnapshot(version: "7", value: 3),
        makeSnapshot(version: "8", value: 4),
      ]
    )
    let harness = makeHarness(repository: repository)
    let instance = makeInstance(mode: "PAPER", status: "PAUSED")
    await harness.store.select(instance)
    let parameter = try XCTUnwrap(harness.store.parameterState.snapshot?.parameters.first)

    harness.store.setDraftValue(.integer(4), for: parameter)
    try await harness.store.saveParameters(for: instance)

    XCTAssertEqual(repository.updateCalls.count, 1)
    XCTAssertEqual(repository.updateCalls[0].instanceID, instance.id)
    XCTAssertEqual(repository.updateCalls[0].expectedVersion, "7")
    XCTAssertEqual(repository.updateCalls[0].values, ["threshold": .integer(4)])
    XCTAssertFalse(repository.updateCalls[0].applyImmediately)
    XCTAssertEqual(harness.store.parameterState.snapshot?.configVersion, "8")
    XCTAssertEqual(harness.store.draftValues, ["threshold": .integer(4)])
    XCTAssertNotNil(harness.store.successMessage)
  }

  func testVersionConflictPreservesDraftUntilExplicitServerAdoption() async {
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [
        makeSnapshot(version: "7", value: 3),
        makeSnapshot(version: "9", value: 6),
      ]
    )
    repository.updateError = StrategyWorkspaceError.versionConflict
    let harness = makeHarness(repository: repository)
    let instance = makeInstance(mode: "PAPER", status: "PAUSED")
    await harness.store.select(instance)
    let parameter = try! XCTUnwrap(harness.store.parameterState.snapshot?.parameters.first)
    harness.store.setDraftValue(.integer(4), for: parameter)

    do {
      try await harness.store.saveParameters(for: instance)
      XCTFail("版本冲突不得伪装成功")
    } catch {
      XCTAssertEqual(error as? StrategyWorkspaceError, .versionConflict)
    }

    XCTAssertEqual(harness.store.parameterState.snapshot?.configVersion, "9")
    XCTAssertEqual(harness.store.draftValues, ["threshold": .integer(4)])
    XCTAssertEqual(harness.store.parameterConflict?.staleVersion, "7")
    XCTAssertEqual(harness.store.parameterConflict?.serverVersion, "9")
    XCTAssertEqual(harness.store.parameterConflict?.differences.count, 1)
    XCTAssertEqual(
      harness.store.parameterConflict?.differences.first?.userValue,
      .integer(4)
    )
    XCTAssertEqual(
      harness.store.parameterConflict?.differences.first?.serverValue,
      .integer(6)
    )
    XCTAssertNil(harness.store.successMessage)
    XCTAssertEqual(harness.runtime.refreshCount, 1)

    harness.store.adoptServerValuesAfterConflict()

    XCTAssertEqual(harness.store.draftValues, ["threshold": .integer(6)])
    XCTAssertNil(harness.store.parameterConflict)
    XCTAssertEqual(repository.updateCalls.count, 1)
  }

  func testConflictRebaseResubmitsDraftWithFreshExpectedVersionOnlyAfterUserChoice() async throws {
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [
        makeSnapshot(version: "7", value: 3),
        makeSnapshot(version: "9", value: 6),
        makeSnapshot(version: "10", value: 4),
      ]
    )
    repository.updateError = StrategyWorkspaceError.versionConflict
    let harness = makeHarness(repository: repository)
    let instance = makeInstance(mode: "PAPER", status: "PAUSED")
    await harness.store.select(instance)
    let parameter = try XCTUnwrap(harness.store.parameterState.snapshot?.parameters.first)
    harness.store.setDraftValue(.integer(4), for: parameter)

    do {
      try await harness.store.saveParameters(for: instance)
      XCTFail("首次旧版本提交必须冲突")
    } catch {
      XCTAssertEqual(error as? StrategyWorkspaceError, .versionConflict)
    }
    XCTAssertEqual(repository.updateCalls.count, 1)
    XCTAssertEqual(harness.store.draftValues, ["threshold": .integer(4)])

    repository.updateError = nil
    try await harness.store.resubmitParametersAfterConflict(for: instance)

    XCTAssertEqual(repository.updateCalls.count, 2)
    XCTAssertEqual(repository.updateCalls[1].expectedVersion, "9")
    XCTAssertEqual(repository.updateCalls[1].values, ["threshold": .integer(4)])
    XCTAssertEqual(harness.store.parameterState.snapshot?.configVersion, "10")
    XCTAssertEqual(harness.store.draftValues, ["threshold": .integer(4)])
    XCTAssertNil(harness.store.parameterConflict)
    XCTAssertNotNil(harness.store.successMessage)
  }

  func testPaperPauseResumeAndLivePauseNeverRequestBiometrics() async throws {
    let controls: [(StrategyMonitorItem, StrategyLifecycleControl)] = [
      (makeInstance(mode: "PAPER", status: "RUNNING"), .pause),
      (makeInstance(mode: "PAPER", status: "PAUSED"), .resumePaper),
      (makeInstance(mode: "LIVE", status: "RUNNING"), .pause),
    ]

    for (instance, control) in controls {
      let authentication = StrategyAuthenticationSpy()
      let repository = StrategyWorkspaceRepositorySpy(
        snapshots: [makeSnapshot(instanceID: instance.id)]
      )
      let harness = makeHarness(
        repository: repository,
        authentication: authentication
      )
      await harness.store.select(instance)

      try await harness.store.performDirectControl(control, instance: instance)

      XCTAssertTrue(authentication.reasons.isEmpty)
      if control == .resumePaper {
        XCTAssertEqual(repository.resumeCount, 1)
      } else {
        XCTAssertEqual(repository.pauseCount, 1)
      }
    }
  }

  func testEveryLiveStartResumeAndCloneConfirmationUsesExactBiometricReason() async throws {
    let cases: [(StrategyMonitorItem, StrategyLifecycleControl, StrategyLiveControlAction)] = [
      (makeInstance(mode: "LIVE", status: "STOPPED"), .startLive, .start),
      (makeInstance(mode: "LIVE", status: "PAUSED"), .resumeLive, .resume),
      (makeInstance(mode: "PAPER", status: "STOPPED"), .cloneToLive, .clone),
    ]

    for (instance, control, action) in cases {
      let authentication = StrategyAuthenticationSpy()
      let repository = StrategyWorkspaceRepositorySpy(
        snapshots: [makeSnapshot(instanceID: instance.id)]
      )
      let harness = makeHarness(
        repository: repository,
        authentication: authentication
      )
      await harness.store.select(instance)

      try await harness.store.previewLiveControl(control, instance: instance)
      let preview = try XCTUnwrap(harness.store.pendingControl)
      try await harness.store.confirmLiveControl(preview)

      XCTAssertEqual(preview.action, action)
      XCTAssertEqual(
        authentication.reasons,
        ["确认\(action.title)：\(instance.id)"]
      )
      XCTAssertEqual(repository.confirmCalls.count, 1)
      XCTAssertNil(harness.store.pendingControl)
      XCTAssertNotNil(harness.store.successMessage)
    }
  }

  func testLivePreviewRequiresBothIndependentScopesBeforeNetwork() async {
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [makeSnapshot()]
    )
    let harness = makeHarness(
      repository: repository,
      scopes: ["strategy:read", "strategy:control"]
    )
    let instance = makeInstance(mode: "LIVE", status: "PAUSED")
    await harness.store.select(instance)

    do {
      try await harness.store.previewLiveControl(.resumeLive, instance: instance)
      XCTFail("缺少 trade:approve 不得请求实盘预览")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("trade:approve"))
    }

    XCTAssertTrue(repository.previewCalls.isEmpty)
  }

  func testAccountOrDeviceSessionChangeClearsMemoryOnlyChallenge() async throws {
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [makeSnapshot()]
    )
    let harness = makeHarness(repository: repository)
    let instance = makeInstance(mode: "LIVE", status: "PAUSED")
    await harness.store.select(instance)
    try await harness.store.previewLiveControl(.resumeLive, instance: instance)
    let stale = try XCTUnwrap(harness.store.pendingControl)

    harness.store.activate(
      identity: StrategyWorkspace.SessionIdentity(
        userID: "user-1",
        deviceSessionID: "session-2",
        activeAccountID: "ACCOUNT-2",
        authorizedAccountIDs: ["ACCOUNT-2"],
        grantedScopes: ["strategy:read", "strategy:control", "trade:approve"]
      ),
      repository: repository
    )

    XCTAssertNil(harness.store.pendingControl)
    do {
      try await harness.store.confirmLiveControl(stale)
      XCTFail("切账户或会话后不得消费旧挑战")
    } catch {
      XCTAssertEqual(error as? StrategyWorkspaceError, .contextChanged)
    }
    XCTAssertTrue(repository.confirmCalls.isEmpty)
  }

  func testConfirmationTransportFailureNeverDisplaysSuccess() async throws {
    let authentication = StrategyAuthenticationSpy()
    let repository = StrategyWorkspaceRepositorySpy(
      snapshots: [makeSnapshot()]
    )
    repository.confirmError = ReadOnlyRepositoryError.transport
    let harness = makeHarness(
      repository: repository,
      authentication: authentication
    )
    let instance = makeInstance(mode: "LIVE", status: "PAUSED")
    await harness.store.select(instance)
    try await harness.store.previewLiveControl(.resumeLive, instance: instance)
    let preview = try XCTUnwrap(harness.store.pendingControl)

    do {
      try await harness.store.confirmLiveControl(preview)
      XCTFail("传输结果不确定不得显示成功")
    } catch {
      XCTAssertEqual(error as? StrategyWorkspaceError, .resultUncertain)
    }

    XCTAssertEqual(authentication.reasons.count, 1)
    XCTAssertNil(harness.store.successMessage)
    XCTAssertNotNil(harness.store.errorMessage)
    XCTAssertNil(harness.store.pendingControl)
    XCTAssertEqual(harness.runtime.refreshCount, 1)
  }

  private func makeHarness(
    repository: StrategyWorkspaceRepositorySpy,
    authentication: StrategyAuthenticationSpy = StrategyAuthenticationSpy(),
    scopes: Set<String> = ["strategy:read", "strategy:control", "trade:approve"]
  ) -> (store: StrategyWorkspace, runtime: StrategyWorkspaceRuntimeSpy) {
    let runtime = StrategyWorkspaceRuntimeSpy()
    let store = StrategyWorkspace(localAuthentication: authentication)
    store.configure(
      contextProvider: { runtime.context },
      refreshSession: {},
      refreshStrategies: { runtime.refreshCount += 1 }
    )
    store.activate(
      identity: StrategyWorkspace.SessionIdentity(
        userID: "user-1",
        deviceSessionID: "session-1",
        activeAccountID: "ACCOUNT-1",
        authorizedAccountIDs: ["ACCOUNT-1"],
        grantedScopes: scopes
      ),
      repository: repository
    )
    return (store, runtime)
  }

  private func makeSnapshot(
    instanceID: String = "instance-1",
    version: String = "4",
    value: Int = 3
  ) -> StrategyMobileParameterSnapshot {
    StrategyMobileParameterSnapshot(
      instanceID: instanceID,
      configVersion: version,
      editable: true,
      parameters: [
        StrategyMobileParameter(
          key: "threshold",
          title: "阈值",
          description: "服务端 allowlist 参数",
          kind: .integer,
          currentValue: .integer(value),
          unit: nil,
          minimum: 1,
          maximum: 10,
          step: 1,
          enumValues: [],
          applyImmediately: false,
          riskLevel: .medium
        )
      ]
    )
  }

  private func makeInstance(
    mode: String,
    status: String,
    id: String = "instance-1"
  ) -> StrategyMonitorItem {
    StrategyMonitorItem(
      id: id,
      strategyKey: "strategy",
      strategyID: 1,
      strategyName: "策略",
      instrumentCode: "600519.SH",
      displayName: "策略实例",
      status: status,
      mode: mode,
      parameterVersion: "4",
      createdAt: Date(),
      updatedAt: Date(),
      lastDecisionAt: nil,
      latestExecutionStatus: nil
    )
  }
}

@MainActor
private final class StrategyWorkspaceRepositorySpy: StrategyWorkspaceLoading {
  struct UpdateCall: Equatable {
    let instanceID: String
    let values: [String: StrategyMobileParameterValue]
    let expectedVersion: String
    let applyImmediately: Bool
  }

  struct PreviewCall: Equatable {
    let action: StrategyLiveControlAction
    let instanceID: String
    let expectedConfigVersion: String
    let context: StrategyWorkspaceRepositoryContext
  }

  var updateError: Error?
  var confirmError: Error?
  private var snapshots: [StrategyMobileParameterSnapshot]
  private var loadCount = 0
  private(set) var updateCalls: [UpdateCall] = []
  private(set) var previewCalls: [PreviewCall] = []
  private(set) var confirmCalls: [StrategyControlPreviewTicket] = []
  private(set) var pauseCount = 0
  private(set) var resumeCount = 0

  init(snapshots: [StrategyMobileParameterSnapshot]) {
    self.snapshots = snapshots
  }

  func loadMobileParameters(
    instanceID: String
  ) async throws -> StrategyMobileParameterSnapshot {
    let index = min(loadCount, snapshots.count - 1)
    loadCount += 1
    let snapshot = snapshots[index]
    if snapshot.instanceID == instanceID { return snapshot }
    return StrategyMobileParameterSnapshot(
      instanceID: instanceID,
      configVersion: snapshot.configVersion,
      editable: snapshot.editable,
      parameters: snapshot.parameters
    )
  }

  func updateMobileParameters(
    instanceID: String,
    values: [String: StrategyMobileParameterValue],
    expectedVersion: String,
    applyImmediately: Bool
  ) async throws {
    updateCalls.append(
      UpdateCall(
        instanceID: instanceID,
        values: values,
        expectedVersion: expectedVersion,
        applyImmediately: applyImmediately
      )
    )
    if let updateError { throw updateError }
  }

  func pause(instanceID: String) async throws -> String {
    pauseCount += 1
    return "服务端已暂停"
  }

  func resumePaper(instanceID: String) async throws -> String {
    resumeCount += 1
    return "服务端已恢复"
  }

  func previewLiveControl(
    action: StrategyLiveControlAction,
    instanceID: String,
    expectedConfigVersion: String,
    idempotencyKey: UUID,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlPreviewTicket {
    previewCalls.append(
      PreviewCall(
        action: action,
        instanceID: instanceID,
        expectedConfigVersion: expectedConfigVersion,
        context: context
      )
    )
    let targetID = action == .clone ? "live-target-1" : instanceID
    let mode = action == .clone ? "PAPER" : "LIVE"
    let status = action == .resume ? "PAUSED" : "STOPPED"
    return StrategyControlPreviewTicket(
      id: UUID().uuidString.lowercased(),
      confirmationToken: "memory-only-token",
      sessionContextID: context.sessionContextID,
      userID: context.userID,
      deviceSessionID: context.deviceSessionID,
      accountID: context.activeAccountID,
      instanceID: instanceID,
      targetInstanceID: targetID,
      action: action,
      configVersion: expectedConfigVersion,
      currentMode: mode,
      currentStatus: status,
      readinessStatus: "READY",
      snapshotID: "snapshot-1",
      snapshotAt: Date(),
      expiresAt: Date().addingTimeInterval(60),
      checks: [
        StrategyControlReadinessCheck(
          code: "AGENT_READY",
          passed: true,
          message: "Agent 已就绪"
        )
      ],
      warnings: ["确认不代表委托或成交"]
    )
  }

  func confirmLiveControl(
    _ preview: StrategyControlPreviewTicket,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlConfirmation {
    confirmCalls.append(preview)
    if let confirmError { throw confirmError }
    return StrategyControlConfirmation(
      challengeID: preview.id,
      instanceID: preview.targetInstanceID,
      status: "APPLIED",
      message: "Engine 已应用策略控制"
    )
  }
}

@MainActor
private final class StrategyAuthenticationSpy: LocalAuthenticationProviding {
  var tradeAuthorizationAvailable = true
  private(set) var reasons: [String] = []

  func unlock(reason: String) async throws {}

  func authorizeTrade(reason: String) async throws {
    reasons.append(reason)
  }
}

@MainActor
private final class StrategyWorkspaceRuntimeSpy {
  var accountID: String? = "ACCOUNT-1"
  var localSessionLocked = false
  var accountDataEnabled = true
  var refreshCount = 0

  var context: StrategyWorkspaceRuntimeContext {
    StrategyWorkspaceRuntimeContext(
      accountID: accountID,
      localSessionLocked: localSessionLocked,
      accountDataEnabled: accountDataEnabled
    )
  }
}
