import Foundation

struct TTradeControlRuntimeContext {
  let accountID: String?
  let localSessionLocked: Bool
  let accountDataEnabled: Bool
}

@MainActor
final class TTradeControlStore: ObservableObject {
  struct SessionIdentity: Equatable {
    let userID: String
    let deviceSessionID: String
    let activeAccountID: String?
    let authorizedAccountIDs: Set<String>
    let grantedScopes: Set<String>
  }

  private struct SessionBinding {
    let identity: SessionIdentity
    let repository: (any TTradeControlLoading)?
    let releaseRepository: (any TAssistantReleaseLoading)?
    let legacyRepository: (any TAssistantLegacyMaintenanceLoading)?
    let accountRepository: (any AccountExecutionControlLoading)?
  }

  typealias ContextProvider = @MainActor () -> TTradeControlRuntimeContext
  typealias RefreshSession = @MainActor () async throws -> Void
  typealias RefreshAssistantProjection = @MainActor () async -> Void

  @Published private(set) var accountControlTicket: NativeAccountControlTicket?
  @Published private(set) var legacyScope: TAssistantLegacyScope?
  @Published private(set) var legacyPreparationID: UUID?
  @Published private(set) var legacyInventory: TAssistantLegacyInventory?
  @Published private(set) var legacyTicket: TAssistantLegacyDrainTicket?
  @Published private(set) var legacyChallengeID: String?
  @Published private(set) var legacyCommandID: String?
  @Published private(set) var legacyStatus: TAssistantLegacyOperation?
  @Published private(set) var legacyConfirmationAttempted = false
  private var legacyContext: TTradeControlRepositoryContext?
  private var legacyOperationGeneration = UUID()
  @Published private(set) var recentReleaseOperations: [TAssistantReleaseOperation] = []
  @Published private(set) var releaseReference: TAssistantReleaseReference?
  @Published private(set) var releaseTicket: TAssistantReleaseTicket?
  @Published private(set) var releaseStatus: TAssistantReleaseStatus?
  @Published private(set) var releaseCommandID: String?
  @Published private(set) var state: TTradeControlState = .idle
  @Published private(set) var refreshInProgress = false
  @Published private(set) var operationInProgress = false
  @Published private(set) var requestedAction: TTradeSafetyAction?
  @Published private(set) var pendingControl: TTradeControlPreviewTicket?
  @Published private(set) var successMessage: String?
  @Published private(set) var errorMessage: String?

  private var releaseConfirmationAttempted = false
  private let localAuthentication: any LocalAuthenticationProviding
  private let legacyNow: @MainActor () -> Date
  private var binding: SessionBinding?
  private var contextProvider: ContextProvider?
  private var refreshSession: RefreshSession?
  private var refreshAssistantProjection: RefreshAssistantProjection?
  private var sessionContextID = UUID()
  private var stateRequestID = UUID()

  init(localAuthentication: any LocalAuthenticationProviding, legacyNow: @escaping @MainActor () -> Date = { Date() }) {
    self.localAuthentication = localAuthentication
    self.legacyNow = legacyNow
  }

  func configure(
    contextProvider: @escaping ContextProvider,
    refreshSession: @escaping RefreshSession,
    refreshAssistantProjection: @escaping RefreshAssistantProjection
  ) {
    self.contextProvider = contextProvider
    self.refreshSession = refreshSession
    self.refreshAssistantProjection = refreshAssistantProjection
  }

  func activate(
    identity: SessionIdentity,
    repository: (any TTradeControlLoading)?,
    releaseRepository: (any TAssistantReleaseLoading)? = nil,
    legacyRepository: (any TAssistantLegacyMaintenanceLoading)? = nil,
    accountRepository: (any AccountExecutionControlLoading)? = nil
  ) {
    if binding?.identity != identity {
      sessionContextID = UUID()
      resetTransientState(resetReadState: true)
    }
    binding = SessionBinding(identity: identity, repository: repository, releaseRepository: releaseRepository, legacyRepository: legacyRepository, accountRepository: accountRepository)
  }

  func clearSession() {
    binding = nil
    sessionContextID = UUID()
    resetTransientState(resetReadState: true)
  }

  func invalidateChallengeContext() {
    sessionContextID = UUID()
    legacyTicket = nil
    accountControlTicket = nil
    recentReleaseOperations = []
    releaseTicket = nil
    releaseStatus = nil
    pendingControl = nil
    requestedAction = nil
  }

  func dismissPendingControl() {
    guard !operationInProgress else { return }
    pendingControl = nil
  }

  func clearMessages() {
    successMessage = nil
    errorMessage = nil
  }

  func refresh() async {
    guard !refreshInProgress else { return }
    guard readUnavailableReason == nil else {
      state = .unavailable(readUnavailableReason ?? "做 T 安全状态不可用")
      return
    }
    guard let accountID = binding?.identity.activeAccountID else {
      state = .unavailable("当前会话没有唯一主账户")
      return
    }
    let previous = state.snapshot
    let requestID = UUID()
    let originalContextID = sessionContextID
    stateRequestID = requestID
    refreshInProgress = true
    if previous == nil { state = .loading }
    defer { refreshInProgress = false }
    do {
      let snapshot = try await performLoad(accountID: accountID)
      apply(snapshot, requestID: requestID, contextID: originalContextID)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(
          expectedContextID: originalContextID,
          requiredScopes: ["strategy:read"]
        )
        guard binding?.identity.activeAccountID == accountID else {
          throw TTradeControlError.contextChanged
        }
        let snapshot = try await performLoad(accountID: accountID)
        apply(snapshot, requestID: requestID, contextID: originalContextID)
      } catch {
        applyLoadFailure(
          error,
          previous: previous,
          requestID: requestID,
          contextID: originalContextID
        )
      }
    } catch {
      applyLoadFailure(
        error,
        previous: previous,
        requestID: requestID,
        contextID: originalContextID
      )
    }
  }

  var readUnavailableReason: String? {
    baseUnavailableReason(requiredScopes: ["strategy:read"], requiresBiometrics: false)
  }

  var pauseUnavailableReason: String? {
    baseUnavailableReason(
      requiredScopes: ["t-trade:control"],
      requiresBiometrics: false
    )
  }

  func controlUnavailableReason(for action: TTradeSafetyAction) -> String? {
    if let reason = baseUnavailableReason(
      requiredScopes: ["t-trade:control", "trade:approve"],
      requiresBiometrics: true
    ) {
      return reason
    }
    guard let snapshot = state.snapshot else {
      return "做 T 生产状态正在从服务端同步"
    }
    guard snapshot.accountID == binding?.identity.activeAccountID else {
      return "生产状态与当前主账户不一致"
    }
    if action != .killSwitch {
      guard let snapshotID = snapshot.snapshotID, !snapshotID.isEmpty,
        snapshot.snapshotHash != nil
      else {
        return "当前没有可绑定的完整安全快照"
      }
    }
    return nil
  }

  func preview(
    action: TTradeSafetyAction,
    reason: String = "",
    idempotencyKey: UUID = UUID()
  ) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    if let unavailable = controlUnavailableReason(for: action) {
      throw fail(TTradeControlError.unavailable(unavailable))
    }
    guard let originalSnapshot = state.snapshot else {
      throw fail(TTradeControlError.unavailable("做 T 安全状态尚未同步"))
    }
    let originalContextID = sessionContextID
    var context = try repositoryContext(
      requiredScopes: ["t-trade:control", "trade:approve"]
    )
    var request = try previewRequest(
      action: action,
      reason: reason,
      idempotencyKey: idempotencyKey,
      snapshot: originalSnapshot
    )
    guard let repository = binding?.repository else {
      throw fail(TTradeControlError.unavailable("做 T 安全控制服务尚未连接"))
    }

    operationInProgress = true
    requestedAction = action
    pendingControl = nil
    successMessage = nil
    errorMessage = nil
    defer {
      operationInProgress = false
      requestedAction = nil
    }
    do {
      let preview = try await repository.previewControl(request, context: context)
      try validateCurrentContext(preview, expectedContextID: originalContextID)
      pendingControl = preview
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(
          expectedContextID: originalContextID,
          requiredScopes: ["t-trade:control", "trade:approve"]
        )
        context = try repositoryContext(
          requiredScopes: ["t-trade:control", "trade:approve"]
        )
        let refreshedSnapshot = try await performLoad(accountID: context.activeAccountID)
        guard
          action == .killSwitch
            || refreshedSnapshot.snapshotBinding == originalSnapshot.snapshotBinding
        else {
          throw TTradeControlError.contextChanged
        }
        state = .loaded(refreshedSnapshot, refreshWarning: nil)
        if action == .killSwitch {
          request = try previewRequest(
            action: action,
            reason: reason,
            idempotencyKey: idempotencyKey,
            snapshot: refreshedSnapshot
          )
        }
        guard let refreshedRepository = binding?.repository else {
          throw TTradeControlError.unavailable("做 T 安全控制服务尚未连接")
        }
        let preview = try await refreshedRepository.previewControl(
          request,
          context: context
        )
        try validateCurrentContext(preview, expectedContextID: originalContextID)
        pendingControl = preview
      } catch {
        throw fail(error)
      }
    } catch {
      throw fail(error)
    }
  }

  func confirm(_ preview: TTradeControlPreviewTicket) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard pendingControl == preview else {
      throw fail(TTradeControlError.contextChanged)
    }
    let originalContextID = sessionContextID
    try validateCurrentContext(preview, expectedContextID: originalContextID)
    guard !preview.isExpired() else {
      pendingControl = nil
      throw fail(TTradeControlError.challengeExpired)
    }
    var context = try repositoryContext(
      requiredScopes: ["t-trade:control", "trade:approve"]
    )
    let biometricReason =
      "确认\(preview.action.title)：账户 \(TTradeControlPrivacy.maskedAccount(preview.accountID))"

    operationInProgress = true
    requestedAction = preview.action
    successMessage = nil
    errorMessage = nil
    defer {
      operationInProgress = false
      requestedAction = nil
      pendingControl = nil
    }
    do {
      try await localAuthentication.authorizeTrade(reason: biometricReason)
      try validateCurrentContext(preview, expectedContextID: originalContextID)
      guard !preview.isExpired() else { throw TTradeControlError.challengeExpired }
      guard let repository = binding?.repository else {
        throw TTradeControlError.unavailable("做 T 安全控制服务尚未连接")
      }
      let confirmation = try await repository.confirmControl(preview, context: context)
      successMessage = confirmation.message
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(
          expectedContextID: originalContextID,
          requiredScopes: ["t-trade:control", "trade:approve"]
        )
        context = try repositoryContext(
          requiredScopes: ["t-trade:control", "trade:approve"]
        )
        let refreshedSnapshot = try await performLoad(accountID: context.activeAccountID)
        state = .loaded(refreshedSnapshot, refreshWarning: nil)
        try validateCurrentContext(preview, expectedContextID: originalContextID)
        guard !preview.isExpired() else { throw TTradeControlError.challengeExpired }
        try await localAuthentication.authorizeTrade(reason: biometricReason)
        try validateCurrentContext(preview, expectedContextID: originalContextID)
        guard !preview.isExpired() else { throw TTradeControlError.challengeExpired }
        guard let repository = binding?.repository else {
          throw TTradeControlError.unavailable("做 T 安全控制服务尚未连接")
        }
        let confirmation = try await repository.confirmControl(preview, context: context)
        successMessage = confirmation.message
      } catch ReadOnlyRepositoryError.transport {
        await refreshTruth()
        throw fail(TTradeControlError.resultUncertain)
      } catch TTradeControlError.resultUncertain {
        await refreshTruth()
        throw fail(TTradeControlError.resultUncertain)
      } catch {
        throw fail(error)
      }
    } catch ReadOnlyRepositoryError.transport {
      await refreshTruth()
      throw fail(TTradeControlError.resultUncertain)
    } catch TTradeControlError.resultUncertain {
      await refreshTruth()
      throw fail(TTradeControlError.resultUncertain)
    } catch {
      throw fail(error)
    }
    await refreshTruth()
  }

  func pauseEntries(reason: String = "iOS manual pause") async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    if let unavailable = pauseUnavailableReason {
      throw fail(TTradeControlError.unavailable(unavailable))
    }
    let normalizedReason = try normalizedUserReason(reason, required: true)
    let originalContextID = sessionContextID
    var context = try repositoryContext(requiredScopes: ["t-trade:control"])
    guard let repository = binding?.repository else {
      throw fail(TTradeControlError.unavailable("做 T 安全控制服务尚未连接"))
    }

    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    defer { operationInProgress = false }
    do {
      let result = try await repository.pauseEntries(
        accountID: context.activeAccountID,
        reason: normalizedReason,
        context: context
      )
      successMessage = result.message
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(
          expectedContextID: originalContextID,
          requiredScopes: ["t-trade:control"]
        )
        context = try repositoryContext(
          requiredScopes: ["t-trade:control"]
        )
        guard let repository = binding?.repository else {
          throw TTradeControlError.unavailable("做 T 安全控制服务尚未连接")
        }
        let result = try await repository.pauseEntries(
          accountID: context.activeAccountID,
          reason: normalizedReason,
          context: context
        )
        successMessage = result.message
      } catch ReadOnlyRepositoryError.transport {
        await refreshTruth()
        throw fail(TTradeControlError.resultUncertain)
      } catch {
        throw fail(error)
      }
    } catch ReadOnlyRepositoryError.transport {
      await refreshTruth()
      throw fail(TTradeControlError.resultUncertain)
    } catch {
      throw fail(error)
    }
    await refreshTruth()
  }

  private func previewRequest(
    action: TTradeSafetyAction,
    reason: String,
    idempotencyKey: UUID,
    snapshot: TTradeControlSnapshot
  ) throws -> TTradeControlPreviewRequest {
    let normalizedReason =
      action == .killSwitch
      ? try normalizedUserReason(reason, required: true)
      : action.serverReason
    return TTradeControlPreviewRequest(
      accountID: snapshot.accountID,
      action: action,
      policyVersion: snapshot.policyVersion,
      snapshotID: snapshot.snapshotID ?? "",
      targetStage: action.targetStage,
      reason: normalizedReason,
      idempotencyKey: idempotencyKey,
      expectedStage: snapshot.stage,
      expectedReadinessStatus: action == .killSwitch
        ? "RISK_REDUCTION_READY"
        : snapshot.status,
      expectedChecks: action == .killSwitch ? [] : snapshot.checks,
      snapshotBinding: snapshot.snapshotBinding
    )
  }

  private func performLoad(accountID: String) async throws -> TTradeControlSnapshot {
    guard let repository = binding?.repository else {
      throw TTradeControlError.unavailable("做 T 安全控制服务尚未连接")
    }
    let context = try repositoryContext(requiredScopes: ["strategy:read"])
    return try await repository.loadControlState(accountID: accountID, context: context)
  }

  private func apply(
    _ snapshot: TTradeControlSnapshot,
    requestID: UUID,
    contextID: UUID
  ) {
    guard
      stateRequestID == requestID,
      sessionContextID == contextID,
      snapshot.accountID == binding?.identity.activeAccountID
    else { return }
    state = .loaded(snapshot, refreshWarning: nil)
  }

  private func applyLoadFailure(
    _ error: Error,
    previous: TTradeControlSnapshot?,
    requestID: UUID,
    contextID: UUID
  ) {
    guard stateRequestID == requestID, sessionContextID == contextID else { return }
    if error is CancellationError {
      state = previous.map { .loaded($0, refreshWarning: nil) } ?? .idle
      return
    }
    let message = errorText(error, fallback: "做 T 安全状态暂时无法读取")
    state =
      previous.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次服务端状态。\(message)")
      } ?? .failed(message)
  }

  private func repositoryContext(
    requiredScopes: Set<String>
  ) throws -> TTradeControlRepositoryContext {
    if let unavailable = baseUnavailableReason(
      requiredScopes: requiredScopes,
      requiresBiometrics: false
    ) {
      throw TTradeControlError.unavailable(unavailable)
    }
    guard let identity = binding?.identity,
      let accountID = identity.activeAccountID
    else {
      throw TTradeControlError.contextChanged
    }
    return TTradeControlRepositoryContext(
      userID: identity.userID,
      deviceSessionID: identity.deviceSessionID,
      activeAccountID: accountID,
      authorizedAccountIDs: identity.authorizedAccountIDs,
      sessionContextID: sessionContextID
    )
  }

  private func validateCurrentContext(
    _ preview: TTradeControlPreviewTicket,
    expectedContextID: UUID
  ) throws {
    let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
    guard
      pendingControl == nil || pendingControl == preview,
      sessionContextID == expectedContextID,
      preview.sessionContextID == context.sessionContextID,
      preview.userID == context.userID,
      preview.deviceSessionID == context.deviceSessionID,
      preview.accountID == context.activeAccountID,
      preview.action.targetStage == preview.targetStage,
      preview.action != .killSwitch || !preview.reason.isEmpty
    else {
      throw TTradeControlError.contextChanged
    }
    // KILL_SWITCH is deliberately independent of ordinary readiness. Backend
    // allows policy, snapshot, Agent and rollout state to move after preview.
    if preview.action != .killSwitch {
      guard state.snapshot?.snapshotBinding == preview.snapshotBinding else {
        throw TTradeControlError.contextChanged
      }
    }
  }

  private func baseUnavailableReason(
    requiredScopes: Set<String>,
    requiresBiometrics: Bool
  ) -> String? {
    guard let binding else { return "请先恢复个人账户会话" }
    guard let contextProvider else { return "做 T 安全控制上下文尚未连接" }
    let runtime = contextProvider()
    guard runtime.accountDataEnabled else { return "账户能力尚未启用" }
    guard !runtime.localSessionLocked else { return "请先解锁个人量化会话" }
    guard requiredScopes.isSubset(of: binding.identity.grantedScopes) else {
      let missing = requiredScopes.subtracting(binding.identity.grantedScopes).sorted()
      return "当前会话缺少 \(missing.joined(separator: "、")) 权限"
    }
    guard let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == [accountID]
    else {
      return "当前会话没有唯一主账户，做 T 控制保持只读"
    }
    guard runtime.accountID == accountID else {
      return runtime.accountID == nil
        ? "主账户正在安全同步"
        : "做 T 账户与当前主账户不一致，操作已停止"
    }
    guard binding.repository != nil else { return "做 T 安全控制服务尚未连接" }
    if requiresBiometrics, !localAuthentication.tradeAuthorizationAvailable {
      return "做 T 安全控制要求此设备已启用 Face ID 或 Touch ID"
    }
    return nil
  }

  private func refreshAndValidateSession(
    expectedContextID: UUID,
    requiredScopes: Set<String>
  ) async throws {
    guard let refreshSession else { throw ReadOnlyRepositoryError.unauthenticated }
    try await refreshSession()
    guard sessionContextID == expectedContextID,
      baseUnavailableReason(
        requiredScopes: requiredScopes,
        requiresBiometrics: false
      ) == nil
    else {
      throw TTradeControlError.contextChanged
    }
  }

  private func refreshTruth() async {
    await refresh()
    await refreshAssistantProjection?()
  }

  private func normalizedUserReason(_ value: String, required: Bool) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      !required || !normalized.isEmpty,
      normalized.count <= 512,
      !normalized.unicodeScalars.contains(where: {
        $0.value < 32 || $0.value == 127
      })
    else {
      throw fail(TTradeControlError.invalidRequest("请输入 1–512 个有效字符的处置原因"))
    }
    return normalized
  }

  @discardableResult
  private func fail(_ error: Error) -> Error {
    if error is CancellationError {
      errorMessage = nil
      return error
    }
    errorMessage = errorText(error, fallback: "做 T 控制未完成")
    successMessage = nil
    return error
  }

  private func errorText(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }

  private func resetTransientState(resetReadState: Bool) {
    stateRequestID = UUID()
    legacyOperationGeneration = UUID()
    legacyScope = nil
    legacyPreparationID = nil
    legacyChallengeID = nil
    legacyInventory = nil
    legacyTicket = nil
    legacyCommandID = nil
    legacyStatus = nil
    legacyContext = nil
    legacyConfirmationAttempted = false
    accountControlTicket = nil
    recentReleaseOperations = []
    releaseTicket = nil
    releaseReference = nil
    releaseConfirmationAttempted = false
    releaseStatus = nil
    releaseCommandID = nil
    if resetReadState { state = .idle }
    refreshInProgress = false
    operationInProgress = false
    requestedAction = nil
    pendingControl = nil
    successMessage = nil
    errorMessage = nil
  }
}


extension TTradeControlStore {
  func loadReleaseOperations() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    operationInProgress = true
    defer { operationInProgress = false }
    do {
      let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
      guard let repository = binding?.releaseRepository else { throw TTradeControlError.contextChanged }
      let operations = try await repository.recentOperations(context: context)
      guard context == (try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])) else {
        throw TTradeControlError.contextChanged
      }
      recentReleaseOperations = operations
      errorMessage = nil
    } catch { throw fail(error) }
  }

  func recoverReleaseOperation(_ operation: TAssistantReleaseOperation) async throws {
    guard !operationInProgress, recentReleaseOperations.contains(operation) else {
      throw fail(TTradeControlError.contextChanged)
    }
    let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
    try operation.reference.validate(context: context)
    releaseTicket = nil
    releaseReference = operation.reference
    releaseCommandID = nil
    releaseStatus = nil
    releaseConfirmationAttempted = true
    try await refreshReleaseStatus()
  }

  func previewRelease(_ draft: TAssistantReleaseDraft) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard !releaseConfirmationAttempted
      || [.expired, .failed, .succeeded].contains(releaseStatus?.phase) else {
      throw fail(TTradeControlError.unavailable("请先查询原发布操作的处理结果"))
    }
    let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
    guard let repository = binding?.releaseRepository else {
      throw fail(TTradeControlError.unavailable("发布服务尚未连接"))
    }
    operationInProgress = true
    accountControlTicket = nil
    recentReleaseOperations = []
    releaseTicket = nil
    releaseReference = nil
    releaseConfirmationAttempted = false
    releaseStatus = nil
    releaseCommandID = nil
    clearMessages()
    defer { operationInProgress = false }
    do {
      let ticket = try await repository.preview(draft, context: context)
      try ticket.validate(context: repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"]))
      releaseTicket = ticket
      releaseReference = TAssistantReleaseReference(ticket: ticket)
    } catch { throw fail(error) }
  }

  func confirmRelease() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard let ticket = releaseTicket, releaseCommandID == nil else {
      throw fail(TTradeControlError.contextChanged)
    }
    operationInProgress = true
    clearMessages()
    defer { operationInProgress = false }
    do {
      let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
      try ticket.validate(context: context)
      guard ticket.expiresAt > Date() else { throw TTradeControlError.challengeExpired }
      try await localAuthentication.authorizeTrade(reason:
        "确认发布做 T 灰度：账户 \(TTradeControlPrivacy.maskedAccount(ticket.draft.accountID))")
      let current = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
      try ticket.validate(context: current)
      guard releaseTicket == ticket, ticket.expiresAt > Date(),
        let repository = binding?.releaseRepository else { throw TTradeControlError.contextChanged }
      releaseConfirmationAttempted = true
      let commandID = try await repository.confirm(ticket, context: current)
      try ticket.validate(context: repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"]))
      releaseCommandID = commandID
      successMessage = "发布命令已入队，请查询处理状态"
    } catch { throw fail(error) }
  }

  func refreshReleaseStatus() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard let reference = releaseReference, let repository = binding?.releaseRepository else {
      throw fail(TTradeControlError.contextChanged)
    }
    operationInProgress = true
    defer { operationInProgress = false }
    do {
      let context = try repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"])
      try reference.validate(context: context)
      let status = try await repository.status(reference, context: context)
      try reference.validate(context: repositoryContext(requiredScopes: ["t-trade:control", "trade:approve"]))
      guard context.sessionContextID == sessionContextID, releaseReference == reference,
        releaseCommandID == nil || releaseCommandID == status.commandID else {
        throw TTradeControlError.contextChanged
      }
      releaseStatus = status
      releaseCommandID = status.commandID
      errorMessage = nil
      successMessage = status.phase == .succeeded
        ? "发布命令已完成，执行状态：\(status.executionStatus ?? "")" : nil
    } catch { throw fail(error) }
  }
}


extension TTradeControlStore {
  var accountControlUnavailableReason: String? {
    baseUnavailableReason(requiredScopes: ["strategy:read", "account-execution:control", "trade:approve"],
      requiresBiometrics: true) ?? (binding?.accountRepository == nil ? "账户控制服务尚未连接" : nil)
  }

  func dismissAccountControl() {
    if !operationInProgress { accountControlTicket = nil }
  }

  func previewAccountControl(_ action: NativeAccountControlAction, reason: String) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    if let unavailable = accountControlUnavailableReason {
      throw fail(TTradeControlError.unavailable(unavailable))
    }
    operationInProgress = true
    accountControlTicket = nil
    clearMessages()
    defer { operationInProgress = false }
    do {
      let context = try repositoryContext(requiredScopes: ["strategy:read", "account-execution:control", "trade:approve"])
      guard let repository = binding?.accountRepository else { throw TTradeControlError.contextChanged }
      let ticket = try await repository.preview(action: action, reason: reason, context: context)
      guard context == (try repositoryContext(requiredScopes: ["strategy:read", "account-execution:control", "trade:approve"])),
        ticket.context == context else { throw TTradeControlError.contextChanged }
      accountControlTicket = ticket
    } catch { throw fail(error) }
  }

  func confirmAccountControl(_ ticket: NativeAccountControlTicket) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    operationInProgress = true
    clearMessages()
    defer { operationInProgress = false }
    do {
      let context = try repositoryContext(requiredScopes: ["account-execution:control", "trade:approve"])
      guard accountControlTicket == ticket, ticket.context == context else { throw TTradeControlError.contextChanged }
      guard ticket.expiresAt > Date() else { throw TTradeControlError.challengeExpired }
      try await localAuthentication.authorizeTrade(reason: ticket.action.title)
      let current = try repositoryContext(requiredScopes: ["account-execution:control", "trade:approve"])
      guard current == context, accountControlTicket == ticket, ticket.expiresAt > Date(),
        let repository = binding?.accountRepository else { throw TTradeControlError.contextChanged }
      let message = try await repository.confirm(ticket, context: current)
      guard current == (try repositoryContext(requiredScopes: ["account-execution:control", "trade:approve"])) else {
        throw TTradeControlError.contextChanged
      }
      accountControlTicket = nil
      successMessage = message
      await refreshTruth()
    } catch { throw fail(error) }
  }
}

extension TTradeControlStore {
  private static let legacyScopes: Set<String> = ["t-trade:control", "trade:approve"]

  func discardLegacyReview() throws {
    guard !operationInProgress, !legacyConfirmationAttempted else {
      throw fail(TTradeControlError.unavailable("请先恢复原确认结果，不能重新准备排空"))
    }
    legacyScope = nil
    legacyPreparationID = nil
    legacyChallengeID = nil
    legacyInventory = nil
    legacyTicket = nil
    legacyStatus = nil
    legacyContext = nil
    clearMessages()
  }

  private func legacyCurrentContext() throws -> TTradeControlRepositoryContext {
    let current = try repositoryContext(requiredScopes: Self.legacyScopes)
    if let original = legacyContext {
      guard original.userID == current.userID, original.deviceSessionID == current.deviceSessionID,
        original.activeAccountID == current.activeAccountID,
        original.authorizedAccountIDs == current.authorizedAccountIDs else {
        throw TTradeControlError.contextChanged
      }
    }
    return current
  }

  func prepareLegacyInventory(_ scope: TAssistantLegacyScope) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard !legacyConfirmationAttempted, legacyScope == nil || legacyScope == scope,
      let repository = binding?.legacyRepository else { throw fail(TTradeControlError.contextChanged) }
    let operationGeneration = legacyOperationGeneration
    operationInProgress = true
    defer {
      if legacyOperationGeneration == operationGeneration { operationInProgress = false }
    }
    do {
      let current = try legacyCurrentContext()
      try scope.validate(current)
      let identity = legacyPreparationID ?? UUID()
      legacyScope = scope
      legacyPreparationID = identity
      legacyContext = current
      legacyTicket = nil
      let returned = try await repository.prepare(scope, requestID: identity, context: current)
      guard current == (try legacyCurrentContext()), legacyPreparationID == identity,
        returned == identity.uuidString.lowercased() else { throw TTradeControlError.contextChanged }
      errorMessage = nil
      successMessage = nil
    } catch { throw fail(error) }
  }

  func refreshLegacyStatus() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard !legacyConfirmationAttempted || legacyCommandID != nil else {
      throw fail(TTradeControlError.unavailable("原确认投递结果未明确，请重试原确认；锁定后需恢复原确认操作"))
    }
    guard let repository = binding?.legacyRepository, let scope = legacyScope,
      let commandID = legacyCommandID ?? legacyPreparationID?.uuidString.lowercased() else {
      throw fail(TTradeControlError.contextChanged)
    }
    let operationGeneration = legacyOperationGeneration
    operationInProgress = true
    defer {
      if legacyOperationGeneration == operationGeneration { operationInProgress = false }
    }
    do {
      let current = try legacyCurrentContext()
      let status = try await repository.operation(commandID, context: current)
      guard current == (try legacyCurrentContext()), status.commandID == commandID,
        commandID == (legacyCommandID ?? legacyPreparationID?.uuidString.lowercased()) else {
        throw TTradeControlError.contextChanged
      }
      if status.status == .succeeded {
        guard let evidence = status.evidence else { throw TTradeControlError.invalidResponse }
        if legacyCommandID == nil {
          legacyInventory = try .validated(evidence, scope: scope, commandID: commandID)
        } else {
          guard let inventory = legacyInventory else { throw TTradeControlError.contextChanged }
          let details = try TAssistantLegacyInventory.object(evidence)
          var expected = scope.fields
          expected.removeValue(forKey: "account_id")
          expected["inventory_operation_id"] = .string(inventory.operationID)
          expected["inventory_hash"] = .string(inventory.hash)
          guard details["run_id"] == .string(scope.runID), details["request"] == GraphQLJSON(object: expected) else {
            throw TTradeControlError.invalidResponse
          }
          successMessage = "已停止旧执行的新买入，原订单和退出义务继续处理"
        }
      }
      legacyStatus = status
      errorMessage = nil
    } catch { throw fail(error) }
  }

  func previewLegacyDrain(windowStart: Date, windowEnd: Date) async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard !legacyConfirmationAttempted, let inventory = legacyInventory,
      let repository = binding?.legacyRepository else { throw fail(TTradeControlError.contextChanged) }
    let operationGeneration = legacyOperationGeneration
    operationInProgress = true
    legacyTicket = nil
    clearMessages()
    defer {
      if legacyOperationGeneration == operationGeneration { operationInProgress = false }
    }
    do {
      let current = try legacyCurrentContext()
      let ticket = try await repository.preview(inventory, windowStart: windowStart, windowEnd: windowEnd, context: current)
      try ticket.validate(legacyCurrentContext())
      guard current == ticket.context, ticket.inventory == inventory else { throw TTradeControlError.contextChanged }
      legacyTicket = ticket
      legacyChallengeID = ticket.challengeID
    } catch { throw fail(error) }
  }

  func confirmLegacyDrain() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard let ticket = legacyTicket, legacyCommandID == nil,
      let repository = binding?.legacyRepository else { throw fail(TTradeControlError.contextChanged) }
    let operationGeneration = legacyOperationGeneration
    operationInProgress = true
    clearMessages()
    defer {
      if legacyOperationGeneration == operationGeneration { operationInProgress = false }
    }
    do {
      try ticket.validate(legacyCurrentContext())
      try await refreshAndValidateSession(expectedContextID: ticket.context.sessionContextID, requiredScopes: Self.legacyScopes)
      if !legacyConfirmationAttempted {
        guard ticket.expiresAt > legacyNow(), ticket.windowStart <= legacyNow(), legacyNow() < ticket.windowEnd else {
          throw TTradeControlError.challengeExpired
        }
      }
      try await localAuthentication.authorizeTrade(reason: "确认停止旧做 T 新买入：账户 \(TTradeControlPrivacy.maskedAccount(ticket.inventory.scope.accountID))")
      let current = try legacyCurrentContext()
      try ticket.validate(current)
      guard legacyTicket == ticket, legacyConfirmationAttempted || (ticket.expiresAt > legacyNow() && ticket.windowStart <= legacyNow() && legacyNow() < ticket.windowEnd) else {
        throw TTradeControlError.contextChanged
      }
      legacyConfirmationAttempted = true
      let identity = try await repository.confirm(ticket, context: current)
      try ticket.validate(legacyCurrentContext())
      guard legacyTicket == ticket, UUID(uuidString: identity) != nil else { throw TTradeControlError.contextChanged }
      legacyCommandID = identity
      legacyTicket = nil
      legacyStatus = nil
    } catch { throw fail(error) }
  }
}

extension TTradeControlStore {
  func recoverLegacyConfirmation() async throws {
    guard !operationInProgress else { throw fail(TTradeControlError.alreadyInProgress) }
    guard let identity = legacyChallengeID, let inventory = legacyInventory,
      let repository = binding?.legacyRepository else { throw fail(TTradeControlError.contextChanged) }
    let generation = legacyOperationGeneration
    operationInProgress = true
    clearMessages()
    defer { if generation == legacyOperationGeneration { operationInProgress = false } }
    do {
      let current = try legacyCurrentContext()
      let recovered = try await repository.recover(challengeID: identity, inventory: inventory, context: current)
      guard current == (try legacyCurrentContext()), legacyChallengeID == identity, legacyInventory == inventory else {
        throw TTradeControlError.contextChanged
      }
      switch recovered.phase {
      case .awaitingConfirmation:
        guard recovered.commandID == nil, legacyCommandID == nil else { throw TTradeControlError.invalidResponse }
        errorMessage = "原确认尚未消费；投递结果不明确时请等待过期后再次核对"
      case .expired:
        guard recovered.commandID == nil, legacyCommandID == nil else { throw TTradeControlError.invalidResponse }
        legacyConfirmationAttempted = false
        legacyTicket = nil
        legacyChallengeID = nil
        legacyStatus = nil
        errorMessage = "原确认未消费且已过期，可以重新复核"
      default:
        guard let commandID = recovered.commandID, UUID(uuidString: commandID) != nil,
          legacyCommandID == nil || legacyCommandID == commandID else { throw TTradeControlError.invalidResponse }
        legacyCommandID = commandID
        legacyConfirmationAttempted = true
        legacyTicket = nil
        legacyStatus = nil
      }
    } catch { throw fail(error) }
  }
}
