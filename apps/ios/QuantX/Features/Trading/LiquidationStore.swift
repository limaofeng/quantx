import Foundation

struct LiquidationPortfolioContext {
  let accountID: String?
  let instrumentCodes: Set<String>
  let localSessionLocked: Bool
  let accountDataEnabled: Bool
}

struct LiquidationResultRecoveryAuthorization: Equatable, Sendable {
  fileprivate let challengeID: String
  fileprivate let groupID: String
  fileprivate let contextID: UUID

  fileprivate func matches(_ preview: LiquidationPreviewTicket) -> Bool {
    challengeID == preview.id
      && groupID == preview.groupID
      && contextID == preview.contextID
  }
}

@MainActor
final class LiquidationStore: ObservableObject {
  struct SessionIdentity: Equatable {
    let userID: String
    let deviceSessionID: String
    let activeAccountID: String?
    let authorizedAccountIDs: Set<String>
    let grantedScopes: Set<String>
  }

  private struct SessionBinding {
    let identity: SessionIdentity
    let repository: (any LiquidationLoading)?
  }

  typealias ContextProvider = @MainActor () -> LiquidationPortfolioContext
  typealias RefreshSession = @MainActor () async throws -> Void
  typealias RefreshReadModels = @MainActor () async -> Void

  @Published private(set) var operationInProgress = false
  @Published private(set) var challengeContextID = UUID()

  private let localAuthentication: any LocalAuthenticationProviding
  private var binding: SessionBinding?
  private var contextProvider: ContextProvider?
  private var refreshSession: RefreshSession?
  private var refreshReadModels: RefreshReadModels?

  init(localAuthentication: any LocalAuthenticationProviding) {
    self.localAuthentication = localAuthentication
  }

  func configure(
    contextProvider: @escaping ContextProvider,
    refreshSession: @escaping RefreshSession,
    refreshReadModels: @escaping RefreshReadModels
  ) {
    self.contextProvider = contextProvider
    self.refreshSession = refreshSession
    self.refreshReadModels = refreshReadModels
  }

  func activate(
    identity: SessionIdentity,
    repository: (any LiquidationLoading)?
  ) {
    if binding?.identity != identity {
      invalidateChallengeContext()
    }
    binding = SessionBinding(identity: identity, repository: repository)
  }

  func clearSession() {
    binding = nil
    invalidateChallengeContext()
    operationInProgress = false
  }

  func invalidateChallengeContext() {
    challengeContextID = UUID()
  }

  func previewUnavailableReason(for mode: LiquidationExecutionMode) -> String? {
    guard let binding else { return "请先恢复个人账户会话" }
    guard binding.identity.grantedScopes.contains("liquidation:control") else {
      return "当前会话没有 liquidation:control 权限，卖出管理保持只读"
    }
    guard let contextProvider else { return "卖出管理上下文尚未连接" }
    let runtime = contextProvider()
    guard runtime.accountDataEnabled else { return "账户能力尚未启用" }
    guard !runtime.localSessionLocked else { return "请先解锁个人量化会话" }
    guard let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == Set([accountID])
    else {
      return "当前会话没有唯一主账户，卖出管理保持只读"
    }
    guard runtime.accountID == accountID else {
      return runtime.accountID == nil
        ? "主账户持仓正在安全同步"
        : "持仓账户与当前主账户不一致，卖出管理已停止"
    }
    guard binding.repository != nil else { return "卖出管理服务尚未连接" }
    if mode == .live {
      guard binding.identity.grantedScopes.contains("trade:approve") else {
        return "实盘卖出还需要独立的 trade:approve 权限"
      }
      guard localAuthentication.tradeAuthorizationAvailable else {
        return "实盘卖出要求此设备已启用 Face ID 或 Touch ID"
      }
    }
    return nil
  }

  func confirmationUnavailableReason(
    for preview: LiquidationPreviewTicket
  ) -> String? {
    if let reason = confirmationUnavailableReason(for: preview.executionMode) {
      return reason
    }
    guard preview.contextID == challengeContextID else {
      return "账户或会话上下文已变化，请重新预览"
    }
    return nil
  }

  func confirmationUnavailableReason(
    for mode: LiquidationExecutionMode
  ) -> String? {
    if let reason = previewUnavailableReason(for: mode) {
      return reason
    }
    guard let binding else { return "请先恢复个人账户会话" }
    guard binding.identity.grantedScopes.contains("trade:approve") else {
      return "确认卖出计划需要独立的 trade:approve 权限"
    }
    guard localAuthentication.tradeAuthorizationAvailable else {
      return "确认卖出计划要求此设备已启用 Face ID 或 Touch ID"
    }
    return nil
  }

  func preview(
    scope: LiquidationScope,
    instrumentCodes: [String],
    completionStrategy: LiquidationCompletionStrategy,
    conflictStrategy: LiquidationConflictStrategy,
    executionMode: LiquidationExecutionMode,
    idempotencyKey: UUID = UUID()
  ) async throws -> LiquidationPreviewTicket {
    guard !operationInProgress else {
      throw LiquidationStoreError.alreadyInProgress
    }
    if let reason = previewUnavailableReason(for: executionMode) {
      throw LiquidationStoreError.unavailable(reason)
    }
    let originalContextID = challengeContextID
    var context = try currentRepositoryContext()
    let normalizedCodes = try instrumentCodes
      .map(LiquidationDomainValidator.canonicalInstrumentCode)
      .sorted()
    let request = LiquidationPreviewRequest(
      accountID: context.activeAccountID,
      scope: scope,
      instrumentCodes: normalizedCodes,
      completionStrategy: completionStrategy,
      conflictStrategy: conflictStrategy,
      executionMode: executionMode,
      idempotencyKey: idempotencyKey
    )
    guard let repository = binding?.repository else {
      throw LiquidationStoreError.unavailable("卖出管理服务尚未连接")
    }

    operationInProgress = true
    defer { operationInProgress = false }
    do {
      let preview = try await repository.preview(request, context: context)
      try validateCurrentContext(preview, expectedContextID: originalContextID)
      return preview
    } catch ReadOnlyRepositoryError.unauthenticated {
      guard let refreshSession else { throw ReadOnlyRepositoryError.unauthenticated }
      try await refreshSession()
      guard challengeContextID == originalContextID else {
        throw LiquidationStoreError.contextChanged
      }
      if let reason = previewUnavailableReason(for: executionMode) {
        throw LiquidationStoreError.unavailable(reason)
      }
      context = try currentRepositoryContext()
      guard context.activeAccountID == request.accountID,
        let refreshedRepository = binding?.repository
      else {
        throw LiquidationRepositoryError.accountScopeMismatch
      }
      let preview = try await refreshedRepository.preview(request, context: context)
      try validateCurrentContext(preview, expectedContextID: originalContextID)
      return preview
    }
  }

  func confirm(
    _ preview: LiquidationPreviewTicket,
    recoveryAuthorization: LiquidationResultRecoveryAuthorization?,
    onTransmissionStarted: @escaping @MainActor (
      LiquidationResultRecoveryAuthorization
    ) -> Void
  ) async throws -> LiquidationConfirmation {
    guard !operationInProgress else {
      throw LiquidationStoreError.alreadyInProgress
    }
    if let reason = confirmationUnavailableReason(for: preview) {
      throw LiquidationStoreError.unavailable(reason)
    }
    let isResultRecovery = recoveryAuthorization?.matches(preview) == true
    guard isResultRecovery || !preview.isExpired() else {
      throw LiquidationStoreError.challengeExpired
    }
    let originalContextID = challengeContextID
    var context = try currentRepositoryContext()
    try validateCurrentContext(preview, expectedContextID: originalContextID)

    operationInProgress = true
    defer { operationInProgress = false }
    let biometricReason = preview.executionMode == .live
      ? "确认实盘创建 \(preview.includedCount) 只持仓的卖出计划"
      : "确认模拟创建 \(preview.includedCount) 只持仓的卖出计划"
    try await localAuthentication.authorizeTrade(reason: biometricReason)
    guard isResultRecovery || !preview.isExpired() else {
      throw LiquidationStoreError.challengeExpired
    }
    try validateCurrentContext(preview, expectedContextID: originalContextID)

    do {
      guard let repository = binding?.repository else {
        throw LiquidationStoreError.unavailable("卖出管理服务尚未连接")
      }
      return try await transmitConfirmation(
        preview,
        repository: repository,
        context: context,
        expectedContextID: originalContextID,
        resultRecovery: isResultRecovery,
        onTransmissionStarted: onTransmissionStarted
      )
    } catch ReadOnlyRepositoryError.unauthenticated {
      guard let refreshSession else { throw ReadOnlyRepositoryError.unauthenticated }
      try await refreshSession()
      guard challengeContextID == originalContextID else {
        throw LiquidationStoreError.contextChanged
      }
      if let reason = confirmationUnavailableReason(for: preview) {
        throw LiquidationStoreError.unavailable(reason)
      }
      guard isResultRecovery || !preview.isExpired() else {
        throw LiquidationStoreError.challengeExpired
      }
      context = try currentRepositoryContext()
      try await localAuthentication.authorizeTrade(reason: biometricReason)
      guard isResultRecovery || !preview.isExpired() else {
        throw LiquidationStoreError.challengeExpired
      }
      try validateCurrentContext(preview, expectedContextID: originalContextID)
      guard let repository = binding?.repository else {
        throw LiquidationStoreError.unavailable("卖出管理服务尚未连接")
      }
      do {
        return try await transmitConfirmation(
          preview,
          repository: repository,
          context: context,
          expectedContextID: originalContextID,
          resultRecovery: isResultRecovery,
          onTransmissionStarted: onTransmissionStarted
        )
      } catch is CancellationError {
        throw CancellationError()
      } catch let error as LiquidationRepositoryError {
        throw error
      } catch let error as LiquidationStoreError {
        throw error
      } catch ReadOnlyRepositoryError.transport {
        throw LiquidationStoreError.resultUncertain
      }
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as LiquidationRepositoryError {
      throw error
    } catch let error as LiquidationStoreError {
      throw error
    } catch ReadOnlyRepositoryError.transport {
      throw LiquidationStoreError.resultUncertain
    }
  }

  static func allowsResultRecovery(after error: Error) -> Bool {
    if case LiquidationStoreError.resultUncertain = error { return true }
    return (error as? LiquidationRepositoryError)?.allowsResultRecovery == true
  }

  private func currentRepositoryContext() throws -> LiquidationRepositoryContext {
    guard let binding, let contextProvider else {
      throw LiquidationStoreError.unavailable("卖出管理上下文尚未连接")
    }
    let runtime = contextProvider()
    guard let activeAccountID = binding.identity.activeAccountID,
      runtime.accountID == activeAccountID
    else {
      throw LiquidationRepositoryError.accountScopeMismatch
    }
    let canonicalCodes = try Set(
      runtime.instrumentCodes.map(LiquidationDomainValidator.canonicalInstrumentCode)
    )
    guard canonicalCodes == runtime.instrumentCodes else {
      throw LiquidationRepositoryError.contextMismatch
    }
    return LiquidationRepositoryContext(
      activeAccountID: activeAccountID,
      authorizedAccountIDs: binding.identity.authorizedAccountIDs,
      portfolioInstrumentCodes: canonicalCodes,
      contextID: challengeContextID
    )
  }

  private func validateCurrentContext(
    _ preview: LiquidationPreviewTicket,
    expectedContextID: UUID
  ) throws {
    let context = try currentRepositoryContext()
    guard
      challengeContextID == expectedContextID,
      preview.contextID == expectedContextID,
      preview.accountID == context.activeAccountID
    else {
      throw LiquidationStoreError.contextChanged
    }
  }

  private func transmitConfirmation(
    _ preview: LiquidationPreviewTicket,
    repository: any LiquidationLoading,
    context: LiquidationRepositoryContext,
    expectedContextID: UUID,
    resultRecovery: Bool,
    onTransmissionStarted: @escaping @MainActor (
      LiquidationResultRecoveryAuthorization
    ) -> Void
  ) async throws -> LiquidationConfirmation {
    onTransmissionStarted(
      LiquidationResultRecoveryAuthorization(
        challengeID: preview.id,
        groupID: preview.groupID,
        contextID: preview.contextID
      )
    )
    let confirmation = try await repository.confirm(
      preview,
      context: context,
      resultRecovery: resultRecovery
    )
    try validateCurrentContext(preview, expectedContextID: expectedContextID)
    await refreshReadModels?()
    return confirmation
  }
}
