import Foundation
import XCTest

@testable import QuantX

@MainActor
final class PushNotificationStoreTests: XCTestCase {
  func testStartupReadsAuthorizationWithoutRequestingPermission() async {
    let system = PushSystemStub(status: .notDetermined)
    let store = makeStore(system: system)

    await store.prepareSystemState()

    XCTAssertEqual(store.authorizationStatus, .notDetermined)
    XCTAssertEqual(system.authorizationRequestCount, 0)
    XCTAssertEqual(system.remoteRegistrationCount, 0)
    XCTAssertEqual(store.registrationState, .waitingForAuthorization)
  }

  func testExplicitEnableIsOnlyPathThatRequestsPermission() async {
    let system = PushSystemStub(status: .notDetermined)
    system.statusAfterRequest = .authorized
    let store = makeStore(system: system)
    await store.prepareSystemState()

    await store.enableNotifications()

    XCTAssertEqual(system.authorizationRequestCount, 1)
    XCTAssertEqual(system.remoteRegistrationCount, 1)
    XCTAssertEqual(store.authorizationStatus, .authorized)
    XCTAssertEqual(store.registrationState, .waitingForSession)
  }

  func testTokenFirstAndSessionFirstBothRegisterExactlyOnce() async {
    let tokenFirstSystem = PushSystemStub(status: .authorized)
    let tokenFirstRepository = PushRepositoryStub()
    let tokenFirstStore = makeStore(system: tokenFirstSystem)
    await tokenFirstStore.prepareSystemState()
    await tokenFirstStore.receive(deviceToken: "0011aaff")
    XCTAssertEqual(tokenFirstRepository.registerCalls.count, 0)

    await tokenFirstStore.activate(
      identity: identity(),
      repository: tokenFirstRepository,
      localSessionUnlocked: true
    )

    XCTAssertEqual(tokenFirstRepository.registerCalls.map(\.token), ["0011aaff"])
    XCTAssertEqual(tokenFirstStore.preferences, PushNotificationCategory.defaultPreferences)

    let sessionFirstSystem = PushSystemStub(status: .authorized)
    let sessionFirstRepository = PushRepositoryStub()
    let sessionFirstStore = makeStore(system: sessionFirstSystem)
    await sessionFirstStore.prepareSystemState()
    await sessionFirstStore.activate(
      identity: identity(),
      repository: sessionFirstRepository,
      localSessionUnlocked: true
    )
    XCTAssertEqual(sessionFirstRepository.registerCalls.count, 0)

    await sessionFirstStore.receive(deviceToken: "abcd")

    XCTAssertEqual(sessionFirstRepository.registerCalls.map(\.token), ["abcd"])
  }

  func testTokenRotationReregistersWithoutLengthAssumption() async {
    let repository = PushRepositoryStub()
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.prepareSystemState()
    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: true
    )

    await store.receive(deviceToken: "aa")
    await store.receive(deviceToken: String(repeating: "bc", count: 4_097))

    XCTAssertEqual(repository.registerCalls.count, 2)
    XCTAssertEqual(repository.registerCalls[0].token, "aa")
    XCTAssertEqual(repository.registerCalls[1].token.count, 8_194)
  }

  func testSamePrincipalAccessRefreshPreservesServerPreferencesAndDoesNotReregister() async {
    let firstRepository = PushRepositoryStub()
    firstRepository.registerPreferences = preferences(connectionData: true)
    let refreshedRepository = PushRepositoryStub()
    refreshedRepository.routeResult = route(destination: .systemStatus)
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.prepareSystemState()
    await store.receive(deviceToken: "abcd")
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: firstRepository,
      localSessionUnlocked: true
    )
    let serverTruth = store.preferences

    await store.activate(
      identity: identity(contextID: UUID()),
      repository: refreshedRepository,
      localSessionUnlocked: true
    )

    XCTAssertEqual(firstRepository.registerCalls.count, 1)
    XCTAssertEqual(refreshedRepository.registerCalls.count, 0)
    XCTAssertEqual(store.preferences, serverTruth)

    await store.receive(notificationEventID: refreshedRepository.routeResult!.eventID)
    XCTAssertEqual(firstRepository.resolveCalls.count, 0)
    XCTAssertEqual(refreshedRepository.resolveCalls.count, 1)
    XCTAssertEqual(store.navigationRequest?.destination, .systemStatus)
  }

  func testSessionSwitchClearsTokenPendingRouteAndServerPreferences() async {
    let firstRepository = PushRepositoryStub()
    let secondRepository = PushRepositoryStub()
    let system = PushSystemStub(status: .authorized)
    let store = makeStore(system: system)
    await store.prepareSystemState()
    await store.receive(deviceToken: "abcd")
    await store.activate(
      identity: identity(userID: "first-user"),
      repository: firstRepository,
      localSessionUnlocked: true
    )
    store.lockLocalSession()
    await store.receive(notificationEventID: UUID())

    await store.activate(
      identity: identity(userID: "second-user", deviceSessionID: "second-session"),
      repository: secondRepository,
      localSessionUnlocked: false
    )
    await store.setLocalSessionUnlocked(true)

    XCTAssertNil(store.preferences)
    XCTAssertNil(store.navigationRequest)
    XCTAssertEqual(secondRepository.resolveCalls.count, 0)
    XCTAssertEqual(secondRepository.registerCalls.count, 0)
    XCTAssertGreaterThanOrEqual(system.remoteUnregistrationCount, 1)
    XCTAssertEqual(store.registrationState, .waitingForToken)
  }

  func testPreferenceUpdateRollsBackWhenServerRejectsUpdate() async {
    let repository = PushRepositoryStub()
    repository.updateError = .transport
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await register(store: store, repository: repository)
    let before = store.preferences

    await store.updatePreference(.orderUpdate, enabled: false)

    XCTAssertEqual(store.preferences, before)
    XCTAssertEqual(repository.updateCalls.count, 1)
    XCTAssertNotNil(store.preferenceErrorMessage)
  }

  func testLogoutClearsLocalStateBeforeBestEffortUnregisterFailure() async {
    let repository = PushRepositoryStub()
    repository.unregisterError = .transport
    let system = PushSystemStub(status: .authorized)
    let store = makeStore(system: system)
    await register(store: store, repository: repository)
    let eventID = UUID()
    repository.routeResult = route(eventID: eventID, destination: .todayAction)
    await store.receive(notificationEventID: eventID)
    XCTAssertNotNil(store.navigationRequest)

    await store.unregisterBeforeLogout()

    XCTAssertEqual(repository.unregisterCalls.count, 1)
    XCTAssertNil(store.preferences)
    XCTAssertNil(store.navigationRequest)
    XCTAssertEqual(store.deepLinkState, .idle)
    XCTAssertEqual(store.registrationState, .waitingForSession)
    XCTAssertGreaterThanOrEqual(system.remoteUnregistrationCount, 1)

    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: true
    )
    XCTAssertEqual(repository.registerCalls.count, 1, "登出已清 token，不能复用旧 token 注册")
  }

  func testDeepLinkWaitsForLocalUnlockThenUsesResolverRoute() async throws {
    let repository = PushRepositoryStub()
    let eventID = UUID()
    repository.routeResult = route(eventID: eventID, destination: .quantWorkspace)
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: false
    )

    let parsedEventID = try PushNotificationPayloadParser.eventID(
      from: [
        "eventId": eventID.uuidString,
        "category": "ORDER_UPDATE",
        "route": "trading.orders",
      ]
    )
    await store.receive(notificationEventID: parsedEventID)

    XCTAssertEqual(repository.resolveCalls.count, 0)
    XCTAssertEqual(store.deepLinkState, .waitingForUnlock)

    await store.setLocalSessionUnlocked(true)

    XCTAssertEqual(repository.resolveCalls, [eventID])
    XCTAssertEqual(
      store.navigationRequest?.destination,
      .quant,
      "payload route 只做早期 allowlist，最终导航必须使用 resolver route"
    )
  }

  func testExpiredAndUnknownEventsBecomeExplicitlyUnavailable() async {
    let repository = PushRepositoryStub()
    let expiredID = UUID()
    repository.routeResult = route(
      eventID: expiredID,
      destination: .tradingOrders,
      expiresAt: Date(timeIntervalSince1970: 900),
      expired: false
    )
    let store = makeStore(
      system: PushSystemStub(status: .authorized),
      now: { Date(timeIntervalSince1970: 1_000) }
    )
    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: true
    )

    await store.receive(notificationEventID: expiredID)

    XCTAssertNil(store.navigationRequest)
    guard case .unavailable(let expiredMessage) = store.deepLinkState else {
      return XCTFail("过期事件必须显示不可操作状态")
    }
    XCTAssertTrue(expiredMessage.contains("过期"))

    store.dismissDeepLinkStatus()
    repository.routeResult = nil
    await store.receive(notificationEventID: UUID())
    guard case .unavailable(let unknownMessage) = store.deepLinkState else {
      return XCTFail("未知事件必须显示不可操作状态")
    }
    XCTAssertTrue(unknownMessage.contains("失效"))
    XCTAssertNil(store.navigationRequest)
  }

  func testMissingNotificationScopeNeverResolvesPendingEvent() async {
    let repository = PushRepositoryStub()
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.receive(notificationEventID: UUID())

    await store.activate(
      identity: identity(scopes: []),
      repository: repository,
      localSessionUnlocked: true
    )

    XCTAssertEqual(repository.resolveCalls.count, 0)
    XCTAssertNil(store.navigationRequest)
    guard case .unavailable(let message) = store.deepLinkState else {
      return XCTFail("无 notification:manage 必须显示不可用")
    }
    XCTAssertTrue(message.contains("notification:manage"))
  }

  func testClearDuringStructuredActivationCannotReviveOldRegistration() async {
    let repository = PushRepositoryStub()
    repository.blocksRegister = true
    let started = expectation(description: "register started")
    repository.registerStarted = started
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.prepareSystemState()
    await store.receive(deviceToken: "abcd")
    let probe = CompletionProbe()

    let activation = Task { @MainActor in
      await store.activate(
        identity: identity(),
        repository: repository,
        localSessionUnlocked: true
      )
      probe.completed = true
    }
    await fulfillment(of: [started], timeout: 1)
    XCTAssertFalse(probe.completed, "activate 必须等待注册收口，不能提前返回")

    store.clearSession()
    repository.releaseRegister()
    await activation.value

    XCTAssertTrue(probe.completed)
    XCTAssertNil(store.preferences)
    XCTAssertNil(store.navigationRequest)
    XCTAssertEqual(store.registrationState, .waitingForSession)
  }

  func testClearDuringRouteResolutionCannotNavigateOldContext() async {
    let repository = PushRepositoryStub()
    repository.blocksRoute = true
    let started = expectation(description: "route started")
    repository.routeStarted = started
    let eventID = UUID()
    repository.routeResult = route(eventID: eventID, destination: .tradingSafety)
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: true
    )

    let resolution = Task { @MainActor in
      await store.receive(notificationEventID: eventID)
    }
    await fulfillment(of: [started], timeout: 1)
    store.clearSession()
    repository.releaseRoute()
    await resolution.value

    XCTAssertNil(store.navigationRequest)
    XCTAssertEqual(store.deepLinkState, .idle)
  }

  func testSamePrincipalRefreshDuringRouteUsesOnlyNewRepositoryResult() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.blocksRoute = true
    let started = expectation(description: "old route started")
    oldRepository.routeStarted = started
    let eventID = UUID()
    oldRepository.routeResult = route(eventID: eventID, destination: .tradingSafety)
    let newRepository = PushRepositoryStub()
    newRepository.routeResult = route(eventID: eventID, destination: .quantWorkspace)
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    let oldResolution = Task { @MainActor in
      await store.receive(notificationEventID: eventID)
    }
    await fulfillment(of: [started], timeout: 1)

    await store.activate(
      identity: identity(contextID: UUID()),
      repository: newRepository,
      localSessionUnlocked: true
    )

    XCTAssertEqual(newRepository.resolveCalls, [eventID])
    XCTAssertEqual(store.navigationRequest?.destination, .quant)

    oldRepository.releaseRoute()
    await oldResolution.value

    XCTAssertEqual(oldRepository.resolveCalls, [eventID])
    XCTAssertEqual(newRepository.resolveCalls, [eventID])
    XCTAssertEqual(store.navigationRequest?.destination, .quant)
  }

  func testPreferenceResponseCrossingRefreshResynchronizesServerTruth() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.blocksUpdate = true
    let updateStarted = expectation(description: "preference update started")
    oldRepository.updateStarted = updateStarted
    let newRepository = PushRepositoryStub()
    newRepository.registerPreferenceSequence = [
      preferences(connectionData: false),
      preferences(connectionData: true),
    ]
    let store = makeStore(system: PushSystemStub(status: .authorized))
    await store.prepareSystemState()
    await store.receive(deviceToken: "abcd")
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    let update = Task { @MainActor in
      await store.updatePreference(.connectionData, enabled: true)
    }
    await fulfillment(of: [updateStarted], timeout: 1)

    await store.activate(
      identity: identity(contextID: UUID()),
      repository: newRepository,
      localSessionUnlocked: true
    )
    XCTAssertEqual(newRepository.registerCalls.count, 1)
    XCTAssertEqual(store.preferences?[.connectionData], false)

    oldRepository.releaseUpdate()
    await update.value

    XCTAssertEqual(newRepository.registerCalls.count, 2)
    XCTAssertEqual(store.preferences?[.connectionData], true)
    XCTAssertNil(store.preferenceErrorMessage)
  }

  func testUnauthenticatedRouteRefreshesOnceAndNewRepositoryResolvesEvent() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.resolveError = .unauthenticated
    let newRepository = PushRepositoryStub()
    let eventID = UUID()
    newRepository.routeResult = route(eventID: eventID, destination: .tradingOrders)
    let store = makeStore(system: PushSystemStub(status: .authorized))
    var refreshCount = 0
    store.configure(sessionRefresh: {
      refreshCount += 1
      await store.activate(
        identity: self.identity(contextID: UUID()),
        repository: newRepository,
        localSessionUnlocked: true
      )
    })
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    await store.receive(notificationEventID: eventID)

    XCTAssertEqual(refreshCount, 1)
    XCTAssertEqual(oldRepository.resolveCalls, [eventID])
    XCTAssertEqual(newRepository.resolveCalls, [eventID])
    XCTAssertEqual(store.navigationRequest?.destination, .tradingOrders)
  }

  func testRouteRefreshLosingScopeFailsClosedWithoutSecondRefresh() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.resolveError = .unauthenticated
    let newRepository = PushRepositoryStub()
    let eventID = UUID()
    let store = makeStore(system: PushSystemStub(status: .authorized))
    var refreshCount = 0
    store.configure(sessionRefresh: {
      refreshCount += 1
      await store.activate(
        identity: self.identity(contextID: UUID(), scopes: []),
        repository: newRepository,
        localSessionUnlocked: true
      )
    })
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    await store.receive(notificationEventID: eventID)

    XCTAssertEqual(refreshCount, 1)
    XCTAssertEqual(oldRepository.resolveCalls, [eventID])
    XCTAssertEqual(newRepository.resolveCalls.count, 0)
    XCTAssertNil(store.navigationRequest)
    guard case .unavailable(let message) = store.deepLinkState else {
      return XCTFail("scope 收缩后必须 fail-closed")
    }
    XCTAssertTrue(message.contains("notification:manage"))
  }

  func testRouteRefreshLosingAccountFailsClosedWithoutSecondRefresh() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.resolveError = .unauthenticated
    let newRepository = PushRepositoryStub()
    let eventID = UUID()
    let store = makeStore(system: PushSystemStub(status: .authorized))
    var refreshCount = 0
    store.configure(sessionRefresh: {
      refreshCount += 1
      await store.activate(
        identity: self.identity(contextID: UUID(), authorizedAccountIDs: []),
        repository: newRepository,
        localSessionUnlocked: true
      )
    })
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    await store.receive(notificationEventID: eventID)

    XCTAssertEqual(refreshCount, 1)
    XCTAssertEqual(oldRepository.resolveCalls, [eventID])
    XCTAssertEqual(newRepository.resolveCalls.count, 0)
    XCTAssertNil(store.navigationRequest)
    guard case .unavailable(let message) = store.deepLinkState else {
      return XCTFail("账户授权收缩后必须 fail-closed")
    }
    XCTAssertTrue(message.contains("唯一账户"))
  }

  func testRouteSecondUnauthenticatedResponseDoesNotRefreshAgain() async {
    let oldRepository = PushRepositoryStub()
    oldRepository.resolveError = .unauthenticated
    let refreshedRepository = PushRepositoryStub()
    refreshedRepository.resolveError = .unauthenticated
    let eventID = UUID()
    let store = makeStore(system: PushSystemStub(status: .authorized))
    var refreshCount = 0
    store.configure(sessionRefresh: {
      refreshCount += 1
      await store.activate(
        identity: self.identity(contextID: UUID()),
        repository: refreshedRepository,
        localSessionUnlocked: true
      )
    })
    await store.activate(
      identity: identity(contextID: UUID()),
      repository: oldRepository,
      localSessionUnlocked: true
    )

    await store.receive(notificationEventID: eventID)

    XCTAssertEqual(refreshCount, 1)
    XCTAssertEqual(oldRepository.resolveCalls, [eventID])
    XCTAssertEqual(refreshedRepository.resolveCalls, [eventID])
    XCTAssertNil(store.navigationRequest)
    guard case .unavailable(let message) = store.deepLinkState else {
      return XCTFail("二次 401 必须停止刷新并明确不可用")
    }
    XCTAssertTrue(message.contains("登录会话已失效"))
  }

  private func register(
    store: PushNotificationStore,
    repository: PushRepositoryStub
  ) async {
    await store.prepareSystemState()
    await store.receive(deviceToken: "abcd")
    await store.activate(
      identity: identity(),
      repository: repository,
      localSessionUnlocked: true
    )
  }

  private func makeStore(
    system: PushSystemStub,
    now: @escaping @Sendable () -> Date = { Date(timeIntervalSince1970: 1_000) }
  ) -> PushNotificationStore {
    PushNotificationStore(
      runtime: Self.runtime,
      system: system,
      installationStore: FixedInstallIDStore(identifier: Self.installationID),
      now: now
    )
  }

  private func identity(
    contextID: UUID = UUID(),
    userID: String = "user-id",
    deviceSessionID: String = "device-session-id",
    accountID: String = "account-id",
    authorizedAccountIDs: Set<String>? = nil,
    scopes: Set<String> = [NativeSessionScope.notificationManage.rawValue]
  ) -> PushNotificationSessionIdentity {
    PushNotificationSessionIdentity(
      contextID: contextID,
      userID: userID,
      deviceSessionID: deviceSessionID,
      activeAccountID: accountID,
      authorizedAccountIDs: authorizedAccountIDs ?? [accountID],
      grantedScopes: scopes
    )
  }

  private func preferences(connectionData: Bool = false) -> [PushNotificationCategory: Bool] {
    var result = PushNotificationCategory.defaultPreferences
    result[.connectionData] = connectionData
    return result
  }

  private func route(
    eventID: UUID = UUID(),
    destination: NotificationRouteType,
    expiresAt: Date = Date(timeIntervalSince1970: 2_000),
    expired: Bool = false
  ) -> NotificationRouteResolution {
    NotificationRouteResolution(
      eventID: eventID,
      category: .orderUpdate,
      route: destination,
      occurredAt: Date(timeIntervalSince1970: 500),
      expiresAt: expiresAt,
      expired: expired
    )
  }

  private static let installationID = UUID(uuidString: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")!
  private static let runtime = PushNotificationRuntimeConfiguration(
    appBundleID: "com.limaofeng.quantx",
    appVersion: "1.0 (1)",
    environment: .sandbox
  )
}

@MainActor
private final class PushSystemStub: PushNotificationSystemManaging {
  var status: PushAuthorizationStatus
  var statusAfterRequest: PushAuthorizationStatus?
  var authorizationRequestCount = 0
  var remoteRegistrationCount = 0
  var remoteUnregistrationCount = 0
  var settingsOpenCount = 0

  init(status: PushAuthorizationStatus) {
    self.status = status
  }

  func authorizationStatus() async -> PushAuthorizationStatus { status }

  func requestAuthorization() async throws -> Bool {
    authorizationRequestCount += 1
    if let statusAfterRequest {
      status = statusAfterRequest
    }
    return status.permitsRemoteRegistration
  }

  func registerForRemoteNotifications() {
    remoteRegistrationCount += 1
  }

  func unregisterForRemoteNotifications() {
    remoteUnregistrationCount += 1
  }

  func openNotificationSettings() async {
    settingsOpenCount += 1
  }
}

private actor FixedInstallIDStore: DeviceInstallIDStoring {
  let identifier: UUID

  init(identifier: UUID) {
    self.identifier = identifier
  }

  func loadOrCreate() async throws -> UUID { identifier }
}

@MainActor
private final class PushRepositoryStub: PushNotificationLoading {
  struct RegisterCall: Equatable {
    let token: String
    let metadata: PushDeviceMetadata
  }

  var registerCalls: [RegisterCall] = []
  var updateCalls: [[PushNotificationCategory: Bool]] = []
  var unregisterCalls: [PushDeviceMetadata] = []
  var resolveCalls: [UUID] = []
  var registerPreferences = PushNotificationCategory.defaultPreferences
  var registerPreferenceSequence: [[PushNotificationCategory: Bool]] = []
  var updateError: PushNotificationRepositoryError?
  var unregisterError: PushNotificationRepositoryError?
  var resolveError: PushNotificationRepositoryError?
  var routeResult: NotificationRouteResolution?
  var blocksRegister = false
  var blocksRoute = false
  var blocksUpdate = false
  var registerStarted: XCTestExpectation?
  var routeStarted: XCTestExpectation?
  var updateStarted: XCTestExpectation?
  private var registerContinuation: CheckedContinuation<PushRegistrationSnapshot, any Error>?
  private var routeContinuation: CheckedContinuation<NotificationRouteResolution?, any Error>?
  private var updateContinuation: CheckedContinuation<PushRegistrationSnapshot, any Error>?
  private var blockedUpdateMetadata: PushDeviceMetadata?
  private var blockedUpdatePreferences: [PushNotificationCategory: Bool]?

  func register(deviceToken: String, metadata: PushDeviceMetadata) async throws
    -> PushRegistrationSnapshot
  {
    registerCalls.append(RegisterCall(token: deviceToken, metadata: metadata))
    if blocksRegister {
      registerStarted?.fulfill()
      return try await withCheckedThrowingContinuation { continuation in
        registerContinuation = continuation
      }
    }
    let preferences =
      registerPreferenceSequence.isEmpty
      ? registerPreferences
      : registerPreferenceSequence.removeFirst()
    return snapshot(metadata: metadata, preferences: preferences)
  }

  func updatePreferences(
    _ preferences: [PushNotificationCategory: Bool],
    metadata: PushDeviceMetadata
  ) async throws -> PushRegistrationSnapshot {
    updateCalls.append(preferences)
    if let updateError { throw updateError }
    if blocksUpdate {
      blockedUpdateMetadata = metadata
      blockedUpdatePreferences = preferences
      updateStarted?.fulfill()
      return try await withCheckedThrowingContinuation { continuation in
        updateContinuation = continuation
      }
    }
    return snapshot(metadata: metadata, preferences: preferences)
  }

  func unregister(metadata: PushDeviceMetadata) async throws {
    unregisterCalls.append(metadata)
    if let unregisterError { throw unregisterError }
  }

  func resolve(eventID: UUID) async throws -> NotificationRouteResolution? {
    resolveCalls.append(eventID)
    if let resolveError { throw resolveError }
    if blocksRoute {
      routeStarted?.fulfill()
      return try await withCheckedThrowingContinuation { continuation in
        routeContinuation = continuation
      }
    }
    return routeResult
  }

  func releaseRegister() {
    let continuation = registerContinuation
    registerContinuation = nil
    guard let metadata = registerCalls.last?.metadata else {
      continuation?.resume(throwing: PushNotificationRepositoryError.invalidResponse)
      return
    }
    continuation?.resume(returning: snapshot(metadata: metadata, preferences: registerPreferences))
  }

  func releaseRoute() {
    let continuation = routeContinuation
    routeContinuation = nil
    continuation?.resume(returning: routeResult)
  }

  func releaseUpdate() {
    let continuation = updateContinuation
    updateContinuation = nil
    guard let metadata = blockedUpdateMetadata, let preferences = blockedUpdatePreferences else {
      continuation?.resume(throwing: PushNotificationRepositoryError.invalidResponse)
      return
    }
    blockedUpdateMetadata = nil
    blockedUpdatePreferences = nil
    continuation?.resume(returning: snapshot(metadata: metadata, preferences: preferences))
  }

  private func snapshot(
    metadata: PushDeviceMetadata,
    preferences: [PushNotificationCategory: Bool]
  ) -> PushRegistrationSnapshot {
    PushRegistrationSnapshot(
      id: UUID().uuidString.lowercased(),
      metadata: metadata,
      registeredAt: Date(timeIntervalSince1970: 900),
      updatedAt: Date(timeIntervalSince1970: 950),
      preferences: preferences
    )
  }
}

@MainActor
private final class CompletionProbe {
  var completed = false
}
