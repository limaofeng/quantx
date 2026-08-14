import Foundation

struct ExitPlanRuntimeContext {
  let accountID: String?
  let localSessionLocked: Bool
  let accountDataEnabled: Bool
}

@MainActor
final class ExitPlanWorkspace: ObservableObject {
  struct SessionIdentity: Equatable {
    let userID: String
    let deviceSessionID: String
    let activeAccountID: String?
    let authorizedAccountIDs: Set<String>
    let grantedScopes: Set<String>
  }

  private struct SessionBinding {
    let identity: SessionIdentity
    let repository: (any ExitPlanLoading)?
  }

  typealias ContextProvider = @MainActor () -> ExitPlanRuntimeContext
  typealias RefreshSession = @MainActor () async throws -> Void
  typealias RefreshTradingTruth = @MainActor () async -> Void

  @Published private(set) var listState: ExitPlanListState = .idle
  @Published private(set) var detailState: ExitPlanDetailState = .idle
  @Published private(set) var pendingAuthorization: ExitPlanAuthorizationReview?
  @Published private(set) var operationInProgress = false
  @Published private(set) var successMessage: String?
  @Published private(set) var errorMessage: String?

  private let localAuthentication: any LocalAuthenticationProviding
  private var binding: SessionBinding?
  private var contextProvider: ContextProvider?
  private var refreshSession: RefreshSession?
  private var refreshTradingTruth: RefreshTradingTruth?
  private var sessionContextID = UUID()
  private var listRequestID = UUID()
  private var detailRequestID = UUID()
  private var selectedPlanID: String?
  private var pendingTicket: ExitPlanAuthorizationTicket?

  init(localAuthentication: any LocalAuthenticationProviding) {
    self.localAuthentication = localAuthentication
  }

  func configure(
    contextProvider: @escaping ContextProvider,
    refreshSession: @escaping RefreshSession,
    refreshTradingTruth: @escaping RefreshTradingTruth
  ) {
    self.contextProvider = contextProvider
    self.refreshSession = refreshSession
    self.refreshTradingTruth = refreshTradingTruth
  }

  func activate(
    identity: SessionIdentity,
    repository: (any ExitPlanLoading)?
  ) {
    if binding?.identity != identity {
      sessionContextID = UUID()
      resetForIdentityChange()
    }
    binding = SessionBinding(identity: identity, repository: repository)
  }

  func clearSession() {
    binding = nil
    sessionContextID = UUID()
    resetForIdentityChange()
  }

  func invalidateAuthorizationContext() {
    sessionContextID = UUID()
    clearPendingAuthorization()
  }

  var paperConfigurationMessage: String {
    "当前发布契约尚未为 iOS 提供专用 PAPER 计划创建/编辑/暂停字段；兼容 mutation:write 不会签发给原生会话，因此这里只读展示服务端计划。"
  }

  func readUnavailableReason() -> String? {
    guard let binding else { return "请先恢复个人账户会话" }
    guard binding.identity.grantedScopes.contains("orders:read") else {
      return "当前会话没有 orders:read 权限，退出计划保持不可见"
    }
    guard let contextProvider else { return "退出计划上下文尚未连接" }
    let runtime = contextProvider()
    guard runtime.accountDataEnabled else { return "账户能力尚未启用" }
    guard !runtime.localSessionLocked else { return "请先解锁个人量化会话" }
    guard let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == Set([accountID])
    else {
      return "当前会话没有唯一主账户，退出计划保持只读"
    }
    guard runtime.accountID == accountID else {
      return runtime.accountID == nil
        ? "主账户正在安全同步"
        : "退出计划账户与当前主账户不一致，已停止读取"
    }
    guard binding.repository != nil else { return "退出计划服务尚未连接" }
    return nil
  }

  func authorizationUnavailableReason(for plan: ExitPlanItem) -> String? {
    if let reason = readUnavailableReason() { return reason }
    guard let binding else { return "请先恢复个人账户会话" }
    guard binding.identity.grantedScopes.contains("liquidation:control") else {
      return "自动退出授权需要独立的 liquidation:control 权限"
    }
    guard binding.identity.grantedScopes.contains("trade:approve") else {
      return "自动退出授权还需要独立的 trade:approve 权限"
    }
    guard localAuthentication.tradeAuthorizationAvailable else {
      return "实盘自动退出授权要求此设备已启用 Face ID 或 Touch ID"
    }
    guard plan.accountID == binding.identity.activeAccountID else {
      return "退出计划不属于当前唯一主账户"
    }
    guard let current = currentPlan(id: plan.id), current.configVersion == plan.configVersion else {
      return "计划版本已变化，请刷新并重新进入详情"
    }
    guard plan.executionMode == .live else {
      return switch plan.executionMode {
      case .paper: "PAPER 计划不需要实盘自动授权"
      case .unknown: "服务端返回未知执行模式，已阻断授权"
      case .live: ""
      }
    }
    guard plan.status.isAuthorizable else { return "只有监控中的活动计划可以授权" }
    guard plan.remainingVolume > 0 else { return "计划已无剩余保护量" }
    return nil
  }

  func refresh() async {
    let previous = listState.snapshot
    guard let reason = readUnavailableReason() else {
      await loadPlans(previous: previous)
      return
    }
    listRequestID = UUID()
    listState = .unavailable(reason)
    detailState = .idle
    clearPendingAuthorization()
  }

  func select(_ plan: ExitPlanItem) async {
    if selectedPlanID != plan.id {
      selectedPlanID = plan.id
      detailRequestID = UUID()
      detailState = .idle
      clearPendingAuthorization()
      successMessage = nil
      errorMessage = nil
    }
    await loadDetail(plan)
  }

  func retryDetail() async {
    guard let selectedPlanID, let plan = currentPlan(id: selectedPlanID) else {
      detailState = .idle
      return
    }
    await loadDetail(plan)
  }

  func clearSelection(planID: String) {
    guard selectedPlanID == planID else { return }
    selectedPlanID = nil
    detailRequestID = UUID()
    detailState = .idle
    clearPendingAuthorization()
  }

  func previewAuthorization(
    for plan: ExitPlanItem,
    idempotencyKey: UUID = UUID()
  ) async throws {
    guard !operationInProgress else { throw fail(.alreadyInProgress) }
    if let reason = authorizationUnavailableReason(for: plan) {
      throw fail(.unavailable(reason))
    }
    let originalContextID = sessionContextID
    guard let repository = binding?.repository else {
      throw fail(.unavailable("退出计划服务尚未连接"))
    }
    var context = try repositoryContext()
    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    clearPendingAuthorization()
    defer { operationInProgress = false }
    do {
      let ticket = try await repository.previewAuthorization(
        plan: plan,
        idempotencyKey: idempotencyKey,
        context: context
      )
      try apply(ticket, expectedPlan: plan, expectedContextID: originalContextID)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        context = try repositoryContext()
        guard
          let refreshedPlan = currentPlan(id: plan.id),
          refreshedPlan.configVersion == plan.configVersion,
          let repository = binding?.repository
        else {
          throw ExitPlanWorkspaceError.contextChanged
        }
        let ticket = try await repository.previewAuthorization(
          plan: refreshedPlan,
          idempotencyKey: idempotencyKey,
          context: context
        )
        try apply(
          ticket,
          expectedPlan: refreshedPlan,
          expectedContextID: originalContextID
        )
      } catch {
        throw fail(Self.workspaceError(error))
      }
    } catch ExitPlanWorkspaceError.versionConflict {
      await refreshTruthAfterConflict(planID: plan.id)
      throw fail(.versionConflict)
    } catch {
      throw fail(Self.workspaceError(error))
    }
  }

  func dismissAuthorizationReview() {
    guard !operationInProgress else { return }
    clearPendingAuthorization()
  }

  func confirmPendingAuthorization() async throws {
    guard !operationInProgress else { throw fail(.alreadyInProgress) }
    guard let ticket = pendingTicket, pendingAuthorization == ticket.review else {
      throw fail(.contextChanged)
    }
    guard let plan = currentPlan(id: ticket.review.planID) else {
      throw fail(.contextChanged)
    }
    if let reason = authorizationUnavailableReason(for: plan) {
      throw fail(.unavailable(reason))
    }
    try validate(ticket: ticket, plan: plan)
    guard !ticket.review.isChallengeExpired() else {
      clearPendingAuthorization()
      throw fail(.challengeExpired)
    }

    let originalContextID = sessionContextID
    var context = try repositoryContext()
    operationInProgress = true
    successMessage = nil
    errorMessage = nil
    defer { operationInProgress = false }
    let biometricReason = "授权 \(plan.instrumentCode) 计划 v\(plan.configVersion) 自动实盘退出"
    do {
      try await localAuthentication.authorizeTrade(reason: biometricReason)
      try validate(ticket: ticket, plan: plan)
      guard !ticket.review.isChallengeExpired() else {
        throw ExitPlanWorkspaceError.challengeExpired
      }
      guard let repository = binding?.repository else {
        throw ExitPlanWorkspaceError.unavailable("退出计划服务尚未连接")
      }
      let result = try await repository.confirmAuthorization(ticket, context: context)
      try validate(ticket: ticket, plan: plan)
      clearPendingAuthorization()
      successMessage = "\(result.message)；审计编号 \(result.auditEventID)"
      await refreshAuthoritativeTruth(planID: plan.id)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        context = try repositoryContext()
        guard let refreshedPlan = currentPlan(id: plan.id) else {
          throw ExitPlanWorkspaceError.contextChanged
        }
        try validate(ticket: ticket, plan: refreshedPlan)
        guard !ticket.review.isChallengeExpired() else {
          throw ExitPlanWorkspaceError.challengeExpired
        }
        try await localAuthentication.authorizeTrade(reason: biometricReason)
        try validate(ticket: ticket, plan: refreshedPlan)
        guard !ticket.review.isChallengeExpired() else {
          throw ExitPlanWorkspaceError.challengeExpired
        }
        guard let repository = binding?.repository else {
          throw ExitPlanWorkspaceError.unavailable("退出计划服务尚未连接")
        }
        let result = try await repository.confirmAuthorization(ticket, context: context)
        try validate(ticket: ticket, plan: refreshedPlan)
        clearPendingAuthorization()
        successMessage = "\(result.message)；审计编号 \(result.auditEventID)"
        await refreshAuthoritativeTruth(planID: plan.id)
      } catch ReadOnlyRepositoryError.transport {
        await handleUncertainConfirmation(planID: plan.id)
        throw fail(.resultUncertain)
      } catch {
        clearPendingAuthorization()
        throw fail(Self.workspaceError(error))
      }
    } catch ReadOnlyRepositoryError.transport {
      await handleUncertainConfirmation(planID: plan.id)
      throw fail(.resultUncertain)
    } catch ExitPlanWorkspaceError.versionConflict {
      clearPendingAuthorization()
      await refreshTruthAfterConflict(planID: plan.id)
      throw fail(.versionConflict)
    } catch {
      clearPendingAuthorization()
      throw fail(Self.workspaceError(error))
    }
  }
}

extension ExitPlanWorkspace {
  fileprivate func loadPlans(previous: ExitPlanListSnapshot?) async {
    guard let repository = binding?.repository else {
      listState = .unavailable("退出计划服务尚未连接")
      return
    }
    let requestID = UUID()
    let contextID = sessionContextID
    listRequestID = requestID
    if previous == nil { listState = .loading }
    do {
      let snapshot = try await repository.loadPlans(context: repositoryContext())
      try apply(snapshot, requestID: requestID, contextID: contextID)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: contextID)
        guard let repository = binding?.repository else {
          throw ExitPlanWorkspaceError.unavailable("退出计划服务尚未连接")
        }
        let snapshot = try await repository.loadPlans(context: repositoryContext())
        try apply(snapshot, requestID: requestID, contextID: contextID)
      } catch {
        applyListFailure(error, previous: previous, requestID: requestID)
      }
    } catch {
      applyListFailure(error, previous: previous, requestID: requestID)
    }
  }

  fileprivate func apply(
    _ snapshot: ExitPlanListSnapshot,
    requestID: UUID,
    contextID: UUID
  ) throws {
    guard listRequestID == requestID, sessionContextID == contextID else { return }
    let context = try repositoryContext()
    guard snapshot.accountID == context.activeAccountID else {
      throw ExitPlanWorkspaceError.accountScopeMismatch
    }
    listState = .loaded(snapshot, refreshWarning: nil)
    reconcileTransientState(with: snapshot.plans)
  }

  fileprivate func applyListFailure(
    _ error: Error,
    previous: ExitPlanListSnapshot?,
    requestID: UUID
  ) {
    guard listRequestID == requestID else { return }
    if error is CancellationError {
      listState = previous.map { .loaded($0, refreshWarning: nil) } ?? .idle
      return
    }
    let message = Self.message(error, fallback: "退出计划暂时无法读取")
    listState =
      previous.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次服务端快照。\(message)")
      } ?? .failed(message)
    errorMessage = message
  }

  fileprivate func loadDetail(_ requestedPlan: ExitPlanItem) async {
    guard selectedPlanID == requestedPlan.id else { return }
    guard readUnavailableReason() == nil, let repository = binding?.repository else {
      detailState = .failed(
        planID: requestedPlan.id,
        message: readUnavailableReason() ?? "退出计划服务尚未连接"
      )
      return
    }
    guard let plan = currentPlan(id: requestedPlan.id) else {
      detailState = .failed(planID: requestedPlan.id, message: "计划已不在服务端列表中")
      return
    }
    let previous = detailState.snapshot?.plan.id == plan.id ? detailState.snapshot : nil
    let requestID = UUID()
    let contextID = sessionContextID
    detailRequestID = requestID
    if previous == nil { detailState = .loading(planID: plan.id) }
    do {
      let snapshot = try await repository.loadDetail(
        plan: plan,
        context: repositoryContext()
      )
      try applyDetail(snapshot, requestID: requestID, contextID: contextID)
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAndValidateSession(expectedContextID: contextID)
        guard
          let refreshedPlan = currentPlan(id: plan.id),
          refreshedPlan.configVersion == plan.configVersion,
          let repository = binding?.repository
        else {
          throw ExitPlanWorkspaceError.contextChanged
        }
        let snapshot = try await repository.loadDetail(
          plan: refreshedPlan,
          context: repositoryContext()
        )
        try applyDetail(snapshot, requestID: requestID, contextID: contextID)
      } catch {
        applyDetailFailure(error, planID: plan.id, previous: previous, requestID: requestID)
      }
    } catch {
      applyDetailFailure(error, planID: plan.id, previous: previous, requestID: requestID)
    }
  }

  fileprivate func applyDetail(
    _ snapshot: ExitPlanDetailSnapshot,
    requestID: UUID,
    contextID: UUID
  ) throws {
    guard detailRequestID == requestID, sessionContextID == contextID else { return }
    guard
      selectedPlanID == snapshot.plan.id,
      let listed = currentPlan(id: snapshot.plan.id),
      listed.accountID == snapshot.plan.accountID
    else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    if listed.configVersion != snapshot.plan.configVersion {
      clearPendingAuthorization()
    }
    detailState = .loaded(snapshot, refreshWarning: nil)
  }

  fileprivate func applyDetailFailure(
    _ error: Error,
    planID: String,
    previous: ExitPlanDetailSnapshot?,
    requestID: UUID
  ) {
    guard detailRequestID == requestID, selectedPlanID == planID else { return }
    if error is CancellationError {
      detailState = previous.map { .loaded($0, refreshWarning: nil) } ?? .idle
      return
    }
    let message = Self.message(error, fallback: "计划详情暂时无法读取")
    detailState =
      previous.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次详情。\(message)")
      } ?? .failed(planID: planID, message: message)
    errorMessage = message
  }

  fileprivate func apply(
    _ ticket: ExitPlanAuthorizationTicket,
    expectedPlan: ExitPlanItem,
    expectedContextID: UUID
  ) throws {
    guard sessionContextID == expectedContextID else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    try validate(ticket: ticket, plan: expectedPlan)
    pendingTicket = ticket
    pendingAuthorization = ticket.review
  }

  fileprivate func validate(
    ticket: ExitPlanAuthorizationTicket,
    plan: ExitPlanItem
  ) throws {
    let context = try repositoryContext()
    guard
      ticket.userID == context.userID,
      ticket.deviceSessionID == context.deviceSessionID,
      ticket.sessionContextID == context.sessionContextID,
      ticket.review.accountID == context.activeAccountID,
      ticket.review.planID == plan.id,
      ticket.review.instrumentCode == plan.instrumentCode,
      ticket.review.configVersion == plan.configVersion,
      ticket.review.protectedVolume == plan.protectedVolume,
      ticket.review.exitedVolume == plan.exitedVolume,
      ticket.review.remainingVolume == plan.remainingVolume,
      pendingTicket == nil || pendingTicket == ticket
    else {
      throw ExitPlanWorkspaceError.contextChanged
    }
  }

  fileprivate func repositoryContext() throws -> ExitPlanRepositoryContext {
    if let reason = readUnavailableReason() {
      throw ExitPlanWorkspaceError.unavailable(reason)
    }
    guard let identity = binding?.identity, let accountID = identity.activeAccountID else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    return ExitPlanRepositoryContext(
      userID: identity.userID,
      deviceSessionID: identity.deviceSessionID,
      activeAccountID: accountID,
      authorizedAccountIDs: identity.authorizedAccountIDs,
      sessionContextID: sessionContextID
    )
  }

  fileprivate func refreshAndValidateSession(expectedContextID: UUID) async throws {
    guard let refreshSession else { throw ReadOnlyRepositoryError.unauthenticated }
    try await refreshSession()
    guard sessionContextID == expectedContextID else {
      throw ExitPlanWorkspaceError.contextChanged
    }
  }

  fileprivate func refreshAuthoritativeTruth(planID: String) async {
    await refreshTradingTruth?()
    await refresh()
    if selectedPlanID == planID, let plan = currentPlan(id: planID) {
      await loadDetail(plan)
    }
  }

  fileprivate func refreshTruthAfterConflict(planID: String) async {
    clearPendingAuthorization()
    await refreshAuthoritativeTruth(planID: planID)
  }

  fileprivate func handleUncertainConfirmation(planID: String) async {
    clearPendingAuthorization()
    successMessage = nil
    await refreshAuthoritativeTruth(planID: planID)
  }

  fileprivate func reconcileTransientState(with plans: [ExitPlanItem]) {
    if let ticket = pendingTicket {
      guard let plan = plans.first(where: { $0.id == ticket.review.planID }) else {
        clearPendingAuthorization()
        return
      }
      if plan.accountID != ticket.review.accountID
        || plan.instrumentCode != ticket.review.instrumentCode
        || plan.configVersion != ticket.review.configVersion
        || plan.protectedVolume != ticket.review.protectedVolume
        || plan.exitedVolume != ticket.review.exitedVolume
        || plan.remainingVolume != ticket.review.remainingVolume
      {
        clearPendingAuthorization()
      }
    }
    if let selectedPlanID, !plans.contains(where: { $0.id == selectedPlanID }) {
      detailRequestID = UUID()
      detailState = .idle
      self.selectedPlanID = nil
      clearPendingAuthorization()
    }
  }

  fileprivate func currentPlan(id: String) -> ExitPlanItem? {
    listState.snapshot?.plans.first { $0.id == id }
  }

  fileprivate func clearPendingAuthorization() {
    pendingTicket = nil
    pendingAuthorization = nil
  }

  fileprivate func resetForIdentityChange() {
    listRequestID = UUID()
    detailRequestID = UUID()
    selectedPlanID = nil
    pendingTicket = nil
    pendingAuthorization = nil
    listState = .idle
    detailState = .idle
    operationInProgress = false
    successMessage = nil
    errorMessage = nil
  }

  @discardableResult
  fileprivate func fail(_ error: ExitPlanWorkspaceError) -> ExitPlanWorkspaceError {
    if error != .resultUncertain {
      successMessage = nil
    }
    errorMessage = error.localizedDescription
    return error
  }

  fileprivate static func workspaceError(_ error: Error) -> ExitPlanWorkspaceError {
    if let error = error as? ExitPlanWorkspaceError { return error }
    if let error = error as? LocalizedError, let message = error.errorDescription {
      return .unavailable(message)
    }
    return .unavailable("退出计划操作未完成")
  }

  fileprivate static func message(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }
}
