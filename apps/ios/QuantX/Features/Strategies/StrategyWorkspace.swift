import Foundation

struct StrategyWorkspaceRuntimeContext {
  let accountID: String?
  let localSessionLocked: Bool
  let accountDataEnabled: Bool
}

@MainActor
final class StrategyWorkspace: ObservableObject {
  struct SessionIdentity: Equatable {
    let userID: String
    let deviceSessionID: String
    let activeAccountID: String?
    let authorizedAccountIDs: Set<String>
    let grantedScopes: Set<String>
  }

  private struct SessionBinding {
    let identity: SessionIdentity
    let repository: (any StrategyWorkspaceLoading)?
  }

  private struct ConflictDraft {
    let instanceID: String
    let staleVersion: String
    var values: [String: StrategyMobileParameterValue]
  }

  typealias ContextProvider = @MainActor () -> StrategyWorkspaceRuntimeContext
  typealias RefreshSession = @MainActor () async throws -> Void
  typealias RefreshStrategies = @MainActor () async -> Void

  @Published private(set) var parameterState: StrategyParameterEditorState = .idle
  @Published private(set) var draftValues: [String: StrategyMobileParameterValue] = [:]
  @Published private(set) var parameterConflict: StrategyParameterConflict?
  @Published private(set) var pendingControl: StrategyControlPreviewTicket?
  @Published private(set) var operationInProgress = false
  @Published private(set) var successMessage: String?
  @Published private(set) var errorMessage: String?

  private let localAuthentication: any LocalAuthenticationProviding
  private var binding: SessionBinding?
  private var contextProvider: ContextProvider?
  private var refreshSession: RefreshSession?
  private var refreshStrategies: RefreshStrategies?
  private var sessionContextID = UUID()
  private var selectedInstanceID: String?
  private var parameterRequestID = UUID()
  private var conflictDraft: ConflictDraft?

  init(localAuthentication: any LocalAuthenticationProviding) {
    self.localAuthentication = localAuthentication
  }

  func configure(
    contextProvider: @escaping ContextProvider,
    refreshSession: @escaping RefreshSession,
    refreshStrategies: @escaping RefreshStrategies
  ) {
    self.contextProvider = contextProvider
    self.refreshSession = refreshSession
    self.refreshStrategies = refreshStrategies
  }

  func activate(
    identity: SessionIdentity,
    repository: (any StrategyWorkspaceLoading)?
  ) {
    if binding?.identity != identity {
      sessionContextID = UUID()
      resetTransientState()
    }
    binding = SessionBinding(identity: identity, repository: repository)
  }

  func clearSession() {
    binding = nil
    sessionContextID = UUID()
    selectedInstanceID = nil
    resetTransientState()
  }

  func invalidateControlContext() {
    sessionContextID = UUID()
    pendingControl = nil
  }

  func select(_ instance: StrategyMonitorItem) async {
    if selectedInstanceID != instance.id {
      selectedInstanceID = instance.id
      clearParameterConflict()
      pendingControl = nil
      successMessage = nil
      errorMessage = nil
    }
    await loadParameters(instanceID: instance.id)
  }

  func clearSelection(instanceID: String) {
    guard selectedInstanceID == instanceID else { return }
    selectedInstanceID = nil
    parameterRequestID = UUID()
    parameterState = .idle
    draftValues = [:]
    clearParameterConflict()
    pendingControl = nil
    successMessage = nil
    errorMessage = nil
  }

  func retryParameters() async {
    guard let selectedInstanceID else { return }
    await loadParameters(instanceID: selectedInstanceID)
  }

  func setDraftValue(
    _ value: StrategyMobileParameterValue,
    for parameter: StrategyMobileParameter
  ) {
    guard
      parameterState.snapshot?.parameters.contains(where: { $0.key == parameter.key }) == true,
      Self.valueTypeMatches(value, parameter: parameter)
    else { return }
    draftValues[parameter.key] = value
    if var conflictDraft,
      conflictDraft.instanceID == parameterState.snapshot?.instanceID
    {
      conflictDraft.values[parameter.key] = value
      self.conflictDraft = conflictDraft
      if let snapshot = parameterState.snapshot {
        parameterConflict = StrategyParameterConflict(
          staleVersion: conflictDraft.staleVersion,
          serverSnapshot: snapshot,
          userValues: conflictDraft.values
        )
      }
    }
    errorMessage = nil
  }

  func discardParameterChanges() {
    if parameterConflict != nil {
      adoptServerValuesAfterConflict()
      return
    }
    guard let snapshot = parameterState.snapshot else { return }
    draftValues = snapshot.values
    errorMessage = nil
  }

  func adoptServerValuesAfterConflict() {
    guard let snapshot = parameterConflict?.serverSnapshot else { return }
    draftValues = snapshot.values
    clearParameterConflict()
    errorMessage = nil
  }

  func resubmitParametersAfterConflict(
    for instance: StrategyMonitorItem
  ) async throws {
    guard
      let conflict = parameterConflict,
      conflict.serverSnapshot.instanceID == instance.id,
      conflict.serverSnapshot == parameterState.snapshot,
      conflict.canResubmit
    else {
      throw fail(
        StrategyWorkspaceError.invalidRequest(
          "当前差异无法安全重放；请采用服务端值后再重新编辑"
        )
      )
    }
    try await saveParameters(for: instance)
  }

  var hasUnsavedParameterChanges: Bool {
    guard let snapshot = parameterState.snapshot else { return false }
    return snapshot.values != draftValues
  }

  func isDraftValid(for parameter: StrategyMobileParameter) -> Bool {
    guard let value = draftValues[parameter.key] else { return false }
    return parameter.validates(value)
  }

  func parameterEditingUnavailableReason(
    for instance: StrategyMonitorItem
  ) -> String? {
    if let reason = baseUnavailableReason(requiredScopes: ["strategy:read", "strategy:control"]) {
      return reason
    }
    guard selectedInstanceID == instance.id,
      let snapshot = parameterState.snapshot,
      snapshot.instanceID == instance.id
    else {
      return "移动参数正在从服务端同步"
    }
    guard instance.mode != "LIVE" else {
      return "当前公开契约不允许直接修改 LIVE 参数；请在模拟盘验证后再克隆为实盘"
    }
    guard snapshot.editable else {
      return "服务端已将此实例的移动参数设为只读"
    }
    return nil
  }

  func lifecycleUnavailableReason(
    for control: StrategyLifecycleControl,
    instance: StrategyMonitorItem
  ) -> String? {
    var required = Set(["strategy:read", "strategy:control"])
    if control.requiresLiveConfirmation {
      required.insert("trade:approve")
    }
    if let reason = baseUnavailableReason(requiredScopes: required) {
      return reason
    }
    guard instance.lifecycleControls.contains(control) else {
      return "服务端当前策略模式或状态不允许该操作"
    }
    if control.requiresLiveConfirmation {
      guard parameterState.snapshot?.instanceID == instance.id else {
        return "请等待配置版本同步后再预览实盘控制"
      }
      guard localAuthentication.tradeAuthorizationAvailable else {
        return "实盘策略控制要求此设备已启用 Face ID 或 Touch ID"
      }
    }
    return nil
  }

  func saveParameters(for instance: StrategyMonitorItem) async throws {
    guard !operationInProgress else { throw StrategyWorkspaceError.alreadyInProgress }
    if let reason = parameterEditingUnavailableReason(for: instance) {
      throw fail(StrategyWorkspaceError.unavailable(reason))
    }
    guard let snapshot = parameterState.snapshot else {
      throw fail(StrategyWorkspaceError.unavailable("移动参数尚未完成同步"))
    }
    let changed = try changedValues(snapshot: snapshot)
    guard !changed.isEmpty else {
      throw fail(StrategyWorkspaceError.invalidRequest("没有需要保存的参数变更"))
    }
    guard let repository = binding?.repository else {
      throw fail(StrategyWorkspaceError.unavailable("策略控制服务尚未连接"))
    }
    let changedKeys = Set(changed.keys)
    let applyImmediately = snapshot.parameters
      .filter { changedKeys.contains($0.key) }
      .allSatisfy(\.applyImmediately)

    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    defer { operationInProgress = false }
    do {
      try await repository.updateMobileParameters(
        instanceID: instance.id,
        values: changed,
        expectedVersion: snapshot.configVersion,
        applyImmediately: applyImmediately
      )
      clearParameterConflict()
      successMessage =
        applyImmediately
        ? "服务端已确认参数并立即应用"
        : "服务端已确认参数；运行中实例会按后端规则安全暂存"
      await refreshStrategies?()
      await loadParameters(instanceID: instance.id)
    } catch StrategyWorkspaceError.versionConflict {
      await reloadAfterConflict(
        instanceID: instance.id,
        staleVersion: snapshot.configVersion,
        userValues: draftValues
      )
      throw fail(StrategyWorkspaceError.versionConflict)
    } catch ReadOnlyRepositoryError.unauthenticated {
      await refreshAfterAuthenticationFailure(instanceID: instance.id)
      throw fail(
        StrategyWorkspaceError.unavailable("会话已刷新，请基于服务端最新参数重新保存")
      )
    } catch {
      throw fail(error)
    }
  }

  func performDirectControl(
    _ control: StrategyLifecycleControl,
    instance: StrategyMonitorItem
  ) async throws {
    guard !operationInProgress else { throw StrategyWorkspaceError.alreadyInProgress }
    guard control == .pause || control == .resumePaper else {
      throw fail(StrategyWorkspaceError.invalidRequest("该操作必须先预览并进行本机生物确认"))
    }
    if let reason = lifecycleUnavailableReason(for: control, instance: instance) {
      throw fail(StrategyWorkspaceError.unavailable(reason))
    }
    guard let repository = binding?.repository else {
      throw fail(StrategyWorkspaceError.unavailable("策略控制服务尚未连接"))
    }
    let originalContextID = sessionContextID
    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    defer { operationInProgress = false }
    do {
      successMessage = try await performDirect(
        control,
        instanceID: instance.id,
        repository: repository
      )
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        guard let repository = binding?.repository else {
          throw StrategyWorkspaceError.unavailable("策略控制服务尚未连接")
        }
        successMessage = try await performDirect(
          control,
          instanceID: instance.id,
          repository: repository
        )
      } catch {
        throw fail(error)
      }
    } catch {
      throw fail(error)
    }
    await refreshStrategies?()
    await loadParameters(instanceID: instance.id)
  }

  func previewLiveControl(
    _ control: StrategyLifecycleControl,
    instance: StrategyMonitorItem,
    idempotencyKey: UUID = UUID()
  ) async throws {
    guard !operationInProgress else { throw StrategyWorkspaceError.alreadyInProgress }
    guard let action = Self.liveAction(for: control) else {
      throw fail(StrategyWorkspaceError.invalidRequest("该操作不需要实盘控制预览"))
    }
    if let reason = lifecycleUnavailableReason(for: control, instance: instance) {
      throw fail(StrategyWorkspaceError.unavailable(reason))
    }
    guard
      let snapshot = parameterState.snapshot,
      snapshot.instanceID == instance.id,
      let repository = binding?.repository
    else {
      throw fail(
        StrategyWorkspaceError.unavailable("策略配置版本或控制服务尚未就绪")
      )
    }
    let originalContextID = sessionContextID
    var context = try repositoryContext()
    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    pendingControl = nil
    defer { operationInProgress = false }
    do {
      pendingControl = try await repository.previewLiveControl(
        action: action,
        instanceID: instance.id,
        expectedConfigVersion: snapshot.configVersion,
        idempotencyKey: idempotencyKey,
        context: context
      )
    } catch StrategyWorkspaceError.versionConflict {
      await reloadAfterConflict(
        instanceID: instance.id,
        staleVersion: snapshot.configVersion,
        userValues: draftValues
      )
      throw fail(StrategyWorkspaceError.versionConflict)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        context = try repositoryContext()
        guard
          parameterState.snapshot?.configVersion == snapshot.configVersion,
          let repository = binding?.repository
        else {
          throw StrategyWorkspaceError.contextChanged
        }
        pendingControl = try await repository.previewLiveControl(
          action: action,
          instanceID: instance.id,
          expectedConfigVersion: snapshot.configVersion,
          idempotencyKey: idempotencyKey,
          context: context
        )
      } catch {
        throw fail(error)
      }
    } catch {
      throw fail(error)
    }
  }

  func dismissPendingControl() {
    guard !operationInProgress else { return }
    pendingControl = nil
  }

  func confirmLiveControl(_ preview: StrategyControlPreviewTicket) async throws {
    guard !operationInProgress else { throw StrategyWorkspaceError.alreadyInProgress }
    guard pendingControl == preview else {
      throw fail(StrategyWorkspaceError.contextChanged)
    }
    try validateCurrentContext(preview)
    guard !preview.isExpired() else {
      pendingControl = nil
      throw fail(StrategyWorkspaceError.challengeExpired)
    }
    guard let repository = binding?.repository else {
      throw fail(StrategyWorkspaceError.unavailable("策略控制服务尚未连接"))
    }
    let originalContextID = sessionContextID
    var context = try repositoryContext()
    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    defer {
      operationInProgress = false
      pendingControl = nil
    }
    let biometricReason = "确认\(preview.action.title)：\(preview.instanceID)"
    do {
      try await localAuthentication.authorizeTrade(reason: biometricReason)
      try validateCurrentContext(preview)
      guard !preview.isExpired() else { throw StrategyWorkspaceError.challengeExpired }
      let confirmation = try await repository.confirmLiveControl(
        preview,
        context: context
      )
      successMessage = confirmation.message
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        context = try repositoryContext()
        try validateCurrentContext(preview)
        guard !preview.isExpired() else { throw StrategyWorkspaceError.challengeExpired }
        try await localAuthentication.authorizeTrade(reason: biometricReason)
        try validateCurrentContext(preview)
        guard !preview.isExpired() else { throw StrategyWorkspaceError.challengeExpired }
        guard let repository = binding?.repository else {
          throw StrategyWorkspaceError.unavailable("策略控制服务尚未连接")
        }
        let confirmation = try await repository.confirmLiveControl(
          preview,
          context: context
        )
        successMessage = confirmation.message
      } catch ReadOnlyRepositoryError.transport {
        await refreshStrategies?()
        throw fail(StrategyWorkspaceError.resultUncertain)
      } catch {
        throw fail(error)
      }
    } catch ReadOnlyRepositoryError.transport {
      await refreshStrategies?()
      throw fail(StrategyWorkspaceError.resultUncertain)
    } catch {
      throw fail(error)
    }
    await refreshStrategies?()
    await loadParameters(instanceID: preview.targetInstanceID)
  }

  private func loadParameters(instanceID: String) async {
    guard selectedInstanceID == instanceID else { return }
    guard baseUnavailableReason(requiredScopes: ["strategy:read"]) == nil,
      let repository = binding?.repository
    else {
      parameterState = .failed(
        instanceID: instanceID,
        message: baseUnavailableReason(requiredScopes: ["strategy:read"])
          ?? "策略参数服务尚未连接"
      )
      if conflictDraft?.instanceID != instanceID {
        draftValues = [:]
      }
      return
    }
    let requestID = UUID()
    let originalContextID = sessionContextID
    parameterRequestID = requestID
    parameterState = .loading(instanceID: instanceID)
    do {
      let snapshot = try await repository.loadMobileParameters(instanceID: instanceID)
      apply(snapshot, requestID: requestID, contextID: originalContextID)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        guard let repository = binding?.repository else {
          throw StrategyWorkspaceError.unavailable("策略参数服务尚未连接")
        }
        let snapshot = try await repository.loadMobileParameters(instanceID: instanceID)
        apply(snapshot, requestID: requestID, contextID: originalContextID)
      } catch {
        applyLoadFailure(error, instanceID: instanceID, requestID: requestID)
      }
    } catch {
      applyLoadFailure(error, instanceID: instanceID, requestID: requestID)
    }
  }

  private func apply(
    _ snapshot: StrategyMobileParameterSnapshot,
    requestID: UUID,
    contextID: UUID
  ) {
    guard
      parameterRequestID == requestID,
      sessionContextID == contextID,
      selectedInstanceID == snapshot.instanceID
    else { return }
    parameterState = .loaded(snapshot)
    if let conflictDraft,
      conflictDraft.instanceID == snapshot.instanceID
    {
      let conflict = StrategyParameterConflict(
        staleVersion: conflictDraft.staleVersion,
        serverSnapshot: snapshot,
        userValues: conflictDraft.values
      )
      parameterConflict = conflict
      draftValues = conflict.rebasedDraftValues
    } else {
      clearParameterConflict()
      draftValues = snapshot.values
    }
  }

  private func applyLoadFailure(
    _ error: Error,
    instanceID: String,
    requestID: UUID
  ) {
    guard parameterRequestID == requestID, selectedInstanceID == instanceID else { return }
    if error is CancellationError {
      parameterState = .idle
      return
    }
    parameterState = .failed(
      instanceID: instanceID,
      message: errorText(error, fallback: "移动参数暂时无法读取")
    )
    if conflictDraft?.instanceID != instanceID {
      draftValues = [:]
    }
  }

  private func changedValues(
    snapshot: StrategyMobileParameterSnapshot
  ) throws -> [String: StrategyMobileParameterValue] {
    guard Set(draftValues.keys) == Set(snapshot.parameters.map(\.key)) else {
      throw StrategyWorkspaceError.invalidRequest("参数 allowlist 已变化，请重新加载")
    }
    var changed: [String: StrategyMobileParameterValue] = [:]
    for parameter in snapshot.parameters {
      guard let value = draftValues[parameter.key], parameter.validates(value) else {
        throw StrategyWorkspaceError.invalidRequest("“\(parameter.title)”不符合服务端约束")
      }
      if value != parameter.currentValue {
        changed[parameter.key] = value
      }
    }
    return changed
  }

  private func baseUnavailableReason(requiredScopes: Set<String>) -> String? {
    guard let binding else { return "请先恢复个人账户会话" }
    guard let contextProvider else { return "策略工作区尚未连接" }
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
      return "当前会话没有唯一主账户，策略控制保持只读"
    }
    guard runtime.accountID == accountID else {
      return runtime.accountID == nil
        ? "主账户正在安全同步"
        : "策略账户与当前会话不一致，操作已停止"
    }
    guard binding.repository != nil else { return "策略服务尚未连接" }
    return nil
  }

  private func repositoryContext() throws -> StrategyWorkspaceRepositoryContext {
    if let reason = baseUnavailableReason(requiredScopes: ["strategy:read"]) {
      throw StrategyWorkspaceError.unavailable(reason)
    }
    guard
      let identity = binding?.identity,
      let activeAccountID = identity.activeAccountID
    else {
      throw StrategyWorkspaceError.contextChanged
    }
    return StrategyWorkspaceRepositoryContext(
      userID: identity.userID,
      deviceSessionID: identity.deviceSessionID,
      activeAccountID: activeAccountID,
      authorizedAccountIDs: identity.authorizedAccountIDs,
      sessionContextID: sessionContextID
    )
  }

  private func validateCurrentContext(_ preview: StrategyControlPreviewTicket) throws {
    let context = try repositoryContext()
    guard
      pendingControl == preview,
      preview.sessionContextID == context.sessionContextID,
      preview.userID == context.userID,
      preview.deviceSessionID == context.deviceSessionID,
      preview.accountID == context.activeAccountID,
      selectedInstanceID == preview.instanceID,
      parameterState.snapshot?.instanceID == preview.instanceID,
      parameterState.snapshot?.configVersion == preview.configVersion
    else {
      throw StrategyWorkspaceError.contextChanged
    }
  }

  private func performDirect(
    _ control: StrategyLifecycleControl,
    instanceID: String,
    repository: any StrategyWorkspaceLoading
  ) async throws -> String {
    switch control {
    case .pause:
      try await repository.pause(instanceID: instanceID)
    case .resumePaper:
      try await repository.resumePaper(instanceID: instanceID)
    case .startLive, .resumeLive, .cloneToLive:
      throw StrategyWorkspaceError.invalidRequest("实盘控制必须使用两阶段确认")
    }
  }

  private static func liveAction(
    for control: StrategyLifecycleControl
  ) -> StrategyLiveControlAction? {
    switch control {
    case .startLive: .start
    case .resumeLive: .resume
    case .cloneToLive: .clone
    case .pause, .resumePaper: nil
    }
  }

  private static func valueTypeMatches(
    _ value: StrategyMobileParameterValue,
    parameter: StrategyMobileParameter
  ) -> Bool {
    switch (parameter.kind, value) {
    case (.boolean, .boolean), (.integer, .integer), (.string, .string):
      true
    case (.number, .number(let number)):
      number.isFinite
    default:
      false
    }
  }

  private func refreshAndValidateSession(expectedContextID: UUID) async throws {
    guard let refreshSession else { throw ReadOnlyRepositoryError.unauthenticated }
    try await refreshSession()
    guard sessionContextID == expectedContextID else {
      throw StrategyWorkspaceError.contextChanged
    }
  }

  private func refreshAfterAuthenticationFailure(instanceID: String) async {
    let contextID = sessionContextID
    try? await refreshAndValidateSession(expectedContextID: contextID)
    await refreshStrategies?()
    await loadParameters(instanceID: instanceID)
  }

  private func reloadAfterConflict(
    instanceID: String,
    staleVersion: String,
    userValues: [String: StrategyMobileParameterValue]
  ) async {
    pendingControl = nil
    conflictDraft = ConflictDraft(
      instanceID: instanceID,
      staleVersion: staleVersion,
      values: userValues
    )
    parameterConflict = nil
    await refreshStrategies?()
    await loadParameters(instanceID: instanceID)
  }

  private func clearParameterConflict() {
    conflictDraft = nil
    parameterConflict = nil
  }

  @discardableResult
  private func fail(_ error: Error) -> Error {
    if error is CancellationError {
      errorMessage = nil
      return error
    }
    errorMessage = errorText(error, fallback: "策略操作未完成")
    successMessage = nil
    return error
  }

  private func errorText(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }

  private func resetTransientState() {
    parameterRequestID = UUID()
    parameterState = .idle
    draftValues = [:]
    clearParameterConflict()
    pendingControl = nil
    operationInProgress = false
    successMessage = nil
    errorMessage = nil
  }
}
