import Foundation

struct ManualTradingRuntimeContext {
  let accountID: String?
  let todayOrders: [OrderRecord]
  let hasTradingSnapshot: Bool
  let localSessionLocked: Bool
  let accountDataEnabled: Bool
}

enum ManualTradingStoreError: Error, Equatable, LocalizedError {
  case unavailable(String)
  case capabilityUnavailable(String)
  case unsupported(String)
  case alreadyInProgress
  case orderNotCancellable
  case contextChanged

  var errorDescription: String? {
    switch self {
    case .unavailable(let message), .capabilityUnavailable(let message),
      .unsupported(let message):
      message
    case .alreadyInProgress:
      "操作正在处理中，请勿重复提交"
    case .orderNotCancellable:
      "当前券商委托投影已不允许撤单，请以刷新后的状态为准"
    case .contextChanged:
      "账户或会话上下文已变化，请重新操作"
    }
  }
}

@MainActor
final class ManualTradingStore: ObservableObject {
  struct SessionIdentity: Equatable {
    let userID: String
    let deviceSessionID: String
    let activeAccountID: String?
    let authorizedAccountIDs: Set<String>
    let grantedScopes: Set<String>
  }

  private struct SessionBinding {
    let identity: SessionIdentity
    let manualOrderRepository: (any ManualOrderLoading)?
    let cancellationRepository: (any OrderCancellationLoading)?
  }

  typealias ContextProvider = @MainActor () -> ManualTradingRuntimeContext
  typealias RefreshSession = @MainActor () async throws -> Void
  typealias RefreshReadModels = @MainActor () async -> Void

  @Published private(set) var capabilityState: ManualOrderCapabilityState = .idle
  @Published private(set) var manualOrderInProgress = false
  @Published private(set) var cancellingOrderIDs: Set<String> = []
  @Published private(set) var cancellationConfirmations: [String: OrderCancellationQueueConfirmation] = [:]

  private let localAuthentication: any LocalAuthenticationProviding
  private var binding: SessionBinding?
  private var contextProvider: ContextProvider?
  private var refreshSession: RefreshSession?
  private var refreshReadModels: RefreshReadModels?
  private var sessionContextID = UUID()
  private var capabilityRequestID = UUID()
  private var cancellationIdempotencyKeys: [String: UUID] = [:]

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
    manualOrderRepository: (any ManualOrderLoading)?,
    cancellationRepository: (any OrderCancellationLoading)?
  ) {
    if binding?.identity != identity {
      sessionContextID = UUID()
      resetTransientState()
    }
    binding = SessionBinding(
      identity: identity,
      manualOrderRepository: manualOrderRepository,
      cancellationRepository: cancellationRepository
    )
  }

  func clearSession() {
    binding = nil
    sessionContextID = UUID()
    resetTransientState()
    manualOrderInProgress = false
    cancellingOrderIDs = []
  }

  func clearCapabilities() {
    capabilityRequestID = UUID()
    capabilityState = .idle
  }

  var manualOrderAvailabilityMessage: String? {
    guard let binding else { return "请先恢复个人账户会话" }
    guard let contextProvider else { return "手动委托上下文尚未连接" }
    let runtime = contextProvider()
    guard runtime.accountDataEnabled else { return "账户能力尚未启用" }
    guard binding.identity.grantedScopes.contains("trade:manual") else {
      return "当前会话没有 trade:manual 手动交易权限"
    }
    guard binding.identity.grantedScopes.contains("market:read") else {
      return "下单能力查询需要 market:read 权限"
    }
    guard !runtime.localSessionLocked else { return "请先解锁个人量化会话" }
    guard let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == Set([accountID])
    else {
      return "当前会话没有唯一主账户，手动委托保持关闭"
    }
    guard runtime.accountID == accountID else {
      return runtime.accountID == nil
        ? "主账户正在安全同步"
        : "主账户与当前会话不一致，手动委托已停止"
    }
    guard binding.manualOrderRepository != nil else {
      return "手动委托服务尚未连接"
    }
    return nil
  }

  var canPlaceManualOrders: Bool {
    manualOrderAvailabilityMessage == nil
  }

  func loadCapabilities(instrumentCode: String) async {
    let normalizedCode: String
    do {
      normalizedCode = try ManualOrderInstrument.canonicalCode(instrumentCode)
    } catch {
      clearCapabilities()
      return
    }
    let requestID = UUID()
    capabilityRequestID = requestID
    capabilityState = .loading(instrumentCode: normalizedCode)
    do {
      let capabilities = try await performCapabilityLoad(
        instrumentCode: normalizedCode
      )
      guard capabilityRequestID == requestID else { return }
      capabilityState = .loaded(capabilities)
    } catch is CancellationError {
      guard capabilityRequestID == requestID else { return }
      capabilityState = .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        let originalContextID = sessionContextID
        guard let refreshSession else {
          throw ReadOnlyRepositoryError.unauthenticated
        }
        try await refreshSession()
        guard sessionContextID == originalContextID else {
          throw ManualTradingStoreError.contextChanged
        }
        let capabilities = try await performCapabilityLoad(
          instrumentCode: normalizedCode
        )
        guard capabilityRequestID == requestID else { return }
        capabilityState = .loaded(capabilities)
      } catch {
        guard capabilityRequestID == requestID else { return }
        capabilityState = .failed(
          instrumentCode: normalizedCode,
          message: errorMessage(error, fallback: "下单能力暂时无法读取")
        )
      }
    } catch {
      guard capabilityRequestID == requestID else { return }
      capabilityState = .failed(
        instrumentCode: normalizedCode,
        message: errorMessage(error, fallback: "下单能力暂时无法读取")
      )
    }
  }

  func preview(
    instrumentCode: String,
    direction: ManualOrderDirection,
    quoteType: ManualOrderQuoteType,
    executionMode: ManualOrderExecutionMode,
    volume: Int,
    limitPrice: Double?,
    idempotencyKey: UUID
  ) async throws -> ManualOrderPreviewTicket {
    guard !manualOrderInProgress else {
      throw ManualTradingStoreError.alreadyInProgress
    }
    let normalizedCode = try ManualOrderInstrument.canonicalCode(instrumentCode)
    var context = try manualOrderContext()
    try validateCapability(
      instrumentCode: normalizedCode,
      accountID: context.accountID,
      direction: direction,
      quoteType: quoteType,
      executionMode: executionMode
    )
    let request = ManualOrderRequest(
      accountID: context.accountID,
      instrumentCode: normalizedCode,
      direction: direction,
      quoteType: quoteType,
      executionMode: executionMode,
      volume: volume,
      limitPrice: limitPrice,
      idempotencyKey: idempotencyKey
    )
    let originalContextID = sessionContextID
    guard let repository = binding?.manualOrderRepository else {
      throw ManualTradingStoreError.unavailable("手动委托服务尚未连接")
    }

    manualOrderInProgress = true
    defer { manualOrderInProgress = false }
    do {
      return try await repository.preview(
        request,
        authorizedAccountIDs: context.authorizedAccountIDs
      )
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAndValidateSession(expectedContextID: originalContextID)
      context = try manualOrderContext()
      guard context.accountID == request.accountID else {
        throw ManualOrderRepositoryError.accountScopeMismatch
      }
      let refreshedCapabilities = try await performCapabilityLoad(
        instrumentCode: normalizedCode
      )
      capabilityState = .loaded(refreshedCapabilities)
      try validateCapability(
        instrumentCode: normalizedCode,
        accountID: context.accountID,
        direction: direction,
        quoteType: quoteType,
        executionMode: executionMode
      )
      guard let refreshedRepository = binding?.manualOrderRepository else {
        throw ManualTradingStoreError.unavailable("手动委托服务尚未连接")
      }
      return try await refreshedRepository.preview(
        request,
        authorizedAccountIDs: context.authorizedAccountIDs
      )
    }
  }

  func confirm(
    _ preview: ManualOrderPreviewTicket
  ) async throws -> ManualOrderQueueConfirmation {
    guard !manualOrderInProgress else {
      throw ManualTradingStoreError.alreadyInProgress
    }
    guard !preview.isExpired() else {
      throw ManualTradingStoreError.unavailable("确认凭据已过期，请重新预览")
    }
    var context = try manualOrderContext()
    guard context.accountID == preview.accountID else {
      throw ManualOrderRepositoryError.accountScopeMismatch
    }
    try validateCapability(for: preview)
    let originalContextID = sessionContextID

    manualOrderInProgress = true
    defer { manualOrderInProgress = false }
    try await authorizeLiveConfirmationIfNeeded(preview)
    guard !preview.isExpired() else {
      throw ManualTradingStoreError.unavailable(
        "本机认证完成时确认凭据已过期，请重新预览"
      )
    }

    let confirmation: ManualOrderQueueConfirmation
    do {
      confirmation = try await performConfirmation(preview)
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAndValidateSession(expectedContextID: originalContextID)
      context = try manualOrderContext()
      guard context.accountID == preview.accountID else {
        throw ManualOrderRepositoryError.accountScopeMismatch
      }
      let refreshedCapabilities = try await performCapabilityLoad(
        instrumentCode: preview.instrumentCode
      )
      capabilityState = .loaded(refreshedCapabilities)
      try validateCapability(for: preview)
      try await authorizeLiveConfirmationIfNeeded(preview)
      guard !preview.isExpired() else {
        throw ManualTradingStoreError.unavailable(
          "本机认证完成时确认凭据已过期，请重新预览"
        )
      }
      confirmation = try await performConfirmation(preview)
    }
    await refreshReadModels?()
    return confirmation
  }

  func canCancel(_ order: OrderRecord) -> Bool {
    do {
      _ = try cancellationContext(for: order)
      return true
    } catch ManualTradingStoreError.orderNotCancellable {
      if contextProvider?().hasTradingSnapshot == true {
        cancellationIdempotencyKeys[order.id] = nil
      }
      return false
    } catch {
      return false
    }
  }

  func isCancelling(orderID: String) -> Bool {
    cancellingOrderIDs.contains(orderID)
  }

  func cancellationConfirmation(
    orderID: String
  ) -> OrderCancellationQueueConfirmation? {
    cancellationConfirmations[orderID]
  }

  func reconcileCancellationProjection() {
    guard let contextProvider else { return }
    let runtime = contextProvider()
    guard runtime.hasTradingSnapshot else { return }
    let cancellableOrderIDs = Set(
      runtime.todayOrders.filter(\.canCancel).map(\.id)
    )
    cancellationIdempotencyKeys = cancellationIdempotencyKeys.filter {
      cancellableOrderIDs.contains($0.key)
    }
  }

  func cancel(
    _ order: OrderRecord,
    idempotencyKey: UUID? = nil
  ) async throws -> OrderCancellationQueueConfirmation {
    guard !cancellingOrderIDs.contains(order.id) else {
      throw ManualTradingStoreError.alreadyInProgress
    }
    let stableIdempotencyKey = cancellationIdempotencyKeys[order.id]
      ?? idempotencyKey
      ?? UUID()
    cancellationIdempotencyKeys[order.id] = stableIdempotencyKey
    let initialContext: (
      accountID: String,
      orderID: Int,
      authorizedAccountIDs: Set<String>
    )
    do {
      initialContext = try cancellationContext(for: order)
    } catch ManualTradingStoreError.orderNotCancellable {
      cancellationIdempotencyKeys[order.id] = nil
      throw ManualTradingStoreError.orderNotCancellable
    }
    var context = initialContext
    let request = OrderCancellationRequest(
      accountID: context.accountID,
      orderID: context.orderID,
      idempotencyKey: stableIdempotencyKey
    )
    let originalContextID = sessionContextID
    guard let repository = binding?.cancellationRepository else {
      throw ManualTradingStoreError.unavailable("撤单服务尚未连接")
    }

    cancellingOrderIDs.insert(order.id)
    cancellationConfirmations[order.id] = nil
    defer { cancellingOrderIDs.remove(order.id) }
    do {
      let confirmation: OrderCancellationQueueConfirmation
      do {
        confirmation = try await repository.cancel(
          request,
          authorizedAccountIDs: context.authorizedAccountIDs
        )
      } catch ReadOnlyRepositoryError.unauthenticated {
        try await refreshAndValidateSession(expectedContextID: originalContextID)
        context = try cancellationContext(for: order)
        guard
          context.accountID == request.accountID,
          context.orderID == request.orderID,
          let refreshedRepository = binding?.cancellationRepository
        else {
          throw ManualTradingStoreError.contextChanged
        }
        confirmation = try await refreshedRepository.cancel(
          request,
          authorizedAccountIDs: context.authorizedAccountIDs
        )
      }
      cancellationConfirmations[order.id] = confirmation
      await refreshReadModels?()
      reconcileCancellationProjection()
      return confirmation
    } catch let error as OrderCancellationRepositoryError {
      await refreshReadModels?()
      if case .rejected = error {
        cancellationIdempotencyKeys[order.id] = nil
      } else {
        reconcileCancellationProjection()
      }
      throw error
    } catch {
      await refreshReadModels?()
      reconcileCancellationProjection()
      throw error
    }
  }

  private func performCapabilityLoad(
    instrumentCode: String
  ) async throws -> ManualOrderEntryCapabilities {
    let context = try manualOrderContext()
    guard let repository = binding?.manualOrderRepository else {
      throw ManualTradingStoreError.unavailable("手动委托服务尚未连接")
    }
    return try await repository.capabilities(
      instrumentCode: instrumentCode,
      accountID: context.accountID,
      authorizedAccountIDs: context.authorizedAccountIDs
    )
  }

  private func performConfirmation(
    _ preview: ManualOrderPreviewTicket
  ) async throws -> ManualOrderQueueConfirmation {
    guard let repository = binding?.manualOrderRepository else {
      throw ManualTradingStoreError.unavailable("手动委托服务尚未连接")
    }
    return try await repository.confirm(preview)
  }

  private func authorizeLiveConfirmationIfNeeded(
    _ preview: ManualOrderPreviewTicket
  ) async throws {
    guard preview.executionMode == .live else { return }
    guard localAuthentication.tradeAuthorizationAvailable else {
      throw ManualTradingStoreError.unavailable(
        "实盘委托要求此设备已启用 Face ID 或 Touch ID"
      )
    }
    try await localAuthentication.authorizeTrade(
      reason: "确认实盘提交 \(preview.instrumentCode) \(preview.direction.title)委托"
    )
  }

  private func manualOrderContext() throws -> (
    accountID: String,
    authorizedAccountIDs: Set<String>
  ) {
    if let reason = manualOrderAvailabilityMessage {
      throw ManualTradingStoreError.unavailable(reason)
    }
    guard let binding, let accountID = binding.identity.activeAccountID else {
      throw ManualTradingStoreError.unavailable("请先恢复个人账户会话")
    }
    return (accountID, binding.identity.authorizedAccountIDs)
  }

  private func cancellationContext(
    for order: OrderRecord
  ) throws -> (
    accountID: String,
    orderID: Int,
    authorizedAccountIDs: Set<String>
  ) {
    guard let binding, let contextProvider else {
      throw ManualTradingStoreError.unavailable("撤单上下文尚未连接")
    }
    let runtime = contextProvider()
    guard runtime.accountDataEnabled else {
      throw ManualTradingStoreError.unavailable("账户能力尚未启用")
    }
    guard binding.identity.grantedScopes.contains("trade:manual") else {
      throw ManualTradingStoreError.unavailable(
        "当前会话没有 trade:manual 手动交易权限"
      )
    }
    guard binding.identity.grantedScopes.contains("orders:read") else {
      throw ManualTradingStoreError.unavailable("撤单需要 orders:read 委托读取权限")
    }
    guard !runtime.localSessionLocked else {
      throw ManualTradingStoreError.unavailable("请先解锁个人量化会话")
    }
    guard let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == Set([accountID]),
      runtime.accountID == accountID
    else {
      throw OrderCancellationRepositoryError.accountScopeMismatch
    }
    guard binding.cancellationRepository != nil else {
      throw ManualTradingStoreError.unavailable("撤单服务尚未连接")
    }
    let matches = runtime.todayOrders.filter { $0.id == order.id }
    guard matches.count == 1,
      let latest = matches.first,
      latest.canCancel,
      let orderID = latest.brokerOrderID
    else {
      throw ManualTradingStoreError.orderNotCancellable
    }
    return (accountID, orderID, binding.identity.authorizedAccountIDs)
  }

  private func validateCapability(
    for preview: ManualOrderPreviewTicket
  ) throws {
    try validateCapability(
      instrumentCode: preview.instrumentCode,
      accountID: preview.accountID,
      direction: preview.direction,
      quoteType: preview.quoteType,
      executionMode: preview.executionMode
    )
  }

  private func validateCapability(
    instrumentCode: String,
    accountID: String,
    direction: ManualOrderDirection,
    quoteType: ManualOrderQuoteType,
    executionMode: ManualOrderExecutionMode
  ) throws {
    guard let capabilities = capabilityState.capabilities,
      capabilities.instrumentCode == instrumentCode,
      capabilities.accountID == accountID
    else {
      throw ManualTradingStoreError.capabilityUnavailable(
        "请先获取当前证券的服务端下单能力"
      )
    }
    guard capabilities.canManualTrade else {
      throw ManualTradingStoreError.capabilityUnavailable(
        capabilities.liveBlockedReasons.first ?? "当前证券暂不可手动交易"
      )
    }
    guard capabilities.supportedDirections.contains(direction) else {
      throw ManualTradingStoreError.unsupported("当前证券不支持该委托方向")
    }
    guard capabilities.supportedQuoteTypes.contains(quoteType) else {
      throw ManualTradingStoreError.unsupported("服务端未开放该报价方式")
    }
    guard !(quoteType == .best && ManualOrderInstrument.isBeijing(instrumentCode)) else {
      throw ManualTradingStoreError.unsupported("北交所暂不支持对手方最优价委托")
    }
    guard capabilities.executionModes.contains(executionMode) else {
      throw ManualTradingStoreError.unsupported("服务端未开放该执行模式")
    }
    guard executionMode != .live || capabilities.liveReady else {
      throw ManualTradingStoreError.unsupported(
        capabilities.liveBlockedReasons.first ?? "实盘链路尚未就绪"
      )
    }
  }

  private func refreshAndValidateSession(
    expectedContextID: UUID
  ) async throws {
    guard let refreshSession else {
      throw ReadOnlyRepositoryError.unauthenticated
    }
    try await refreshSession()
    guard sessionContextID == expectedContextID else {
      throw ManualTradingStoreError.contextChanged
    }
    guard let binding, let contextProvider,
      binding.identity.grantedScopes.contains("trade:manual"),
      let accountID = binding.identity.activeAccountID,
      binding.identity.authorizedAccountIDs == Set([accountID]),
      contextProvider().accountID == accountID
    else {
      throw ManualTradingStoreError.contextChanged
    }
  }

  private func resetTransientState() {
    capabilityRequestID = UUID()
    capabilityState = .idle
    cancellationConfirmations = [:]
    cancellationIdempotencyKeys = [:]
  }

  private func errorMessage(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }
}
