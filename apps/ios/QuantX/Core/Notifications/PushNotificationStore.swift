import Foundation

@MainActor
final class PushNotificationStore: ObservableObject {
  typealias SessionRefresh = @MainActor () async throws -> Void

  @Published private(set) var authorizationStatus: PushAuthorizationStatus = .unknown
  @Published private(set) var authorizationRequestInProgress = false
  @Published private(set) var registrationState: PushRegistrationState = .idle
  @Published private(set) var preferences: [PushNotificationCategory: Bool]?
  @Published private(set) var preferenceUpdateInProgress = false
  @Published private(set) var preferenceErrorMessage: String?
  @Published private(set) var deepLinkState: NotificationDeepLinkState = .idle
  @Published private(set) var navigationRequest: NotificationNavigationRequest?

  private struct RegistrationKey: Equatable {
    let sessionContextID: UUID
    let deviceToken: String
    let metadata: PushDeviceMetadata
  }

  private let runtime: PushNotificationRuntimeConfiguration?
  private let system: any PushNotificationSystemManaging
  private let installationStore: any DeviceInstallIDStoring
  private let now: @Sendable () -> Date
  private var sessionRefresh: SessionRefresh?
  private var identity: PushNotificationSessionIdentity?
  private var repository: (any PushNotificationLoading)?
  private var cachedMetadata: PushDeviceMetadata?
  private var deviceToken: String?
  private var registeredKey: RegistrationKey?
  private var registrationAttemptID: UUID?
  private var pendingEventID: UUID?
  private var routeAttemptID: UUID?
  private var routeRefreshAttemptedEventID: UUID?
  private var preferenceAttemptID: UUID?
  private var localSessionUnlocked = false

  init(
    runtime: PushNotificationRuntimeConfiguration?,
    system: any PushNotificationSystemManaging,
    installationStore: any DeviceInstallIDStoring,
    now: @escaping @Sendable () -> Date = Date.init
  ) {
    self.runtime = runtime
    self.system = system
    self.installationStore = installationStore
    self.now = now
    if runtime == nil {
      registrationState = .unavailable("通知运行配置无效")
    }
  }

  convenience init(bundle: Bundle = .main) {
    self.init(
      runtime: PushNotificationRuntimeConfiguration.load(bundle: bundle),
      system: ApplePushNotificationSystem(),
      installationStore: KeychainDeviceInstallIDStore(
        service: bundle.bundleIdentifier ?? "com.limaofeng.quantx"
      )
    )
  }

  static func disabled() -> PushNotificationStore {
    PushNotificationStore(
      runtime: nil,
      system: DisabledPushNotificationSystem(),
      installationStore: DisabledDeviceInstallIDStore()
    )
  }

  func configure(sessionRefresh: @escaping SessionRefresh) {
    self.sessionRefresh = sessionRefresh
  }

  func prepareSystemState() async {
    guard runtime != nil else {
      registrationState = .unavailable("通知运行配置无效")
      return
    }
    do {
      _ = try await metadata()
    } catch {
      registrationState = .unavailable(localized(error, fallback: "无法准备本设备通知标识"))
      return
    }
    await refreshSystemAuthorization()
  }

  func refreshSystemAuthorization() async {
    authorizationStatus = await system.authorizationStatus()
    guard authorizationStatus.permitsRemoteRegistration else {
      if authorizationStatus == .denied {
        registrationState = .waitingForAuthorization
      } else if runtime != nil {
        registrationState = .waitingForAuthorization
      }
      return
    }
    system.registerForRemoteNotifications()
    updateWaitingState()
    await registerIfReady()
  }

  /// This is the only entry point that may present the iOS notification prompt.
  func enableNotifications() async {
    guard !authorizationRequestInProgress, runtime != nil else { return }
    authorizationRequestInProgress = true
    preferenceErrorMessage = nil
    defer { authorizationRequestInProgress = false }
    do {
      _ = try await system.requestAuthorization()
      authorizationStatus = await system.authorizationStatus()
      if authorizationStatus.permitsRemoteRegistration {
        system.registerForRemoteNotifications()
        updateWaitingState()
        await registerIfReady()
      } else {
        registrationState = .waitingForAuthorization
      }
    } catch {
      authorizationStatus = await system.authorizationStatus()
      registrationState = .unavailable("系统未能完成通知授权，请稍后重试")
    }
  }

  func openSystemNotificationSettings() async {
    await system.openNotificationSettings()
  }

  func activate(
    identity newIdentity: PushNotificationSessionIdentity,
    repository newRepository: (any PushNotificationLoading)?,
    localSessionUnlocked: Bool
  ) async {
    let previousIdentity = identity
    let samePrincipal = previousIdentity.map { Self.samePrincipal($0, newIdentity) } == true
    let principalChanged = previousIdentity != nil && !samePrincipal
    if principalChanged {
      clearSessionSensitiveState(stopRemoteRegistration: true)
    }
    let hadPreferenceUpdateInFlight = preferenceAttemptID != nil
    let canPreserveRegistration = samePrincipal
      && newIdentity.isAuthorizedUniqueAccount
      && newRepository != nil
      && !hadPreferenceUpdateInFlight
    let previousRegisteredKey = registeredKey
    identity = newIdentity
    repository = newIdentity.isAuthorizedUniqueAccount ? newRepository : nil
    self.localSessionUnlocked = localSessionUnlocked
    registrationAttemptID = nil
    routeAttemptID = nil
    preferenceAttemptID = nil
    preferenceUpdateInProgress = false
    if canPreserveRegistration, let previousRegisteredKey {
      registeredKey = RegistrationKey(
        sessionContextID: newIdentity.contextID,
        deviceToken: previousRegisteredKey.deviceToken,
        metadata: previousRegisteredKey.metadata
      )
    } else {
      registeredKey = nil
      preferences = nil
      preferenceErrorMessage = nil
    }

    guard newIdentity.isAuthorizedUniqueAccount, newRepository != nil else {
      deviceToken = nil
      system.unregisterForRemoteNotifications()
      registrationState = .unavailable(
        "通知需要唯一主账户与 notification:manage 权限"
      )
      if pendingEventID != nil {
        pendingEventID = nil
        routeRefreshAttemptedEventID = nil
        deepLinkState = .unavailable(
          "当前会话没有可验证此通知的唯一账户与 notification:manage 权限"
        )
      }
      return
    }
    do {
      _ = try await metadata()
    } catch {
      registrationState = .unavailable(localized(error, fallback: "无法准备本设备通知标识"))
      return
    }
    if authorizationStatus.permitsRemoteRegistration {
      system.registerForRemoteNotifications()
    }
    updateWaitingState()
    await registerIfReady()
    await resolvePendingIfReady()
  }

  func setLocalSessionUnlocked(_ unlocked: Bool) async {
    localSessionUnlocked = unlocked
    if unlocked {
      await resolvePendingIfReady()
    } else if pendingEventID != nil {
      deepLinkState = .waitingForUnlock
    }
  }

  func lockLocalSession() {
    localSessionUnlocked = false
    if pendingEventID != nil {
      deepLinkState = .waitingForUnlock
    }
  }

  func receive(deviceToken: String) async {
    guard
      !deviceToken.isEmpty,
      deviceToken.count.isMultiple(of: 2),
      deviceToken.allSatisfy({ $0.isHexDigit && !$0.isUppercase })
    else {
      registrationState = .unavailable("系统返回了无效的通知设备标识")
      return
    }
    self.deviceToken = deviceToken
    updateWaitingState()
    await registerIfReady()
  }

  func receiveRemoteRegistrationFailure() {
    guard authorizationStatus.permitsRemoteRegistration else { return }
    registrationState = .unavailable("暂时无法向 Apple 注册通知，请稍后重试")
  }

  func receive(notificationEventID: UUID) async {
    pendingEventID = notificationEventID
    navigationRequest = nil
    routeAttemptID = nil
    routeRefreshAttemptedEventID = nil
    deepLinkState = localSessionUnlocked ? .idle : .waitingForUnlock
    await resolvePendingIfReady()
  }

  func receiveInvalidNotificationResponse() {
    pendingEventID = nil
    navigationRequest = nil
    routeAttemptID = nil
    routeRefreshAttemptedEventID = nil
    deepLinkState = .unavailable("这条通知链接无法验证，未执行任何操作")
  }

  func consumeNavigationRequest() -> NotificationNavigationRequest? {
    defer { navigationRequest = nil }
    return navigationRequest
  }

  func dismissDeepLinkStatus() {
    if case .unavailable = deepLinkState {
      deepLinkState = .idle
    }
  }

  func updatePreference(_ category: PushNotificationCategory, enabled: Bool) async {
    guard
      !preferenceUpdateInProgress,
      let previous = preferences,
      let repository,
      let currentIdentity = identity,
      currentIdentity.isAuthorizedUniqueAccount,
      let metadata = cachedMetadata,
      Set(previous.keys) == Set(PushNotificationCategory.allCases)
    else {
      return
    }
    var proposed = previous
    proposed[category] = enabled
    let attemptID = UUID()
    preferenceAttemptID = attemptID
    preferences = proposed
    preferenceUpdateInProgress = true
    preferenceErrorMessage = nil
    defer {
      if preferenceAttemptID == attemptID {
        preferenceAttemptID = nil
        preferenceUpdateInProgress = false
      }
    }
    do {
      let snapshot = try await repository.updatePreferences(proposed, metadata: metadata)
      guard
        preferenceAttemptID == attemptID,
        identity?.contextID == currentIdentity.contextID,
        snapshot.metadata == metadata
      else {
        await resynchronizeAfterInvalidatedPreferenceAttempt(from: currentIdentity)
        return
      }
      preferences = snapshot.preferences
      registrationState = .registered(updatedAt: snapshot.updatedAt)
    } catch is CancellationError {
      guard preferenceAttemptID == attemptID else { return }
      preferences = previous
    } catch {
      guard preferenceAttemptID == attemptID else {
        await resynchronizeAfterInvalidatedPreferenceAttempt(from: currentIdentity)
        return
      }
      preferences = previous
      preferenceErrorMessage = "更新失败，已恢复服务端上次确认的设置。\(localized(error, fallback: "请稍后重试"))"
    }
  }

  /// Clears local token and route state before making the best-effort mutation.
  func unregisterBeforeLogout() async {
    let unregisterRepository = repository
    let unregisterMetadata = cachedMetadata
    let shouldUnregister = identity?.isAuthorizedUniqueAccount == true
      && unregisterRepository != nil
      && unregisterMetadata != nil
    clearSessionSensitiveState(stopRemoteRegistration: true)
    guard
      shouldUnregister,
      let unregisterRepository,
      let unregisterMetadata
    else {
      return
    }
    try? await unregisterRepository.unregister(metadata: unregisterMetadata)
  }

  func clearSession() {
    clearSessionSensitiveState(stopRemoteRegistration: true)
  }

  private func registerIfReady() async {
    guard registrationAttemptID == nil else { return }
    guard let desiredKey = desiredRegistrationKey() else {
      updateWaitingState()
      return
    }
    if desiredKey == registeredKey {
      return
    }
    guard let repository else {
      updateWaitingState()
      return
    }

    let attemptID = UUID()
    registrationAttemptID = attemptID
    registrationState = .registering
    do {
      let snapshot = try await repository.register(
        deviceToken: desiredKey.deviceToken,
        metadata: desiredKey.metadata
      )
      guard
        registrationAttemptID == attemptID,
        desiredRegistrationKey() == desiredKey,
        snapshot.metadata == desiredKey.metadata
      else {
        if registrationAttemptID == attemptID {
          registrationAttemptID = nil
          await registerIfReady()
        }
        return
      }
      registeredKey = desiredKey
      preferences = snapshot.preferences
      preferenceErrorMessage = nil
      registrationState = .registered(updatedAt: snapshot.updatedAt)
      registrationAttemptID = nil
    } catch is CancellationError {
      if registrationAttemptID == attemptID {
        registrationAttemptID = nil
        updateWaitingState()
      }
    } catch PushNotificationRepositoryError.unauthenticated {
      if registrationAttemptID == attemptID {
        registrationAttemptID = nil
      }
      do {
        try await sessionRefresh?()
        if desiredRegistrationKey() != desiredKey {
          await registerIfReady()
        } else {
          registrationState = .unavailable("登录会话已失效，请重新登录")
        }
      } catch {
        registrationState = .unavailable("登录会话已失效，请重新登录")
      }
    } catch {
      if registrationAttemptID == attemptID {
        registrationAttemptID = nil
        registrationState = .unavailable(localized(error, fallback: "通知设备注册失败"))
      }
    }

    if registrationAttemptID == nil,
      let currentKey = desiredRegistrationKey(),
      currentKey != desiredKey,
      currentKey != registeredKey
    {
      await registerIfReady()
    }
  }

  private func resolvePendingIfReady() async {
    guard routeAttemptID == nil, let eventID = pendingEventID else { return }
    guard localSessionUnlocked else {
      deepLinkState = .waitingForUnlock
      return
    }
    guard
      let identity,
      identity.isAuthorizedUniqueAccount,
      let repository
    else {
      return
    }

    let attemptID = UUID()
    let contextID = identity.contextID
    routeAttemptID = attemptID
    deepLinkState = .resolving
    do {
      let resolution = try await repository.resolve(eventID: eventID)
      guard
        routeAttemptID == attemptID,
        pendingEventID == eventID,
        self.identity?.contextID == contextID
      else {
        return
      }
      routeAttemptID = nil
      pendingEventID = nil
      routeRefreshAttemptedEventID = nil
      guard let resolution else {
        deepLinkState = .unavailable("这条通知已失效，或不属于当前账户与设备会话")
        return
      }
      guard !resolution.expired, resolution.expiresAt > now() else {
        deepLinkState = .unavailable("这条通知已经过期，请从当前业务页面查看最新状态")
        return
      }
      navigationRequest = NotificationNavigationRequest(
        eventID: resolution.eventID,
        destination: resolution.route.destination
      )
      deepLinkState = .idle
    } catch is CancellationError {
      if routeAttemptID == attemptID {
        routeAttemptID = nil
        deepLinkState = .idle
      }
    } catch PushNotificationRepositoryError.unauthenticated {
      guard
        routeAttemptID == attemptID,
        pendingEventID == eventID,
        self.identity?.contextID == contextID
      else {
        return
      }
      routeAttemptID = nil
      guard routeRefreshAttemptedEventID != eventID, let sessionRefresh else {
        pendingEventID = nil
        routeRefreshAttemptedEventID = nil
        deepLinkState = .unavailable("登录会话已失效，未打开通知目标")
        return
      }
      routeRefreshAttemptedEventID = eventID
      do {
        try await sessionRefresh()
        if pendingEventID == eventID {
          await resolvePendingIfReady()
        }
      } catch {
        if pendingEventID == eventID {
          pendingEventID = nil
          routeRefreshAttemptedEventID = nil
          deepLinkState = .unavailable("登录会话刷新失败，未打开通知目标")
        }
      }
    } catch {
      if routeAttemptID == attemptID {
        routeAttemptID = nil
        pendingEventID = nil
        routeRefreshAttemptedEventID = nil
        deepLinkState = .unavailable(localized(error, fallback: "无法解析这条通知"))
      }
    }
  }

  private func metadata() async throws -> PushDeviceMetadata {
    if let cachedMetadata {
      return cachedMetadata
    }
    guard let runtime else {
      throw PushNotificationRepositoryError.contextMismatch
    }
    let value = PushDeviceMetadata(
      installationID: try await installationStore.loadOrCreate(),
      runtime: runtime
    )
    cachedMetadata = value
    return value
  }

  private func desiredRegistrationKey() -> RegistrationKey? {
    guard
      authorizationStatus.permitsRemoteRegistration,
      let identity,
      identity.isAuthorizedUniqueAccount,
      repository != nil,
      let deviceToken,
      let cachedMetadata
    else {
      return nil
    }
    return RegistrationKey(
      sessionContextID: identity.contextID,
      deviceToken: deviceToken,
      metadata: cachedMetadata
    )
  }

  private func updateWaitingState() {
    guard runtime != nil else {
      registrationState = .unavailable("通知运行配置无效")
      return
    }
    guard authorizationStatus.permitsRemoteRegistration else {
      registrationState = .waitingForAuthorization
      return
    }
    guard let identity, identity.isAuthorizedUniqueAccount, repository != nil else {
      registrationState = .waitingForSession
      return
    }
    guard deviceToken != nil else {
      registrationState = .waitingForToken
      return
    }
    if registeredKey == desiredRegistrationKey(), let registeredKey {
      if case .registered = registrationState { return }
      registrationState = .registered(updatedAt: now())
      _ = registeredKey
      return
    }
    registrationState = .idle
  }

  private func clearSessionSensitiveState(stopRemoteRegistration: Bool) {
    identity = nil
    repository = nil
    deviceToken = nil
    registeredKey = nil
    registrationAttemptID = nil
    pendingEventID = nil
    routeAttemptID = nil
    routeRefreshAttemptedEventID = nil
    preferences = nil
    preferenceUpdateInProgress = false
    preferenceAttemptID = nil
    preferenceErrorMessage = nil
    deepLinkState = .idle
    navigationRequest = nil
    localSessionUnlocked = false
    registrationState = authorizationStatus.permitsRemoteRegistration
      ? .waitingForSession
      : .waitingForAuthorization
    if stopRemoteRegistration {
      system.unregisterForRemoteNotifications()
      PushNotificationBridge.shared.clearBufferedSensitiveState()
    }
  }

  private static func samePrincipal(
    _ lhs: PushNotificationSessionIdentity,
    _ rhs: PushNotificationSessionIdentity
  ) -> Bool {
    lhs.userID == rhs.userID
      && lhs.deviceSessionID == rhs.deviceSessionID
      && lhs.activeAccountID == rhs.activeAccountID
  }

  private func resynchronizeAfterInvalidatedPreferenceAttempt(
    from previousIdentity: PushNotificationSessionIdentity
  ) async {
    guard
      let currentIdentity = identity,
      Self.samePrincipal(previousIdentity, currentIdentity),
      currentIdentity.isAuthorizedUniqueAccount
    else {
      return
    }
    registeredKey = nil
    preferences = nil
    preferenceErrorMessage = nil
    updateWaitingState()
    await registerIfReady()
  }

  private func localized(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }
}

@MainActor
private final class DisabledPushNotificationSystem: PushNotificationSystemManaging {
  func authorizationStatus() async -> PushAuthorizationStatus { .unknown }
  func requestAuthorization() async throws -> Bool { false }
  func registerForRemoteNotifications() {}
  func unregisterForRemoteNotifications() {}
  func openNotificationSettings() async {}
}

private actor DisabledDeviceInstallIDStore: DeviceInstallIDStoring {
  func loadOrCreate() async throws -> UUID { UUID() }
}
