import XCTest

@testable import QuantX

final class StrategyModelsTests: XCTestCase {
  func testMobileParameterMapperAcceptsOnlyTypedServerAllowlist() throws {
    let toggle = try makeParameter(
      key: "auto_exit",
      valueType: "boolean",
      currentValue: .boolean(true),
      riskLevel: "HIGH"
    )
    let threshold = try makeParameter(
      key: "drawdown",
      valueType: "number",
      currentValue: .number(0.08),
      minimum: 0,
      maximum: 0.2,
      step: 0.01
    )

    XCTAssertEqual(toggle.currentValue, .boolean(true))
    XCTAssertEqual(toggle.riskLevel, .high)
    XCTAssertEqual(threshold.currentValue, .number(0.08))
    XCTAssertTrue(threshold.validates(.number(0.09)))
    XCTAssertFalse(threshold.validates(.number(0.095)))

    XCTAssertThrowsError(
      try makeParameter(
        key: "raw_payload",
        valueType: "object",
        currentValue: GraphQLJSON(object: ["hidden": .string("value")])
      )
    )
    XCTAssertThrowsError(
      try makeParameter(
        key: "raw_list",
        valueType: "array",
        currentValue: .array([.integer(1), .integer(2)])
      )
    )
    XCTAssertThrowsError(
      try makeParameter(
        key: "forged",
        valueType: "integer",
        currentValue: .string("10")
      )
    )
    XCTAssertThrowsError(
      try makeParameter(
        key: "risk",
        valueType: "number",
        currentValue: .number(0.1),
        riskLevel: "UNKNOWN"
      )
    )
  }

  func testGraphQLJSONLosslesslyRoundTripsNestedArraysAndObjects() throws {
    let value = GraphQLJSON.array([
      .boolean(true),
      .integer(3),
      .number(0.25),
      .string("规则"),
      GraphQLJSON(object: [
        "nested": .array([.null, .string("value")])
      ]),
    ])

    XCTAssertEqual(try GraphQLJSON(_jsonValue: value._jsonValue), value)
  }

  func testParameterConflictShowsBothValuesAndFailsClosedWhenAllowlistChanges() throws {
    let threshold = try makeParameter(
      key: "threshold",
      valueType: "integer",
      currentValue: .integer(6)
    )
    let snapshot = try StrategyMobileParameterMapper.snapshot(
      requestedInstanceID: "instance-1",
      instanceID: "instance-1",
      configVersion: "9",
      editable: true,
      parameters: [threshold]
    )
    let conflict = StrategyParameterConflict(
      staleVersion: "7",
      serverSnapshot: snapshot,
      userValues: ["threshold": .integer(4)]
    )

    XCTAssertEqual(conflict.serverVersion, "9")
    XCTAssertEqual(conflict.differences.count, 1)
    XCTAssertEqual(conflict.differences[0].userValue, .integer(4))
    XCTAssertEqual(conflict.differences[0].serverValue, .integer(6))
    XCTAssertTrue(conflict.canResubmit)
    XCTAssertEqual(conflict.rebasedDraftValues, ["threshold": .integer(4)])

    let changedAllowlist = StrategyParameterConflict(
      staleVersion: "7",
      serverSnapshot: snapshot,
      userValues: ["removed_key": .integer(4)]
    )
    XCTAssertTrue(changedAllowlist.allowlistChanged)
    XCTAssertFalse(changedAllowlist.canResubmit)
    XCTAssertEqual(changedAllowlist.differences.count, 2)
    XCTAssertEqual(changedAllowlist.rebasedDraftValues, ["threshold": .integer(6)])
  }

  func testMobileSnapshotRequiresUniqueKeysExactInstanceAndPositiveVersion() throws {
    let parameter = try makeParameter(
      key: "threshold",
      valueType: "integer",
      currentValue: .integer(3)
    )
    let snapshot = try StrategyMobileParameterMapper.snapshot(
      requestedInstanceID: "instance-1",
      instanceID: "instance-1",
      configVersion: "7",
      editable: true,
      parameters: [parameter]
    )

    XCTAssertEqual(snapshot.values, ["threshold": .integer(3)])
    XCTAssertThrowsError(
      try StrategyMobileParameterMapper.snapshot(
        requestedInstanceID: "instance-1",
        instanceID: "instance-2",
        configVersion: "7",
        editable: true,
        parameters: [parameter]
      )
    )
    XCTAssertThrowsError(
      try StrategyMobileParameterMapper.snapshot(
        requestedInstanceID: "instance-1",
        instanceID: "instance-1",
        configVersion: "07",
        editable: true,
        parameters: [parameter, parameter]
      )
    )
  }

  func testLifecycleActionsFollowPublishedControlContract() {
    XCTAssertEqual(makeInstance(mode: "PAPER", status: "RUNNING").lifecycleControls, [.pause])
    XCTAssertEqual(
      makeInstance(mode: "PAPER", status: "PAUSED").lifecycleControls,
      [.resumePaper, .cloneToLive]
    )
    XCTAssertEqual(makeInstance(mode: "LIVE", status: "RUNNING").lifecycleControls, [.pause])
    XCTAssertEqual(
      makeInstance(mode: "LIVE", status: "PAUSED").lifecycleControls,
      [.resumeLive]
    )
    XCTAssertEqual(
      makeInstance(mode: "LIVE", status: "STOPPED").lifecycleControls,
      [.startLive]
    )
    XCTAssertTrue(makeInstance(mode: "BACKTEST", status: "RUNNING").lifecycleControls.isEmpty)
  }

  func testLivePreviewMappingBindsEverySecurityDimension() throws {
    let contextID = UUID()
    let expiresAt = ISO8601DateFormatter().string(
      from: Date().addingTimeInterval(60)
    )
    let ticket = try StrategyControlPreviewMapper.map(
      challengeID: "challenge-1",
      confirmationToken: "memory-only-token",
      sessionContextID: contextID,
      userID: "user-1",
      deviceSessionID: "session-1",
      accountID: "ACCOUNT-1",
      responseAccountID: "ACCOUNT-1",
      requestedInstanceID: "instance-1",
      responseInstanceID: "instance-1",
      targetInstanceID: "instance-1",
      requestedAction: .resume,
      responseAction: "RESUME_LIVE",
      expectedConfigVersion: "4",
      responseConfigVersion: "4",
      currentMode: "live",
      currentStatus: "paused",
      readinessStatus: "READY",
      snapshotID: "snapshot-1",
      snapshotAt: ISO8601DateFormatter().string(from: Date()),
      expiresAt: expiresAt,
      checks: [
        StrategyControlReadinessCheck(
          code: "AGENT_READY",
          passed: true,
          message: "Agent 已就绪"
        )
      ],
      warnings: ["确认不代表成交"]
    )

    XCTAssertEqual(ticket.sessionContextID, contextID)
    XCTAssertEqual(ticket.accountID, "ACCOUNT-1")
    XCTAssertEqual(ticket.deviceSessionID, "session-1")
    XCTAssertEqual(ticket.action, .resume)
    XCTAssertEqual(ticket.configVersion, "4")

    XCTAssertThrowsError(
      try StrategyControlPreviewMapper.map(
        challengeID: "challenge-2",
        confirmationToken: "memory-only-token",
        sessionContextID: contextID,
        userID: "user-1",
        deviceSessionID: "session-1",
        accountID: "ACCOUNT-1",
        responseAccountID: "ACCOUNT-2",
        requestedInstanceID: "instance-1",
        responseInstanceID: "instance-1",
        targetInstanceID: "instance-1",
        requestedAction: .resume,
        responseAction: "START_LIVE",
        expectedConfigVersion: "4",
        responseConfigVersion: "5",
        currentMode: "live",
        currentStatus: "paused",
        readinessStatus: "READY",
        snapshotID: nil,
        snapshotAt: nil,
        expiresAt: expiresAt,
        checks: [],
        warnings: []
      )
    )
  }

  private func makeParameter(
    key: String,
    valueType: String,
    currentValue: GraphQLJSON,
    minimum: Double? = nil,
    maximum: Double? = nil,
    step: Double? = nil,
    riskLevel: String = "LOW"
  ) throws -> StrategyMobileParameter {
    try StrategyMobileParameterMapper.parameter(
      key: key,
      title: key,
      description: "服务端字段",
      valueType: valueType,
      currentValue: currentValue,
      unit: nil,
      minimum: minimum,
      maximum: maximum,
      step: step,
      enumValues: nil,
      applyImmediately: false,
      riskLevel: riskLevel
    )
  }

  private func makeInstance(
    mode: String,
    status: String
  ) -> StrategyMonitorItem {
    StrategyMonitorItem(
      id: "instance-1",
      strategyKey: "strategy",
      strategyID: 1,
      strategyName: "策略",
      instrumentCode: "600519.SH",
      displayName: "策略实例",
      status: status,
      mode: mode,
      parameterVersion: "1",
      createdAt: Date(),
      updatedAt: Date(),
      lastDecisionAt: nil,
      latestExecutionStatus: nil
    )
  }
}
