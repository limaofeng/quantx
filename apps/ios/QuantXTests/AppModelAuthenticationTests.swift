import Foundation
import XCTest

@testable import QuantX

@MainActor
final class AppModelAuthenticationTests: XCTestCase {
  func testRestoreWithoutStoredTokensEndsSignedOut() async throws {
    let store = MemorySessionTokenStore(tokens: nil)
    let model = makeModel(tokenStore: store)

    await model.restoreSession(requireLocalUnlock: false)

    let deleteCount = await store.deleteCount()
    XCTAssertEqual(model.authenticationState, .signedOut)
    XCTAssertEqual(deleteCount, 0)
  }

  func testExpiredRefreshTokenIsDeletedBeforeNetworkAccess() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens(refreshExpiresIn: -1))
    let session = SessionServiceStub(currentBehavior: .unexpectedCall)
    let model = makeModel(tokenStore: store, sessionClient: session)

    await model.restoreSession(requireLocalUnlock: false)

    let deleteCount = await store.deleteCount()
    let currentCallCount = await session.currentCallCount()
    XCTAssertEqual(model.authenticationState, .signedOut)
    XCTAssertEqual(deleteCount, 1)
    XCTAssertEqual(currentCallCount, 0)
  }

  func testServerUnauthenticatedDeletesLocalSessionAndSignsOut() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(
      currentBehavior: .failure(
        .server(
          code: "UNAUTHENTICATED",
          message: "会话已失效",
          requestID: "request-id",
          retryable: false
        )
      )
    )
    let model = makeModel(tokenStore: store, sessionClient: session)

    await model.restoreSession(requireLocalUnlock: false)

    let deleteCount = await store.deleteCount()
    XCTAssertEqual(model.authenticationState, .signedOut)
    XCTAssertEqual(deleteCount, 1)
  }

  func testTemporaryAuthenticationFailurePreservesTokensForRetry() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(
      currentBehavior: .failure(
        .server(
          code: "TEMPORARY_UNAVAILABLE",
          message: "认证服务暂时不可用",
          requestID: "request-id",
          retryable: true
        )
      )
    )
    let model = makeModel(tokenStore: store, sessionClient: session)

    await model.restoreSession(requireLocalUnlock: false)

    let deleteCount = await store.deleteCount()
    let storedTokens = await store.load()
    XCTAssertEqual(model.authenticationState, .failed("认证服务暂时不可用"))
    XCTAssertEqual(deleteCount, 0)
    XCTAssertNotNil(storedTokens)
  }

  func testInvalidNativeAccountContextClearsStoredSession() async throws {
    let invalidUser = SessionUser(
      id: "user-id",
      username: "ios-user",
      displayName: "iOS 用户",
      permissions: ["portfolio:read"],
      authorizedAccountIDs: ["account-id", "other-account"]
    )
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let model = makeModel(
      tokenStore: store,
      sessionClient: SessionServiceStub(currentBehavior: .user(invalidUser))
    )

    await model.restoreSession(requireLocalUnlock: false)

    let storedTokens = await store.load()
    let deleteCount = await store.deleteCount()
    XCTAssertEqual(
      model.authenticationState,
      .failed("认证服务返回了无法识别的响应")
    )
    XCTAssertNil(storedTokens)
    XCTAssertEqual(deleteCount, 1)
  }

  func testLocalUnlockFailureKeepsSessionLockedWithoutNetworkAccess() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(currentBehavior: .unexpectedCall)
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      localAuthentication: LocalAuthenticationStub(result: .failure)
    )

    await model.restoreSession(requireLocalUnlock: true)

    let currentCallCount = await session.currentCallCount()
    let deleteCount = await store.deleteCount()
    XCTAssertTrue(model.localSessionLocked)
    XCTAssertTrue(model.requiresLocalUnlock)
    XCTAssertEqual(model.localUnlockErrorMessage, "用户未完成本地解锁")
    XCTAssertEqual(currentCallCount, 0)
    XCTAssertEqual(deleteCount, 0)
  }

  func testValidAccessTokenRestoresAuthenticatedSessionAndPortfolioState() async throws {
    let tokens = makeTokens()
    let store = MemorySessionTokenStore(tokens: tokens)
    let session = SessionServiceStub(currentBehavior: .user(Self.user))
    let fetchedAt = Date(timeIntervalSince1970: 1_800_000_000)
    let loader = PortfolioLoaderStub(result: .noAccount(fetchedAt: fetchedAt))
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      portfolioLoader: loader
    )

    await model.restoreSession(requireLocalUnlock: false)

    let currentCallCount = await session.currentCallCount()
    let refreshCallCount = await session.refreshCallCount()
    XCTAssertEqual(model.authenticationState, .authenticated(Self.user))
    XCTAssertEqual(model.portfolioState, .noAccount(fetchedAt: fetchedAt))
    XCTAssertEqual(currentCallCount, 1)
    XCTAssertEqual(refreshCallCount, 0)
    XCTAssertEqual(loader.loadCount, 1)
    XCTAssertEqual(
      model.tTradeAssistantState,
      .unavailable("当前会话没有 strategy:read 权限")
    )
    XCTAssertEqual(
      model.limitUpBoardState,
      .unavailable("当前会话没有 strategy:read 权限")
    )
  }

  func testExpiredAccessTokenRotatesAndPersistsNewSession() async throws {
    let oldTokens = makeTokens(accessExpiresIn: -1)
    let newTokens = makeTokens(accessExpiresIn: 600, refreshExpiresIn: 7_200)
    let refreshedSession = AuthenticatedSession(tokens: newTokens, user: Self.user)
    let store = MemorySessionTokenStore(tokens: oldTokens)
    let session = SessionServiceStub(
      currentBehavior: .user(Self.user),
      refreshBehavior: .session(refreshedSession)
    )
    let loader = PortfolioLoaderStub(result: .noAccount(fetchedAt: Date()))
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      portfolioLoader: loader
    )

    await model.restoreSession(requireLocalUnlock: false)

    let storedTokens = await store.load()
    let refreshCallCount = await session.refreshCallCount()
    let currentCallCount = await session.currentCallCount()
    XCTAssertEqual(model.authenticationState, .authenticated(Self.user))
    XCTAssertEqual(storedTokens, newTokens)
    XCTAssertEqual(refreshCallCount, 1)
    XCTAssertEqual(currentCallCount, 1)
  }

  func testScopeShrinkKeepsIdentitySessionAndImmediatelyClosesPortfolio() async throws {
    let fullUser = SessionUser(
      id: "user-id",
      username: "ios-user",
      displayName: "iOS 用户",
      permissions: ["portfolio:read", "trade:manual"],
      authorizedAccountIDs: ["account-id"]
    )
    let reducedUser = SessionUser(
      id: "user-id",
      username: "ios-user",
      displayName: "iOS 用户",
      permissions: [],
      authorizedAccountIDs: ["account-id"]
    )
    let refreshedTokens = makeTokens(accessExpiresIn: 1_200, refreshExpiresIn: 7_200)
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(
      currentBehavior: .users([fullUser, reducedUser]),
      refreshBehavior: .session(
        AuthenticatedSession(tokens: refreshedTokens, user: reducedUser)
      )
    )
    let loader = PortfolioSequenceLoader(
      results: [
        .success(.noAccount(fetchedAt: Date())),
        .failure(.unauthenticated),
      ]
    )
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      portfolioLoader: loader
    )
    await model.restoreSession(requireLocalUnlock: false)

    await model.refreshPortfolio()

    let refreshCallCount = await session.refreshCallCount()
    let currentCallCount = await session.currentCallCount()
    XCTAssertEqual(model.authenticationState, .authenticated(reducedUser))
    XCTAssertEqual(
      model.portfolioState,
      .unavailable("当前会话没有 portfolio:read 权限")
    )
    XCTAssertFalse(model.canPlaceManualOrders)
    XCTAssertEqual(refreshCallCount, 1)
    XCTAssertEqual(currentCallCount, 2)
  }

  func testRemoteLogoutFailureStillClearsLocalSensitiveState() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(
      currentBehavior: .user(Self.user),
      logoutBehavior: .failure(
        .server(
          code: "HTTP_503",
          message: "认证服务暂时不可用",
          requestID: "request-id",
          retryable: true
        )
      )
    )
    let loader = PortfolioLoaderStub(result: .noAccount(fetchedAt: Date()))
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      portfolioLoader: loader
    )
    await model.restoreSession(requireLocalUnlock: false)

    await model.logout()

    let storedTokens = await store.load()
    let deleteCount = await store.deleteCount()
    let logoutCallCount = await session.logoutCallCount()
    XCTAssertEqual(model.authenticationState, .signedOut)
    XCTAssertEqual(model.portfolioState, .idle)
    XCTAssertNil(storedTokens)
    XCTAssertEqual(deleteCount, 1)
    XCTAssertEqual(logoutCallCount, 1)
  }

  func testInactiveSceneImmediatelyShieldsAndLocksAuthenticatedSession() async throws {
    let store = MemorySessionTokenStore(tokens: makeTokens())
    let session = SessionServiceStub(currentBehavior: .user(Self.user))
    let loader = PortfolioLoaderStub(result: .noAccount(fetchedAt: Date()))
    let model = makeModel(
      tokenStore: store,
      sessionClient: session,
      portfolioLoader: loader
    )
    await model.restoreSession(requireLocalUnlock: false)
    XCTAssertFalse(model.privacyShieldVisible)
    XCTAssertFalse(model.localSessionLocked)

    model.handleScenePhase(.inactive)

    XCTAssertTrue(model.privacyShieldVisible)
    XCTAssertTrue(model.localSessionLocked)
    XCTAssertTrue(model.requiresLocalUnlock)
  }

  private func makeModel(
    tokenStore: any SessionTokenStore,
    sessionClient: (any SessionServing)? = SessionServiceStub(
      currentBehavior: .unexpectedCall
    ),
    localAuthentication: any LocalAuthenticationProviding = LocalAuthenticationStub(
      result: .success
    ),
    portfolioLoader: (any PortfolioLoading)? = nil
  ) -> AppModel {
    let loader =
      portfolioLoader
      ?? PortfolioLoaderStub(
        result: .noAccount(fetchedAt: Date(timeIntervalSince1970: 0))
      )
    return AppModel(
      configuration: Self.configuration,
      sessionClient: sessionClient,
      tokenStore: tokenStore,
      localAuthentication: localAuthentication,
      portfolioLoaderFactory: { _ in loader }
    )
  }

  private func makeTokens(
    accessExpiresIn: TimeInterval = 600,
    refreshExpiresIn: TimeInterval = 3_600
  ) -> SessionTokens {
    SessionTokens(
      accessToken: "access-token",
      refreshToken: "refresh-token",
      accessTokenExpiresAt: Date().addingTimeInterval(accessExpiresIn),
      refreshTokenExpiresAt: Date().addingTimeInterval(refreshExpiresIn),
      deviceSessionID: "device-session-id"
    )
  }

  private static let configuration = APIConfiguration(
    environment: .staging,
    graphQLHTTPURL: URL(string: "https://quantx.test/graphql")!,
    graphQLWebSocketURL: URL(string: "wss://quantx.test/graphql")!,
    healthURL: URL(string: "https://quantx.test/health")!,
    authBaseURL: URL(string: "https://quantx.test")!,
    accountDataEnabled: true
  )

  private static let user = SessionUser(
    id: "user-id",
    username: "ios-user",
    displayName: "iOS 用户",
    permissions: ["portfolio:read"],
    authorizedAccountIDs: ["account-id"]
  )
}

private actor MemorySessionTokenStore: SessionTokenStore {
  private var tokens: SessionTokens?
  private var deletions = 0

  init(tokens: SessionTokens?) {
    self.tokens = tokens
  }

  func load() -> SessionTokens? {
    tokens
  }

  func save(_ tokens: SessionTokens) {
    self.tokens = tokens
  }

  func delete() {
    tokens = nil
    deletions += 1
  }

  func deleteCount() -> Int {
    deletions
  }
}

private actor SessionServiceStub: SessionServing {
  enum CurrentBehavior: Sendable {
    case user(SessionUser)
    case users([SessionUser])
    case failure(SessionClient.ClientError)
    case unexpectedCall
  }

  enum RefreshBehavior: Sendable {
    case session(AuthenticatedSession)
    case failure(SessionClient.ClientError)
    case unexpectedCall
  }

  enum LogoutBehavior: Sendable {
    case success
    case failure(SessionClient.ClientError)
  }

  private var currentBehavior: CurrentBehavior
  private let refreshBehavior: RefreshBehavior
  private let logoutBehavior: LogoutBehavior
  private var currentCalls = 0
  private var refreshCalls = 0
  private var logoutCalls = 0

  init(
    currentBehavior: CurrentBehavior,
    refreshBehavior: RefreshBehavior = .unexpectedCall,
    logoutBehavior: LogoutBehavior = .success
  ) {
    self.currentBehavior = currentBehavior
    self.refreshBehavior = refreshBehavior
    self.logoutBehavior = logoutBehavior
  }

  func login(
    username _: String,
    password _: String,
    deviceName _: String
  ) throws -> AuthenticatedSession {
    throw SessionClient.ClientError.invalidResponse
  }

  func refresh(refreshToken _: String) throws -> AuthenticatedSession {
    refreshCalls += 1
    switch refreshBehavior {
    case .session(let session):
      return session
    case .failure(let error):
      throw error
    case .unexpectedCall:
      throw SessionClient.ClientError.invalidResponse
    }
  }

  func current(accessToken _: String) throws -> SessionUser {
    currentCalls += 1
    switch currentBehavior {
    case .user(let user):
      return user
    case .users(var users):
      guard !users.isEmpty else {
        throw SessionClient.ClientError.invalidResponse
      }
      let user = users.removeFirst()
      currentBehavior = .users(users)
      return user
    case .failure(let error):
      throw error
    case .unexpectedCall:
      throw SessionClient.ClientError.invalidResponse
    }
  }

  func logout(accessToken _: String, allDevices _: Bool) throws {
    logoutCalls += 1
    if case .failure(let error) = logoutBehavior {
      throw error
    }
  }

  func currentCallCount() -> Int {
    currentCalls
  }

  func refreshCallCount() -> Int {
    refreshCalls
  }

  func logoutCallCount() -> Int {
    logoutCalls
  }
}

@MainActor
private final class PortfolioSequenceLoader: PortfolioLoading {
  enum Failure: Error {
    case unauthenticated
  }

  private var results: [Result<PortfolioLoadResult, Failure>]

  init(results: [Result<PortfolioLoadResult, Failure>]) {
    self.results = results
  }

  func load(authorizedAccountIDs _: Set<String>) async throws -> PortfolioLoadResult {
    guard !results.isEmpty else {
      throw PortfolioRepository.RepositoryError.unauthenticated
    }
    switch results.removeFirst() {
    case .success(let result):
      return result
    case .failure:
      throw PortfolioRepository.RepositoryError.unauthenticated
    }
  }
}

@MainActor
private final class PortfolioLoaderStub: PortfolioLoading {
  private let result: PortfolioLoadResult
  private(set) var loadCount = 0

  init(result: PortfolioLoadResult) {
    self.result = result
  }

  func load(authorizedAccountIDs _: Set<String>) async throws -> PortfolioLoadResult {
    loadCount += 1
    return result
  }
}

@MainActor
private final class LocalAuthenticationStub: LocalAuthenticationProviding {
  enum Result {
    case success
    case failure
  }

  struct UnlockError: LocalizedError {
    var errorDescription: String? { "用户未完成本地解锁" }
  }

  private let result: Result

  init(result: Result) {
    self.result = result
  }

  func unlock(reason _: String) async throws {
    if case .failure = result {
      throw UnlockError()
    }
  }
}
