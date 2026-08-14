import SwiftUI
import UIKit

@MainActor
final class AppModel: ObservableObject {
  typealias ApolloSessionFactory = (APIConfiguration, String) throws -> ApolloSession
  typealias PortfolioLoaderFactory = (ApolloSession) -> any PortfolioLoading
  typealias StrategyLoaderFactory = (ApolloSession) -> any StrategyMonitoringLoading
  typealias TradingLoaderFactory = (ApolloSession) -> any TradingActivityLoading
  typealias TTradeAssistantLoaderFactory = (ApolloSession) -> any TTradeAssistantLoading
  typealias LimitUpBoardLoaderFactory = (ApolloSession) -> any LimitUpBoardLoading
  typealias TradeApprovalLoaderFactory = (ApolloSession) -> any TradeApprovalLoading
  typealias MarketLoaderFactory = (ApolloSession) -> any MarketDataLoading

  enum ServiceState: Equatable {
    case idle
    case loading
    case loaded(HealthSnapshot)
    case failed(String)
  }

  enum AuthenticationState: Equatable {
    case disabled(String)
    case signedOut
    case restoring
    case authenticating
    case authenticated(SessionUser)
    case failed(String)
  }

  @Published var selectedTab: AppTab = .today
  @Published private(set) var serviceState: ServiceState = .idle
  @Published private(set) var privacyShieldVisible = false
  @Published private(set) var authenticationState: AuthenticationState
  @Published private(set) var localSessionLocked = false
  @Published private(set) var localUnlockErrorMessage: String?
  @Published private(set) var portfolioState: PortfolioState = .idle
  @Published private(set) var portfolioRefreshInProgress = false
  @Published private(set) var strategyState: StrategyMonitorState = .idle
  @Published private(set) var strategyRefreshInProgress = false
  @Published private(set) var tradingState: TradingActivityState = .idle
  @Published private(set) var tradingRefreshInProgress = false
  @Published private(set) var tTradeAssistantState: TTradeAssistantState = .idle
  @Published private(set) var tTradeAssistantRefreshInProgress = false
  @Published private(set) var limitUpBoardState: LimitUpBoardState = .idle
  @Published private(set) var limitUpBoardRefreshInProgress = false
  @Published private(set) var tradeApprovalInProgress = false
  @Published private(set) var marketState: MarketWorkspaceState = .idle
  @Published private(set) var marketRefreshInProgress = false

  let configuration: APIConfiguration?
  let configurationErrorMessage: String?
  private let healthClient: (any HealthChecking)?
  private let sessionClient: (any SessionServing)?
  private let tokenStore: any SessionTokenStore
  private let localAuthentication: any LocalAuthenticationProviding
  private let apolloSessionFactory: ApolloSessionFactory
  private let portfolioLoaderFactory: PortfolioLoaderFactory
  private let strategyLoaderFactory: StrategyLoaderFactory
  private let tradingLoaderFactory: TradingLoaderFactory
  private let tTradeAssistantLoaderFactory: TTradeAssistantLoaderFactory
  private let limitUpBoardLoaderFactory: LimitUpBoardLoaderFactory
  private let tradeApprovalLoaderFactory: TradeApprovalLoaderFactory
  private let marketLoaderFactory: MarketLoaderFactory
  private var apolloSession: ApolloSession?
  private var portfolioRepository: (any PortfolioLoading)?
  private var strategyRepository: (any StrategyMonitoringLoading)?
  private var tradingRepository: (any TradingActivityLoading)?
  private var tTradeAssistantRepository: (any TTradeAssistantLoading)?
  private var limitUpBoardRepository: (any LimitUpBoardLoading)?
  private var tradeApprovalRepository: (any TradeApprovalLoading)?
  private var marketRepository: (any MarketDataLoading)?
#if DEBUG
  private let usesTransientRealBackendUITestSession =
    ProcessInfo.processInfo.arguments.contains(
      DebugRealBackendUITestSession.launchArgument
    )
#endif

  init(
    bundle: Bundle = .main,
    localAuthentication: any LocalAuthenticationProviding = LocalAuthenticationGate()
  ) {
    self.localAuthentication = localAuthentication
    apolloSessionFactory = { configuration, accessToken in
      try ApolloClientFactory.make(
        configuration: configuration,
        accessToken: accessToken
      )
    }
    portfolioLoaderFactory = { session in
      PortfolioRepository(client: session.client)
    }
    strategyLoaderFactory = { session in
      StrategyRepository(client: session.client)
    }
    tradingLoaderFactory = { session in
      TradingActivityRepository(client: session.client)
    }
    tTradeAssistantLoaderFactory = { session in
      TTradeAssistantRepository(client: session.client)
    }
    limitUpBoardLoaderFactory = { session in
      LimitUpBoardRepository(client: session.client)
    }
    tradeApprovalLoaderFactory = { session in
      TradeApprovalRepository(client: session.client)
    }
    marketLoaderFactory = { session in
      MarketRepository(client: session.client)
    }
    tokenStore = KeychainSessionTokenStore(
      service: bundle.bundleIdentifier ?? "com.limaofeng.quantx"
    )
    do {
      let configuration = try APIConfiguration.load(bundle: bundle)
      self.configuration = configuration
      configurationErrorMessage = nil
      healthClient = HealthClient(endpoint: configuration.healthURL)
      if configuration.accountDataEnabled {
        do {
          sessionClient = try SessionClient(
            baseURL: configuration.authBaseURL,
            allowsInsecureDevelopmentTransport: configuration.environment == .debug
          )
          authenticationState = .signedOut
        } catch {
          sessionClient = nil
          authenticationState = .disabled(error.localizedDescription)
          portfolioState = .unavailable("认证地址不满足安全传输要求")
          strategyState = .unavailable("认证地址不满足安全传输要求")
          tradingState = .unavailable("认证地址不满足安全传输要求")
          tTradeAssistantState = .unavailable("认证地址不满足安全传输要求")
          limitUpBoardState = .unavailable("认证地址不满足安全传输要求")
          marketState = .unavailable("认证地址不满足安全传输要求")
        }
      } else {
        sessionClient = nil
        authenticationState = .disabled("后端认证、TLS 与只读授权尚未完成验收")
        portfolioState = .unavailable("等待 TLS、认证与只读授权部署验收")
        strategyState = .unavailable("等待 TLS、认证与只读授权部署验收")
        tradingState = .unavailable("等待 TLS、认证与只读授权部署验收")
        tTradeAssistantState = .unavailable("等待 TLS、认证与只读授权部署验收")
        limitUpBoardState = .unavailable("等待 TLS、认证与只读授权部署验收")
        marketState = .unavailable("等待 TLS、认证与行情授权部署验收")
      }
    } catch {
      configuration = nil
      configurationErrorMessage = error.localizedDescription
      healthClient = nil
      sessionClient = nil
      authenticationState = .disabled("环境配置无效")
      portfolioState = .unavailable("环境配置无效，账户连接保持关闭")
      strategyState = .unavailable("环境配置无效，策略连接保持关闭")
      tradingState = .unavailable("环境配置无效，委托成交连接保持关闭")
      tTradeAssistantState = .unavailable("环境配置无效，做T助手连接保持关闭")
      limitUpBoardState = .unavailable("环境配置无效，打板助手连接保持关闭")
      marketState = .unavailable("环境配置无效，行情连接保持关闭")
    }
  }

  init(
    configuration: APIConfiguration,
    healthClient: (any HealthChecking)? = nil,
    sessionClient: (any SessionServing)?,
    tokenStore: any SessionTokenStore,
    localAuthentication: any LocalAuthenticationProviding,
    apolloSessionFactory: @escaping ApolloSessionFactory = { configuration, accessToken in
      try ApolloClientFactory.make(
        configuration: configuration,
        accessToken: accessToken
      )
    },
    portfolioLoaderFactory: @escaping PortfolioLoaderFactory = { session in
      PortfolioRepository(client: session.client)
    },
    strategyLoaderFactory: @escaping StrategyLoaderFactory = { session in
      StrategyRepository(client: session.client)
    },
    tradingLoaderFactory: @escaping TradingLoaderFactory = { session in
      TradingActivityRepository(client: session.client)
    },
    tTradeAssistantLoaderFactory: @escaping TTradeAssistantLoaderFactory = { session in
      TTradeAssistantRepository(client: session.client)
    },
    limitUpBoardLoaderFactory: @escaping LimitUpBoardLoaderFactory = { session in
      LimitUpBoardRepository(client: session.client)
    },
    tradeApprovalLoaderFactory: @escaping TradeApprovalLoaderFactory = { session in
      TradeApprovalRepository(client: session.client)
    },
    marketLoaderFactory: @escaping MarketLoaderFactory = { session in
      MarketRepository(client: session.client)
    }
  ) {
    self.configuration = configuration
    configurationErrorMessage = nil
    self.healthClient = healthClient
    self.sessionClient = sessionClient
    self.tokenStore = tokenStore
    self.localAuthentication = localAuthentication
    self.apolloSessionFactory = apolloSessionFactory
    self.portfolioLoaderFactory = portfolioLoaderFactory
    self.strategyLoaderFactory = strategyLoaderFactory
    self.tradingLoaderFactory = tradingLoaderFactory
    self.tTradeAssistantLoaderFactory = tTradeAssistantLoaderFactory
    self.limitUpBoardLoaderFactory = limitUpBoardLoaderFactory
    self.tradeApprovalLoaderFactory = tradeApprovalLoaderFactory
    self.marketLoaderFactory = marketLoaderFactory

    if configuration.accountDataEnabled, sessionClient != nil {
      authenticationState = .signedOut
      portfolioState = .idle
    } else if configuration.accountDataEnabled {
      authenticationState = .disabled("认证客户端不可用")
      portfolioState = .unavailable("认证客户端不可用")
      strategyState = .unavailable("认证客户端不可用")
      tradingState = .unavailable("认证客户端不可用")
      tTradeAssistantState = .unavailable("认证客户端不可用")
      limitUpBoardState = .unavailable("认证客户端不可用")
      marketState = .unavailable("认证客户端不可用")
    } else {
      authenticationState = .disabled("后端认证、TLS 与只读授权尚未完成验收")
      portfolioState = .unavailable("等待 TLS、认证与只读授权部署验收")
      strategyState = .unavailable("等待 TLS、认证与只读授权部署验收")
      tradingState = .unavailable("等待 TLS、认证与只读授权部署验收")
      tTradeAssistantState = .unavailable("等待 TLS、认证与只读授权部署验收")
      limitUpBoardState = .unavailable("等待 TLS、认证与只读授权部署验收")
      marketState = .unavailable("等待 TLS、认证与行情授权部署验收")
    }
  }

  var accountDataEnabled: Bool {
    configuration?.accountDataEnabled == true
  }

  var requiresAuthentication: Bool {
#if DEBUG
    if ProcessInfo.processInfo.arguments.contains("-QuantXUITesting") {
      return false
    }
#endif
    guard accountDataEnabled else { return false }
    return switch authenticationState {
    case .authenticated, .disabled:
      false
    case .signedOut, .restoring, .authenticating, .failed:
      true
    }
  }

  var authenticationIsBusy: Bool {
    switch authenticationState {
    case .restoring, .authenticating:
      true
    default:
      false
    }
  }

  var requiresLocalUnlock: Bool {
    accountDataEnabled && localSessionLocked
  }

  var authenticationErrorMessage: String? {
    if case .failed(let message) = authenticationState {
      return message
    }
    return nil
  }

  var authenticatedUser: SessionUser? {
    if case .authenticated(let user) = authenticationState {
      return user
    }
    return nil
  }

  func start() async {
#if DEBUG
    if usesTransientRealBackendUITestSession {
      await startTransientRealBackendUITestSession()
      return
    }
    if ProcessInfo.processInfo.arguments.contains("-QuantXUITesting") {
      serviceState = .failed("UI 测试未连接服务")
      portfolioState = .failed("UI 测试未连接账户服务")
      strategyState = .failed("UI 测试未连接策略服务")
      tradingState = .failed("UI 测试未连接委托成交服务")
      tTradeAssistantState = .failed("UI 测试未连接做T服务")
      limitUpBoardState = .failed("UI 测试未连接打板服务")
      marketState = .failed("UI 测试未连接行情服务")
      return
    }
#endif
    await refreshHealth()
    await restoreSession()
  }

  func refreshHealth() async {
    guard let healthClient else { return }
    serviceState = .loading
    do {
      serviceState = .loaded(try await healthClient.fetch())
    } catch is CancellationError {
      serviceState = .idle
    } catch {
      serviceState = .failed(error.localizedDescription)
    }
  }

  func restoreSession(requireLocalUnlock: Bool = true) async {
    guard let sessionClient, let configuration else { return }
    authenticationState = .restoring
    do {
      guard let tokens = try await tokenStore.load() else {
        localSessionLocked = false
        authenticationState = .signedOut
        return
      }
      guard tokens.refreshTokenExpiresAt > Date() else {
        try await tokenStore.delete()
        authenticationState = .signedOut
        return
      }

      if requireLocalUnlock {
        localSessionLocked = true
        do {
          try await localAuthentication.unlock(
            reason: "解锁 QuantX 个人量化会话"
          )
          localSessionLocked = false
          localUnlockErrorMessage = nil
        } catch {
          localUnlockErrorMessage = error.localizedDescription
          return
        }
      }

      let restored: AuthenticatedSession
      if tokens.accessTokenExpiresAt > Date().addingTimeInterval(30) {
        let user = try await sessionClient.current(accessToken: tokens.accessToken)
        restored = AuthenticatedSession(tokens: tokens, user: user)
      } else {
        restored = try await sessionClient.refresh(refreshToken: tokens.refreshToken)
      }
      try await activate(restored, configuration: configuration)
      await refreshAllReadOnlySnapshots()
    } catch {
      await handleAuthenticationFailure(error)
    }
  }

  func login(username: String, password: String) async {
    guard let sessionClient, let configuration else { return }
    authenticationState = .authenticating
    do {
      let session = try await sessionClient.login(
        username: username,
        password: password,
        deviceName: UIDevice.current.model
      )
      try await activate(session, configuration: configuration)
      await refreshAllReadOnlySnapshots()
    } catch {
      await handleAuthenticationFailure(error)
    }
  }

  func unlockLocalSession() async {
    do {
      try await localAuthentication.unlock(reason: "解锁 QuantX 个人量化会话")
      localSessionLocked = false
      localUnlockErrorMessage = nil
      if case .restoring = authenticationState {
        await restoreSession(requireLocalUnlock: false)
      } else {
        await resumeSessionAfterForeground()
      }
    } catch {
      localUnlockErrorMessage = error.localizedDescription
    }
  }

  func logout(allDevices: Bool = false) async {
    if let sessionClient,
      let tokens = try? await tokenStore.load()
    {
      try? await sessionClient.logout(
        accessToken: tokens.accessToken,
        allDevices: allDevices
      )
    }
    await clearLocalSession()
    authenticationState = .signedOut
  }

  func refreshPortfolio() async {
    guard accountDataEnabled else {
      portfolioState = .unavailable("等待 TLS、认证与只读授权部署验收")
      return
    }
    guard hasPermission("portfolio:read") else {
      portfolioState = .unavailable("当前会话没有 portfolio:read 权限")
      return
    }
    guard !portfolioRefreshInProgress else { return }
    guard !localSessionLocked,
      let repository = portfolioRepository,
      let user = authenticatedUser
    else {
      return
    }

    let previousSnapshot = portfolioState.snapshot
    portfolioRefreshInProgress = true
    if previousSnapshot == nil {
      portfolioState = .loading
    }
    defer { portfolioRefreshInProgress = false }

    do {
      let result = try await repository.load(
        authorizedAccountIDs: Set(user.authorizedAccountIDs)
      )
      switch result {
      case .noAccount(let fetchedAt):
        portfolioState = .noAccount(fetchedAt: fetchedAt)
      case .snapshot(let snapshot):
        portfolioState = .loaded(snapshot, refreshWarning: nil)
      }
    } catch is CancellationError {
      if let previousSnapshot {
        portfolioState = .loaded(previousSnapshot, refreshWarning: nil)
      } else {
        portfolioState = .idle
      }
    } catch PortfolioRepository.RepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        guard let refreshedRepository = portfolioRepository,
          let refreshedUser = authenticatedUser
        else {
          throw PortfolioRepository.RepositoryError.unauthenticated
        }
        let result = try await refreshedRepository.load(
          authorizedAccountIDs: Set(refreshedUser.authorizedAccountIDs)
        )
        applyPortfolioLoadResult(result)
      } catch let SessionClient.ClientError.server(code, _, _, _)
        where code == "UNAUTHENTICATED"
      {
        await clearLocalSession()
        authenticationState = .signedOut
      } catch PortfolioRepository.RepositoryError.unauthenticated {
        await clearLocalSession()
        authenticationState = .signedOut
      } catch {
        let message = "会话刷新或账户重试失败，请检查私网连接后重试"
        if let previousSnapshot {
          portfolioState = .loaded(previousSnapshot, refreshWarning: message)
        } else {
          portfolioState = .failed(message)
        }
      }
    } catch {
      let message =
        (error as? LocalizedError)?.errorDescription
        ?? "账户数据暂时无法读取"
      if let previousSnapshot {
        portfolioState = .loaded(
          previousSnapshot,
          refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)"
        )
      } else {
        portfolioState = .failed(message)
      }
    }
  }

  func refreshMarket() async {
    guard accountDataEnabled else {
      marketState = .unavailable("等待 TLS、认证与行情授权部署验收")
      return
    }
    guard hasPermission("market:read") else {
      marketState = .unavailable("当前会话没有 market:read 权限")
      return
    }
    guard hasPermission("portfolio:read") else {
      marketState = .unavailable("自选列表需要 portfolio:read 权限")
      return
    }
    guard !marketRefreshInProgress, !localSessionLocked else { return }

    if portfolioState.snapshot == nil {
      await refreshPortfolio()
    }
    guard let accountID = portfolioState.snapshot?.account.id else {
      if case .noAccount = portfolioState {
        marketState = .noAccount
      }
      return
    }
    guard let repository = marketRepository, let user = authenticatedUser else { return }

    let previousSnapshot = marketState.snapshot
    marketRefreshInProgress = true
    if previousSnapshot == nil {
      marketState = .loading
    }
    defer { marketRefreshInProgress = false }

    do {
      marketState = .loaded(
        try await repository.loadWatchlist(
          accountID: accountID,
          authorizedAccountIDs: Set(user.authorizedAccountIDs)
        ),
        refreshWarning: nil
      )
    } catch is CancellationError {
      marketState = previousSnapshot.map { .loaded($0, refreshWarning: nil) } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        await refreshPortfolio()
        guard let refreshedAccountID = portfolioState.snapshot?.account.id,
          refreshedAccountID == accountID,
          let refreshedRepository = marketRepository,
          let refreshedUser = authenticatedUser
        else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        marketState = .loaded(
          try await refreshedRepository.loadWatchlist(
            accountID: refreshedAccountID,
            authorizedAccountIDs: Set(refreshedUser.authorizedAccountIDs)
          ),
          refreshWarning: nil
        )
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .market
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "行情与自选暂时无法读取")
      marketState = previousSnapshot.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)")
      } ?? .failed(message)
    }
  }

  func searchMarket(term: String) async throws -> [MarketInstrument] {
    guard hasPermission("market:read"), !localSessionLocked else {
      throw ReadOnlyRepositoryError.forbidden
    }
    guard let repository = marketRepository else {
      throw ReadOnlyRepositoryError.transport
    }
    do {
      return try await repository.search(term: term)
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      guard let refreshedRepository = marketRepository else {
        throw ReadOnlyRepositoryError.unauthenticated
      }
      return try await refreshedRepository.search(term: term)
    }
  }

  func loadMarketInstrument(
    stockCode: String,
    period: MarketPeriod
  ) async throws -> MarketInstrumentSnapshot? {
    guard hasPermission("market:read"), !localSessionLocked else {
      throw ReadOnlyRepositoryError.forbidden
    }
    guard let repository = marketRepository else {
      throw ReadOnlyRepositoryError.transport
    }
    do {
      return try await repository.loadInstrument(stockCode: stockCode, period: period)
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      guard let refreshedRepository = marketRepository else {
        throw ReadOnlyRepositoryError.unauthenticated
      }
      return try await refreshedRepository.loadInstrument(
        stockCode: stockCode,
        period: period
      )
    }
  }

  func marketQuoteUpdates(
    stockCode: String
  ) throws -> AsyncThrowingStream<MarketLiveQuote, any Error> {
    guard hasPermission("market:read"), !localSessionLocked else {
      throw ReadOnlyRepositoryError.forbidden
    }
    guard let repository = marketRepository else {
      throw ReadOnlyRepositoryError.transport
    }
    return try repository.quoteUpdates(stockCode: stockCode)
  }

  func marketDepthUpdates(
    stockCode: String
  ) throws -> AsyncThrowingStream<MarketDepthSnapshot, any Error> {
    guard hasPermission("market:read"), !localSessionLocked else {
      throw ReadOnlyRepositoryError.forbidden
    }
    guard let repository = marketRepository else {
      throw ReadOnlyRepositoryError.transport
    }
    return try repository.depthUpdates(stockCode: stockCode)
  }

  func refreshStrategies() async {
    guard accountDataEnabled else {
      strategyState = .unavailable("等待 TLS、认证与只读授权部署验收")
      return
    }
    guard hasPermission("strategy:read") else {
      strategyState = .unavailable("当前会话没有 strategy:read 权限")
      return
    }
    guard !strategyRefreshInProgress else { return }
    guard !localSessionLocked, let repository = strategyRepository else { return }

    let previousSnapshot = strategyState.snapshot
    strategyRefreshInProgress = true
    if previousSnapshot == nil {
      strategyState = .loading
    }
    defer { strategyRefreshInProgress = false }

    do {
      strategyState = .loaded(try await repository.load(), refreshWarning: nil)
    } catch is CancellationError {
      if let previousSnapshot {
        strategyState = .loaded(previousSnapshot, refreshWarning: nil)
      } else {
        strategyState = .idle
      }
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        await refreshPortfolio()
        guard let refreshedRepository = strategyRepository else {
          throw ReadOnlyRepositoryError.unauthenticated
        }
        strategyState = .loaded(
          try await refreshedRepository.load(),
          refreshWarning: nil
        )
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .strategy
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "策略快照暂时无法读取")
      if let previousSnapshot {
        strategyState = .loaded(
          previousSnapshot,
          refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)"
        )
      } else {
        strategyState = .failed(message)
      }
    }
  }

  func refreshTradingActivity() async {
    guard accountDataEnabled else {
      tradingState = .unavailable("等待 TLS、认证与只读授权部署验收")
      return
    }
    guard hasPermission("orders:read") else {
      tradingState = .unavailable("当前会话没有 orders:read 权限")
      return
    }
    guard !tradingRefreshInProgress else { return }
    guard !localSessionLocked else { return }

    if portfolioState.snapshot == nil {
      await refreshPortfolio()
    }
    guard let accountID = portfolioState.snapshot?.account.id else {
      if case .noAccount = portfolioState {
        tradingState = .noAccount
      }
      return
    }
    guard let repository = tradingRepository else { return }

    let previousSnapshot = tradingState.snapshot
    tradingRefreshInProgress = true
    if previousSnapshot == nil {
      tradingState = .loading
    }
    defer { tradingRefreshInProgress = false }

    do {
      tradingState = .loaded(
        try await repository.load(accountID: accountID),
        refreshWarning: nil
      )
    } catch is CancellationError {
      if let previousSnapshot {
        tradingState = .loaded(previousSnapshot, refreshWarning: nil)
      } else {
        tradingState = .idle
      }
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        await refreshPortfolio()
        guard let refreshedAccountID = portfolioState.snapshot?.account.id,
          refreshedAccountID == accountID,
          let refreshedRepository = tradingRepository
        else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        tradingState = .loaded(
          try await refreshedRepository.load(accountID: refreshedAccountID),
          refreshWarning: nil
        )
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .trading
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "委托成交暂时无法读取")
      if let previousSnapshot {
        tradingState = .loaded(
          previousSnapshot,
          refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)"
        )
      } else {
        tradingState = .failed(message)
      }
    }
  }

  var limitUpStrategyInstances: [StrategyMonitorItem] {
    strategyState.snapshot?.instances.filter(\.isLimitUpBoardStrategy) ?? []
  }

  func refreshTTradeAssistant() async {
    guard accountDataEnabled else {
      tTradeAssistantState = .unavailable("等待 TLS、认证与只读授权部署验收")
      return
    }
    guard hasPermission("strategy:read") else {
      tTradeAssistantState = .unavailable("当前会话没有 strategy:read 权限")
      return
    }
    guard !tTradeAssistantRefreshInProgress, !localSessionLocked else { return }
    if portfolioState.snapshot == nil {
      await refreshPortfolio()
    }
    guard let accountID = portfolioState.snapshot?.account.id else {
      if case .noAccount = portfolioState {
        tTradeAssistantState = .noAccount
      }
      return
    }
    guard let repository = tTradeAssistantRepository else { return }

    let previousSnapshot = tTradeAssistantState.snapshot
    tTradeAssistantRefreshInProgress = true
    if previousSnapshot == nil {
      tTradeAssistantState = .loading
    }
    defer { tTradeAssistantRefreshInProgress = false }

    do {
      tTradeAssistantState = .loaded(
        try await repository.load(accountID: accountID),
        refreshWarning: nil
      )
    } catch is CancellationError {
      tTradeAssistantState = previousSnapshot.map {
        .loaded($0, refreshWarning: nil)
      } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        await refreshPortfolio()
        guard let refreshedAccountID = portfolioState.snapshot?.account.id,
          refreshedAccountID == accountID,
          let refreshedRepository = tTradeAssistantRepository
        else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        tTradeAssistantState = .loaded(
          try await refreshedRepository.load(accountID: refreshedAccountID),
          refreshWarning: nil
        )
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .tTrade
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "做T监控暂时无法读取")
      tTradeAssistantState = previousSnapshot.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)")
      } ?? .failed(message)
    }
  }

  func refreshLimitUpBoard(runID requestedRunID: String? = nil) async {
    guard accountDataEnabled else {
      limitUpBoardState = .unavailable("等待 TLS、认证与只读授权部署验收")
      return
    }
    guard hasPermission("strategy:read") else {
      limitUpBoardState = .unavailable("当前会话没有 strategy:read 权限")
      return
    }
    guard !limitUpBoardRefreshInProgress, !localSessionLocked else { return }
    if strategyState.snapshot == nil {
      await refreshStrategies()
    }
    guard let runID = requestedRunID ?? limitUpStrategyInstances.first?.id else {
      limitUpBoardState = .noStrategy
      return
    }
    guard let repository = limitUpBoardRepository else { return }

    let currentSnapshot = limitUpBoardState.snapshot
    let previousSnapshot = currentSnapshot?.runID == runID ? currentSnapshot : nil
    limitUpBoardRefreshInProgress = true
    if previousSnapshot == nil {
      limitUpBoardState = .loading
    }
    defer { limitUpBoardRefreshInProgress = false }

    do {
      limitUpBoardState = .loaded(
        try await repository.load(runID: runID),
        refreshWarning: nil
      )
    } catch is CancellationError {
      limitUpBoardState = previousSnapshot.map {
        .loaded($0, refreshWarning: nil)
      } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        guard let refreshedRepository = limitUpBoardRepository else {
          throw ReadOnlyRepositoryError.unauthenticated
        }
        limitUpBoardState = .loaded(
          try await refreshedRepository.load(runID: runID),
          refreshWarning: nil
        )
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .limitUp
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "打板工作台暂时无法读取")
      limitUpBoardState = previousSnapshot.map {
        .loaded($0, refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)")
      } ?? .failed(message)
    }
  }

  var canApproveTrades: Bool {
    accountDataEnabled
      && hasPermission("trade:approve")
      && !localSessionLocked
      && tradeApprovalRepository != nil
  }

  func previewTTradeEntryApproval(
    runID: String,
    intentID: String
  ) async throws -> TradeApprovalPreview {
    let accountIDs = try authorizedTradeApprovalAccountIDs()
    guard let repository = tradeApprovalRepository else {
      throw tradeApprovalUnavailable("交易确认服务尚未连接")
    }
    do {
      return try await repository.previewTTradeEntry(
        runID: runID,
        intentID: intentID,
        authorizedAccountIDs: accountIDs
      )
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      guard let refreshedRepository = tradeApprovalRepository else {
        throw tradeApprovalUnavailable("交易确认服务尚未连接")
      }
      return try await refreshedRepository.previewTTradeEntry(
        runID: runID,
        intentID: intentID,
        authorizedAccountIDs: try authorizedTradeApprovalAccountIDs()
      )
    }
  }

  func previewStrategyTradeIntentApproval(
    runID: String,
    intentID: String
  ) async throws -> TradeApprovalPreview {
    let accountIDs = try authorizedTradeApprovalAccountIDs()
    guard let repository = tradeApprovalRepository else {
      throw tradeApprovalUnavailable("交易确认服务尚未连接")
    }
    do {
      return try await repository.previewStrategyTradeIntent(
        runID: runID,
        intentID: intentID,
        authorizedAccountIDs: accountIDs
      )
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      guard let refreshedRepository = tradeApprovalRepository else {
        throw tradeApprovalUnavailable("交易确认服务尚未连接")
      }
      return try await refreshedRepository.previewStrategyTradeIntent(
        runID: runID,
        intentID: intentID,
        authorizedAccountIDs: try authorizedTradeApprovalAccountIDs()
      )
    }
  }

  func confirmTradeApproval(
    _ preview: TradeApprovalPreview
  ) async throws -> TradeApprovalConfirmation {
    guard canApproveTrades else {
      throw tradeApprovalUnavailable("当前会话没有 trade:approve 权限")
    }
    guard !tradeApprovalInProgress, !preview.isExpired() else {
      throw tradeApprovalUnavailable("确认凭据已过期或正在处理，请刷新信号")
    }

    tradeApprovalInProgress = true
    defer { tradeApprovalInProgress = false }
    try await localAuthentication.authorizeTrade(
      reason: "确认 \(preview.instrumentCode) 买入意图并提交统一交易风控"
    )
    guard !preview.isExpired() else {
      throw tradeApprovalUnavailable("本机认证完成时确认凭据已过期，请重新预览")
    }

    let confirmation: TradeApprovalConfirmation
    do {
      confirmation = try await performTradeApprovalConfirmation(preview)
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      confirmation = try await performTradeApprovalConfirmation(preview)
    }
    switch preview.kind {
    case .tTradeEntry:
      await refreshTTradeAssistant()
    case .strategyTradeIntent:
      await refreshLimitUpBoard(runID: preview.runID)
    }
    return confirmation
  }

  private func performTradeApprovalConfirmation(
    _ preview: TradeApprovalPreview
  ) async throws -> TradeApprovalConfirmation {
    guard let repository = tradeApprovalRepository else {
      throw tradeApprovalUnavailable("交易确认服务尚未连接")
    }
    switch preview.kind {
    case .tTradeEntry:
      return try await repository.confirmTTradeEntry(preview)
    case .strategyTradeIntent:
      return try await repository.confirmStrategyTradeIntent(preview)
    }
  }

  private func authorizedTradeApprovalAccountIDs() throws -> Set<String> {
    guard hasPermission("trade:approve") else {
      throw tradeApprovalUnavailable("当前会话没有 trade:approve 权限")
    }
    guard !localSessionLocked, let user = authenticatedUser else {
      throw tradeApprovalUnavailable("请先解锁并恢复账户会话")
    }
    let accountIDs = Set(user.authorizedAccountIDs)
    guard !accountIDs.isEmpty else {
      throw tradeApprovalUnavailable("当前用户未授权任何资金账户")
    }
    return accountIDs
  }

  private func tradeApprovalUnavailable(_ message: String) -> TradeApprovalRepositoryError {
    .rejected(code: "TRADE_APPROVAL_UNAVAILABLE", message: message)
  }

  private func activate(
    _ session: AuthenticatedSession,
    configuration: APIConfiguration,
    persistTokens: Bool = true
  ) async throws {
    let newApolloSession = try apolloSessionFactory(
      configuration,
      session.tokens.accessToken
    )
    if persistTokens {
      try await tokenStore.save(session.tokens)
    }
    if let oldApolloSession = apolloSession {
      try? await oldApolloSession.clearCache()
      await oldApolloSession.pauseSubscriptions()
    }
    apolloSession = newApolloSession
    portfolioRepository = portfolioLoaderFactory(newApolloSession)
    strategyRepository = strategyLoaderFactory(newApolloSession)
    tradingRepository = tradingLoaderFactory(newApolloSession)
    tTradeAssistantRepository = tTradeAssistantLoaderFactory(newApolloSession)
    limitUpBoardRepository = limitUpBoardLoaderFactory(newApolloSession)
    tradeApprovalRepository = tradeApprovalLoaderFactory(newApolloSession)
    marketRepository = marketLoaderFactory(newApolloSession)
    portfolioState = session.user.permissions.contains("portfolio:read")
      ? .idle
      : .unavailable("当前会话没有 portfolio:read 权限")
    strategyState = session.user.permissions.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    tradingState = session.user.permissions.contains("orders:read")
      ? .idle
      : .unavailable("当前会话没有 orders:read 权限")
    tTradeAssistantState = session.user.permissions.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    limitUpBoardState = session.user.permissions.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    if !session.user.permissions.contains("market:read") {
      marketState = .unavailable("当前会话没有 market:read 权限")
    } else if !session.user.permissions.contains("portfolio:read") {
      marketState = .unavailable("自选列表需要 portfolio:read 权限")
    } else {
      marketState = .idle
    }
    localSessionLocked = false
    localUnlockErrorMessage = nil
    authenticationState = .authenticated(session.user)
  }

  private func refreshAccessSession() async throws {
    guard let sessionClient,
      let configuration,
      let tokens = try await tokenStore.load(),
      tokens.refreshTokenExpiresAt > Date()
    else {
      throw PortfolioRepository.RepositoryError.unauthenticated
    }
    let refreshed = try await sessionClient.refresh(
      refreshToken: tokens.refreshToken
    )
    try await activate(refreshed, configuration: configuration)
  }

  private func applyPortfolioLoadResult(_ result: PortfolioLoadResult) {
    switch result {
    case .noAccount(let fetchedAt):
      portfolioState = .noAccount(fetchedAt: fetchedAt)
    case .snapshot(let snapshot):
      portfolioState = .loaded(snapshot, refreshWarning: nil)
    }
  }

  private func handleAuthenticationFailure(_ error: Error) async {
    if case SessionClient.ClientError.server(let code, _, _, _) = error,
      code == "UNAUTHENTICATED"
    {
      await clearLocalSession()
      authenticationState = .signedOut
      return
    }
    authenticationState = .failed(error.localizedDescription)
  }

  private func clearLocalSession() async {
    if let apolloSession {
      try? await apolloSession.clearCache()
      await apolloSession.pauseSubscriptions()
    }
    apolloSession = nil
    portfolioRepository = nil
    strategyRepository = nil
    tradingRepository = nil
    tTradeAssistantRepository = nil
    limitUpBoardRepository = nil
    tradeApprovalRepository = nil
    marketRepository = nil
    tradeApprovalInProgress = false
    portfolioState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与只读授权部署验收")
    strategyState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与只读授权部署验收")
    tradingState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与只读授权部署验收")
    tTradeAssistantState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与只读授权部署验收")
    limitUpBoardState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与只读授权部署验收")
    marketState =
      accountDataEnabled
      ? .idle
      : .unavailable("等待 TLS、认证与行情授权部署验收")
    localSessionLocked = false
    localUnlockErrorMessage = nil
    try? await tokenStore.delete()
  }

  private func resumeSessionAfterForeground() async {
    guard case .authenticated = authenticationState else { return }
    do {
      guard let tokens = try await tokenStore.load() else {
        authenticationState = .signedOut
        return
      }
      if tokens.accessTokenExpiresAt <= Date().addingTimeInterval(30) {
        await restoreSession()
      } else {
        await apolloSession?.resumeSubscriptions()
        await refreshAllReadOnlySnapshots()
      }
    } catch {
      await handleAuthenticationFailure(error)
    }
  }

  func handleScenePhase(_ phase: ScenePhase) {
#if DEBUG
    if usesTransientRealBackendUITestSession {
      switch phase {
      case .active:
        privacyShieldVisible = false
        Task {
          await refreshHealth()
          await apolloSession?.resumeSubscriptions()
        }
      case .inactive, .background:
        privacyShieldVisible = true
        Task { await apolloSession?.pauseSubscriptions() }
      @unknown default:
        privacyShieldVisible = true
      }
      return
    }
#endif
    switch phase {
    case .active:
      privacyShieldVisible = false
      Task {
        await refreshHealth()
        if localSessionLocked {
          await unlockLocalSession()
        } else {
          await resumeSessionAfterForeground()
        }
      }
    case .inactive, .background:
      privacyShieldVisible = true
      if case .authenticated = authenticationState {
        localSessionLocked = true
      }
      Task { await apolloSession?.pauseSubscriptions() }
    @unknown default:
      privacyShieldVisible = true
    }
  }

  private enum ReadOnlyFeature {
    case market
    case strategy
    case trading
    case tTrade
    case limitUp
  }

  private func refreshAllReadOnlySnapshots() async {
    await refreshPortfolio()
    await refreshMarket()
    await refreshStrategies()
    await refreshTradingActivity()
    await refreshTTradeAssistant()
    await refreshLimitUpBoard()
  }

#if DEBUG
  private func startTransientRealBackendUITestSession() async {
    await refreshHealth()
    guard let configuration else {
      authenticationState = .failed("真实后端 UI 测试配置无效")
      return
    }
    do {
      guard let session = try DebugRealBackendUITestSession.make(
        arguments: ProcessInfo.processInfo.arguments,
        environment: ProcessInfo.processInfo.environment
      ) else {
        authenticationState = .failed("真实后端 UI 测试会话未提供")
        return
      }
      try await activate(
        session,
        configuration: configuration,
        persistTokens: false
      )
      await refreshAllReadOnlySnapshots()
    } catch {
      authenticationState = .failed("真实后端 UI 测试会话无效")
    }
  }
#endif

  private func hasPermission(_ permission: String) -> Bool {
    authenticatedUser?.permissions.contains(permission) == true
  }

  private func readOnlyErrorMessage(_ error: Error, fallback: String) -> String {
    (error as? LocalizedError)?.errorDescription ?? fallback
  }

  private func handleReadOnlyRetryFailure<Snapshot: Equatable & Sendable>(
    _ error: Error,
    previousSnapshot: Snapshot?,
    feature: ReadOnlyFeature
  ) async {
    let isUnauthenticated =
      (error as? ReadOnlyRepositoryError) == .unauthenticated
      || {
        if case SessionClient.ClientError.server(let code, _, _, _) = error {
          return code == "UNAUTHENTICATED"
        }
        return false
      }()
    if isUnauthenticated {
      await clearLocalSession()
      authenticationState = .signedOut
      return
    }

    let message = "会话刷新或数据重试失败，请检查私网连接后重试"
    switch feature {
    case .market:
      if let snapshot = previousSnapshot as? MarketWorkspaceSnapshot {
        marketState = .loaded(snapshot, refreshWarning: message)
      } else {
        marketState = .failed(message)
      }
    case .strategy:
      if let snapshot = previousSnapshot as? StrategyMonitorSnapshot {
        strategyState = .loaded(snapshot, refreshWarning: message)
      } else {
        strategyState = .failed(message)
      }
    case .trading:
      if let snapshot = previousSnapshot as? TradingActivitySnapshot {
        tradingState = .loaded(snapshot, refreshWarning: message)
      } else {
        tradingState = .failed(message)
      }
    case .tTrade:
      if let snapshot = previousSnapshot as? TTradeAssistantSnapshot {
        tTradeAssistantState = .loaded(snapshot, refreshWarning: message)
      } else {
        tTradeAssistantState = .failed(message)
      }
    case .limitUp:
      if let snapshot = previousSnapshot as? LimitUpBoardSnapshot {
        limitUpBoardState = .loaded(snapshot, refreshWarning: message)
      } else {
        limitUpBoardState = .failed(message)
      }
    }
  }
}
