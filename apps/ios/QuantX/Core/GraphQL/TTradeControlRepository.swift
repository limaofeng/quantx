import Apollo
import Foundation

@MainActor
protocol TTradeControlLoading: AnyObject {
  func loadControlState(
    accountID: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlSnapshot

  func previewControl(
    _ request: TTradeControlPreviewRequest,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlPreviewTicket

  func confirmControl(
    _ preview: TTradeControlPreviewTicket,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlConfirmation

  func pauseEntries(
    accountID: String,
    reason: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradePauseConfirmation
}

@MainActor
final class TTradeControlRepository: TTradeControlLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func loadControlState(
    accountID: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlSnapshot {
    do {
      try Self.validate(context)
      guard accountID == context.activeAccountID else {
        throw TTradeControlError.contextChanged
      }
      let response = try await client.fetch(
        query: QuantXAPI.IOSTTradeControlStateQuery(accountId: accountID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw TTradeControlError.invalidResponse
      }
      let monitor = data.tTradeGlobalMonitor
      let readiness = data.validateTTradeLiveReadiness
      let checks = try readiness.checks.map {
        try Self.mapCheck(
          code: $0.code,
          passed: $0.passed,
          message: $0.message,
          scope: $0.scope
        )
      }
      return try Self.mapSnapshot(
        requestedAccountID: accountID,
        monitorAccountID: monitor.accountId,
        monitorEnabled: monitor.enabled,
        monitorMode: monitor.mode,
        monitorStage: monitor.rolloutStage,
        monitorKillSwitch: monitor.killSwitch,
        pendingSignalCount: monitor.pendingSignalCount,
        activeBatchCount: monitor.activeBatchCount,
        drainingCount: monitor.drainingCount,
        positionSnapshotSource: monitor.positionSnapshotSource,
        positionSnapshotReportedAt: monitor.positionSnapshotReportedAt,
        positionSnapshotReceivedAt: monitor.positionSnapshotReceivedAt,
        positionSnapshotComplete: monitor.positionSnapshotComplete,
        positionSnapshotError: monitor.positionSnapshotError,
        projectionGeneratedAt: monitor.projectionGeneratedAt,
        readinessAccountID: readiness.accountId,
        ready: readiness.ready,
        status: readiness.status,
        preparationReady: readiness.preparationReady,
        automationReady: readiness.automationReady,
        readinessStage: readiness.stage,
        engineStatus: readiness.engineStatus,
        agentStatus: readiness.agentStatus,
        agentDeviceID: readiness.agentDeviceId,
        agentMode: readiness.agentMode,
        protocolVersion: readiness.protocolVersion,
        reconcileStatus: readiness.reconcileStatus,
        readinessKillSwitch: readiness.killSwitch,
        policyVersion: readiness.policyVersion,
        canApprove: readiness.canApprove,
        canActivateLive: readiness.canActivateLive,
        blockedReasons: readiness.blockedReasons,
        preparationBlockedReasons: readiness.preparationBlockedReasons,
        checks: checks,
        snapshotID: readiness.snapshotId,
        snapshotHash: readiness.snapshotHash,
        snapshotAt: readiness.snapshotAt,
        reconciliationAgeSeconds: readiness.reconciliationAgeSeconds,
        controlledWindowActive: readiness.controlledWindowActive,
        controlledWindowSnapshotID: readiness.controlledWindowSnapshotId,
        controlledWindowStartedAt: readiness.controlledWindowStartedAt,
        queuedCommandCount: readiness.queuedCommandCount,
        queueDelaySeconds: readiness.queueDelaySeconds,
        deadLetterCount: readiness.deadLetterCount,
        unresolvedCriticalAlertCount: readiness.unresolvedCriticalAlertCount,
        manualCoexistence: readiness.manualCoexistence,
        externalOrderCount: readiness.externalOrderCount,
        externalTradeCount: readiness.externalTradeCount,
        newExternalOrderCount: readiness.newExternalOrderCount,
        newExternalTradeCount: readiness.newExternalTradeCount,
        workingExternalOrderCount: readiness.workingExternalOrderCount,
        journalIntegrity: readiness.journalIntegrity,
        journalSizeBytes: readiness.journalSizeBytes,
        journalPendingReports: readiness.journalPendingReports,
        lastBackupAt: readiness.lastBackupAt,
        checkedAt: readiness.checkedAt
      )
    } catch {
      throw Self.map(error)
    }
  }

  func previewControl(
    _ request: TTradeControlPreviewRequest,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlPreviewTicket {
    do {
      try Self.validate(request, context: context)
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPreviewTTradeControlMutation(
          input: QuantXAPI.TTradeControlPreviewInput(
            accountId: request.accountID,
            action: .init(request.action.graphQLValue),
            policyVersion: Int32(request.policyVersion),
            idempotencyKey: request.idempotencyKeyValue,
            snapshotId: request.snapshotID,
            targetStage: request.targetStage.map {
              GraphQLNullable.some(GraphQLEnum($0.graphQLValue))
            } ?? .null,
            reason: request.reason
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.previewTTradeControl else {
        throw TTradeControlError.invalidResponse
      }
      guard result.success,
        result.code == "T_TRADE_CONTROL_PREVIEW_READY",
        let preview = result.preview
      else {
        throw Self.rejection(code: result.code, message: result.message)
      }
      let checks = try preview.checks.map {
        try Self.mapCheck(
          code: $0.code,
          passed: $0.passed,
          message: $0.message,
          scope: $0.scope
        )
      }
      return try Self.mapPreview(
        challengeID: preview.challengeId,
        confirmationToken: preview.confirmationToken,
        tokenIssued: preview.tokenIssued,
        responseAccountID: preview.accountId,
        responseAction: preview.action.rawValue,
        responsePolicyVersion: preview.policyVersion,
        responseSnapshotID: preview.snapshotId,
        responseTargetStage: preview.targetStage?.rawValue,
        responseReason: preview.reason,
        currentStage: preview.currentStage,
        readinessStatus: preview.readinessStatus,
        readinessFingerprint: preview.readinessFingerprint,
        expiresAt: preview.challengeExpiresAt,
        challengeStatus: preview.challengeStatus,
        operationStatus: preview.operationStatus,
        checks: checks,
        warnings: preview.warnings,
        request: request,
        context: context
      )
    } catch {
      throw Self.map(error)
    }
  }

  func confirmControl(
    _ preview: TTradeControlPreviewTicket,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradeControlConfirmation {
    do {
      try Self.validate(preview, context: context)
      guard !preview.isExpired() else { throw TTradeControlError.challengeExpired }
      let response = try await client.perform(
        mutation: QuantXAPI.IOSConfirmTTradeControlMutation(
          input: QuantXAPI.TTradeControlConfirmationInput(
            challengeId: preview.id,
            confirmationToken: preview.confirmationToken
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.confirmTTradeControl else {
        throw TTradeControlError.invalidResponse
      }
      return try Self.mapConfirmation(
        success: result.success,
        code: result.code,
        message: result.message,
        challengeID: result.challengeId,
        accountID: result.accountId,
        action: result.action?.rawValue,
        challengeConsumed: result.challengeConsumed,
        operationStatus: result.operationStatus,
        readinessAccountID: result.readiness?.accountId,
        preview: preview
      )
    } catch {
      throw Self.map(error)
    }
  }

  func pauseEntries(
    accountID: String,
    reason: String,
    context: TTradeControlRepositoryContext
  ) async throws -> TTradePauseConfirmation {
    do {
      try Self.validate(context)
      guard accountID == context.activeAccountID else {
        throw TTradeControlError.contextChanged
      }
      let normalizedReason = try Self.normalizedReason(reason, required: true)
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPauseTTradeEntriesMutation(
          accountId: accountID,
          reason: normalizedReason
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.pauseTTradeEntries else {
        throw TTradeControlError.invalidResponse
      }
      guard result.success,
        result.code == "ENTRIES_PAUSED",
        result.readiness?.accountId == accountID
      else {
        if !result.success {
          throw Self.rejection(code: result.code, message: result.message)
        }
        throw TTradeControlError.invalidResponse
      }
      return TTradePauseConfirmation(
        accountID: accountID,
        code: result.code,
        message: try Self.nonempty(result.message, maximumLength: 500)
      )
    } catch {
      throw Self.map(error)
    }
  }

  static func mapCheck(
    code: String,
    passed: Bool,
    message: String,
    scope: String
  ) throws -> TTradeSafetyCheck {
    TTradeSafetyCheck(
      code: try nonempty(code, maximumLength: 128).uppercased(),
      passed: passed,
      message: try nonempty(message, maximumLength: 500),
      scope: try nonempty(scope, maximumLength: 64).uppercased()
    )
  }

  // Kept primitive so malformed/unknown GraphQL values can be exercised without networking.
  static func mapPreview(
    challengeID: String,
    confirmationToken: String?,
    tokenIssued: Bool,
    responseAccountID: String,
    responseAction: String,
    responsePolicyVersion: Int,
    responseSnapshotID: String,
    responseTargetStage: String?,
    responseReason: String,
    currentStage: String,
    readinessStatus: String,
    readinessFingerprint: String,
    expiresAt: String,
    challengeStatus: String,
    operationStatus: String,
    checks: [TTradeSafetyCheck],
    warnings: [String],
    request: TTradeControlPreviewRequest,
    context: TTradeControlRepositoryContext,
    now: Date = Date()
  ) throws -> TTradeControlPreviewTicket {
    try validate(request, context: context)
    guard tokenIssued else { throw TTradeControlError.duplicatePreviewUnavailable }
    let token = try nonempty(confirmationToken ?? "", maximumLength: 256)
    let normalizedID = try nonempty(challengeID, maximumLength: 120)
    guard UUID(uuidString: normalizedID) != nil else {
      throw TTradeControlError.invalidResponse
    }
    let normalizedStage = try uppercased(currentStage, maximumLength: 40)
    let normalizedStatus = try uppercased(readinessStatus, maximumLength: 80)
    let expectedStatus =
      request.action == .killSwitch
      ? "RISK_REDUCTION_READY"
      : request.expectedReadinessStatus
    let responseTarget = try target(from: responseTargetStage)
    guard
      responseAccountID == request.accountID,
      responseAction == request.action.rawValue,
      responsePolicyVersion == request.policyVersion,
      responseSnapshotID == request.snapshotID,
      responseTarget == request.targetStage,
      responseReason == request.reason,
      normalizedStage == request.expectedStage,
      normalizedStatus == expectedStatus,
      challengeStatus == "PENDING",
      operationStatus == "PENDING",
      readinessFingerprint.range(
        of: #"^[0-9a-f]{64}$"#,
        options: .regularExpression
      ) != nil,
      checks.count == request.expectedChecks.count,
      Set(checks) == Set(request.expectedChecks),
      Set(checks.map(\.id)).count == checks.count,
      !warnings.isEmpty,
      warnings.count <= 20,
      warnings.allSatisfy({
        !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && $0.count <= 500
      })
    else {
      throw TTradeControlError.contextChanged
    }
    let expiration = try ReadOnlyModelValidator.requireDate(
      expiresAt,
      field: "tTrade.control.challengeExpiresAt"
    )
    guard expiration > now, expiration <= now.addingTimeInterval(120) else {
      throw TTradeControlError.challengeExpired
    }
    return TTradeControlPreviewTicket(
      id: normalizedID,
      confirmationToken: token,
      sessionContextID: context.sessionContextID,
      userID: context.userID,
      deviceSessionID: context.deviceSessionID,
      accountID: request.accountID,
      action: request.action,
      policyVersion: request.policyVersion,
      snapshotID: request.snapshotID,
      targetStage: request.targetStage,
      reason: request.reason,
      currentStage: normalizedStage,
      readinessStatus: normalizedStatus,
      readinessFingerprint: readinessFingerprint,
      expiresAt: expiration,
      checks: checks.sorted { $0.id < $1.id },
      warnings: warnings.map {
        String($0.trimmingCharacters(in: .whitespacesAndNewlines).prefix(500))
      },
      snapshotBinding: request.snapshotBinding
    )
  }

  static func mapConfirmation(
    success: Bool,
    code: String,
    message: String,
    challengeID: String?,
    accountID: String?,
    action: String?,
    challengeConsumed: Bool,
    operationStatus: String,
    readinessAccountID: String?,
    preview: TTradeControlPreviewTicket
  ) throws -> TTradeControlConfirmation {
    let status = try uppercased(operationStatus, maximumLength: 40)
    if status == "DISPATCHING", challengeConsumed {
      throw TTradeControlError.resultUncertain
    }
    guard success else { throw rejection(code: code, message: message) }
    let expectedCode: String =
      switch preview.action {
      case .beginControlledWindow: "CONTROLLED_WINDOW_APPLIED"
      case .activateCanary: "CANARY_ACTIVATION_APPLIED"
      case .activateLive: "LIVE_ACTIVATION_APPLIED"
      case .killSwitch: "KILL_SWITCH_APPLIED"
      }
    guard
      challengeID == preview.id,
      accountID == preview.accountID,
      action == preview.action.rawValue,
      challengeConsumed,
      status == "APPLIED",
      code == expectedCode,
      readinessAccountID == nil || readinessAccountID == preview.accountID
    else {
      throw TTradeControlError.invalidResponse
    }
    return TTradeControlConfirmation(
      challengeID: preview.id,
      accountID: preview.accountID,
      action: preview.action,
      code: code,
      operationStatus: status,
      message: try nonempty(message, maximumLength: 500)
    )
  }

  static func mapSnapshot(
    requestedAccountID: String,
    monitorAccountID: String,
    monitorEnabled: Bool,
    monitorMode: String,
    monitorStage: String,
    monitorKillSwitch: Bool,
    pendingSignalCount: Int,
    activeBatchCount: Int,
    drainingCount: Int,
    positionSnapshotSource: String?,
    positionSnapshotReportedAt: String?,
    positionSnapshotReceivedAt: String?,
    positionSnapshotComplete: Bool,
    positionSnapshotError: String?,
    projectionGeneratedAt: String?,
    readinessAccountID: String,
    ready: Bool,
    status: String,
    preparationReady: Bool,
    automationReady: Bool,
    readinessStage: String,
    engineStatus: String,
    agentStatus: String,
    agentDeviceID: String?,
    agentMode: String,
    protocolVersion: String,
    reconcileStatus: String,
    readinessKillSwitch: Bool,
    policyVersion: Int,
    canApprove: Bool,
    canActivateLive: Bool,
    blockedReasons: [String],
    preparationBlockedReasons: [String],
    checks: [TTradeSafetyCheck],
    snapshotID: String?,
    snapshotHash: String?,
    snapshotAt: String?,
    reconciliationAgeSeconds: Double?,
    controlledWindowActive: Bool,
    controlledWindowSnapshotID: String?,
    controlledWindowStartedAt: String?,
    queuedCommandCount: Int,
    queueDelaySeconds: Double,
    deadLetterCount: Int,
    unresolvedCriticalAlertCount: Int,
    manualCoexistence: Bool,
    externalOrderCount: Int,
    externalTradeCount: Int,
    newExternalOrderCount: Int,
    newExternalTradeCount: Int,
    workingExternalOrderCount: Int,
    journalIntegrity: String,
    journalSizeBytes: Int,
    journalPendingReports: Int,
    lastBackupAt: String?,
    checkedAt: String,
    now: Date = Date()
  ) throws -> TTradeControlSnapshot {
    let accountID = try nonempty(requestedAccountID, maximumLength: 50)
    let stage = try uppercased(readinessStage, maximumLength: 40)
    let monitorStage = try uppercased(monitorStage, maximumLength: 40)
    let normalizedStatus = try uppercased(status, maximumLength: 80)
    let mode = try uppercased(monitorMode, maximumLength: 40)
    let snapshotID = try optionalNonempty(snapshotID, maximumLength: 128)
    let snapshotHash = try optionalNonempty(snapshotHash, maximumLength: 128)
    let controlledSnapshot = try optionalNonempty(
      controlledWindowSnapshotID,
      maximumLength: 128
    )
    guard
      monitorAccountID == accountID,
      readinessAccountID == accountID,
      monitorStage == stage,
      monitorKillSwitch == readinessKillSwitch,
      policyVersion > 0,
      policyVersion <= Int(Int32.max),
      [
        pendingSignalCount, activeBatchCount, drainingCount, queuedCommandCount,
        deadLetterCount, unresolvedCriticalAlertCount, externalOrderCount,
        externalTradeCount, newExternalOrderCount, newExternalTradeCount,
        workingExternalOrderCount, journalSizeBytes, journalPendingReports,
      ]
      .allSatisfy({ $0 >= 0 }),
      queueDelaySeconds.isFinite,
      queueDelaySeconds >= 0,
      reconciliationAgeSeconds.map({ $0.isFinite && $0 >= 0 }) ?? true,
      (snapshotID == nil) == (snapshotHash == nil),
      snapshotHash == nil
        || snapshotHash?.range(
          of: #"^[0-9a-fA-F]{64}$"#,
          options: .regularExpression
        ) != nil,
      !controlledWindowActive || controlledSnapshot != nil,
      Set(checks.map(\.id)).count == checks.count,
      checks.count <= 100
    else {
      throw TTradeControlError.invalidResponse
    }
    let checkedDate = try ReadOnlyModelValidator.requireDate(
      checkedAt,
      field: "tTrade.control.checkedAt"
    )
    guard checkedDate <= now.addingTimeInterval(300) else {
      throw TTradeControlError.invalidResponse
    }
    return TTradeControlSnapshot(
      accountID: accountID,
      monitorEnabled: monitorEnabled,
      monitorMode: mode,
      stage: stage,
      ready: ready,
      status: normalizedStatus,
      preparationReady: preparationReady,
      automationReady: automationReady,
      engineStatus: try uppercased(engineStatus, maximumLength: 80),
      agentStatus: try uppercased(agentStatus, maximumLength: 80),
      agentDeviceID: try optionalNonempty(agentDeviceID, maximumLength: 128),
      agentMode: try uppercasedAllowingEmpty(agentMode, maximumLength: 40),
      protocolVersion: try uppercasedAllowingEmpty(protocolVersion, maximumLength: 40),
      reconcileStatus: try uppercased(reconcileStatus, maximumLength: 80),
      killSwitch: readinessKillSwitch,
      policyVersion: policyVersion,
      canApprove: canApprove,
      canActivateLive: canActivateLive,
      blockedReasons: try normalizedMessages(blockedReasons),
      preparationBlockedReasons: try normalizedMessages(preparationBlockedReasons),
      checks: checks.sorted { $0.id < $1.id },
      snapshotID: snapshotID,
      snapshotHash: snapshotHash?.lowercased(),
      snapshotAt: try optionalDate(snapshotAt, field: "tTrade.control.snapshotAt"),
      reconciliationAgeSeconds: reconciliationAgeSeconds,
      controlledWindowActive: controlledWindowActive,
      controlledWindowSnapshotID: controlledSnapshot,
      controlledWindowStartedAt: try optionalDate(
        controlledWindowStartedAt,
        field: "tTrade.control.controlledWindowStartedAt"
      ),
      positionSnapshotSource: try optionalNonempty(
        positionSnapshotSource,
        maximumLength: 128
      ),
      positionSnapshotReportedAt: try optionalDate(
        positionSnapshotReportedAt,
        field: "tTrade.control.positionSnapshotReportedAt"
      ),
      positionSnapshotReceivedAt: try optionalDate(
        positionSnapshotReceivedAt,
        field: "tTrade.control.positionSnapshotReceivedAt"
      ),
      positionSnapshotComplete: positionSnapshotComplete,
      positionSnapshotError: try optionalNonempty(positionSnapshotError, maximumLength: 500),
      queuedCommandCount: queuedCommandCount,
      queueDelaySeconds: queueDelaySeconds,
      deadLetterCount: deadLetterCount,
      unresolvedCriticalAlertCount: unresolvedCriticalAlertCount,
      manualCoexistence: manualCoexistence,
      externalOrderCount: externalOrderCount,
      externalTradeCount: externalTradeCount,
      newExternalOrderCount: newExternalOrderCount,
      newExternalTradeCount: newExternalTradeCount,
      workingExternalOrderCount: workingExternalOrderCount,
      journalIntegrity: try uppercased(journalIntegrity, maximumLength: 80),
      journalSizeBytes: journalSizeBytes,
      journalPendingReports: journalPendingReports,
      lastBackupAt: try optionalDate(lastBackupAt, field: "tTrade.control.lastBackupAt"),
      pendingSignalCount: pendingSignalCount,
      activeBatchCount: activeBatchCount,
      drainingCount: drainingCount,
      projectionGeneratedAt: try optionalDate(
        projectionGeneratedAt,
        field: "tTrade.control.projectionGeneratedAt"
      ),
      checkedAt: checkedDate,
      fetchedAt: now
    )
  }

  private static func validate(
    _ request: TTradeControlPreviewRequest,
    context: TTradeControlRepositoryContext
  ) throws {
    try validate(context)
    let reason = try normalizedReason(
      request.reason,
      required: request.action == .killSwitch
    )
    guard
      request.accountID == context.activeAccountID,
      request.policyVersion > 0,
      request.policyVersion <= Int(Int32.max),
      request.snapshotID.count <= 128,
      request.action == .killSwitch || !request.snapshotID.isEmpty,
      request.targetStage == request.action.targetStage,
      reason == request.reason,
      request.expectedStage == request.expectedStage.uppercased(),
      request.expectedReadinessStatus == request.expectedReadinessStatus.uppercased(),
      Set(request.expectedChecks.map(\.id)).count == request.expectedChecks.count,
      !request.snapshotBinding.isEmpty
    else {
      throw TTradeControlError.contextChanged
    }
  }

  private static func validate(
    _ preview: TTradeControlPreviewTicket,
    context: TTradeControlRepositoryContext
  ) throws {
    try validate(context)
    guard
      preview.sessionContextID == context.sessionContextID,
      preview.userID == context.userID,
      preview.deviceSessionID == context.deviceSessionID,
      preview.accountID == context.activeAccountID,
      preview.policyVersion > 0,
      preview.targetStage == preview.action.targetStage,
      !preview.snapshotBinding.isEmpty
    else {
      throw TTradeControlError.contextChanged
    }
  }

  private static func validate(_ context: TTradeControlRepositoryContext) throws {
    guard
      !context.userID.isEmpty,
      !context.deviceSessionID.isEmpty,
      !context.activeAccountID.isEmpty,
      context.authorizedAccountIDs == [context.activeAccountID]
    else {
      throw TTradeControlError.contextChanged
    }
  }

  private static func target(from value: String?) throws -> TTradeSafetyTarget? {
    guard let value else { return nil }
    guard let target = TTradeSafetyTarget(rawValue: value) else {
      throw TTradeControlError.invalidResponse
    }
    return target
  }

  private static func rejection(code: String, message: String) -> TTradeControlError {
    let normalizedCode = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    if ["CONFIRMATION_EXPIRED", "CHALLENGE_EXPIRED"].contains(normalizedCode) {
      return .challengeExpired
    }
    return .rejected(
      code: String(
        (normalizedCode.isEmpty ? "T_TRADE_CONTROL_REJECTED" : normalizedCode).prefix(100)),
      message: (try? nonempty(message, maximumLength: 500)) ?? "做 T 控制未应用"
    )
  }

  private static func normalizedReason(_ value: String, required: Bool) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      !required || !normalized.isEmpty,
      normalized.count <= 512,
      !normalized.unicodeScalars.contains(where: {
        $0.value < 32 || $0.value == 127
      })
    else {
      throw TTradeControlError.invalidRequest(
        required ? "紧急熔断必须填写原因" : "做 T 控制原因无效"
      )
    }
    return normalized
  }

  private static func nonempty(_ value: String, maximumLength: Int) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty, normalized.count <= maximumLength else {
      throw TTradeControlError.invalidResponse
    }
    return normalized
  }

  private static func optionalNonempty(
    _ value: String?,
    maximumLength: Int
  ) throws -> String? {
    guard let value else { return nil }
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty else { return nil }
    guard normalized.count <= maximumLength else {
      throw TTradeControlError.invalidResponse
    }
    return normalized
  }

  private static func uppercased(_ value: String, maximumLength: Int) throws -> String {
    try nonempty(value, maximumLength: maximumLength).uppercased()
  }

  private static func uppercasedAllowingEmpty(
    _ value: String,
    maximumLength: Int
  ) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard normalized.count <= maximumLength else { throw TTradeControlError.invalidResponse }
    return normalized.uppercased()
  }

  private static func normalizedMessages(_ values: [String]) throws -> [String] {
    guard values.count <= 100 else { throw TTradeControlError.invalidResponse }
    return try values.map { try nonempty($0, maximumLength: 500) }
  }

  private static func optionalDate(_ value: String?, field: String) throws -> Date? {
    guard let value else { return nil }
    return try ReadOnlyModelValidator.requireDate(value, field: field)
  }

  private static func map(_ error: Error) -> Error {
    if error is CancellationError { return CancellationError() }
    if let error = error as? TTradeControlError { return error }
    if let error = error as? ReadOnlyRepositoryError { return error }
    if error is ReadOnlyMappingError { return TTradeControlError.invalidResponse }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      return ApolloReadOnlyResponseValidator.mapResponseCode(error)
    }
    return ReadOnlyRepositoryError.transport
  }
}
