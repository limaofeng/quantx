import SwiftUI
import UIKit

@MainActor
final class AppModel: ObservableObject {
  typealias ApolloSessionFactory = (APIConfiguration, String) throws -> ApolloSession
  typealias PortfolioLoaderFactory = (ApolloSession) -> any PortfolioLoading
  typealias StrategyLoaderFactory = (ApolloSession) -> any StrategyMonitoringLoading
  typealias TradingLoaderFactory = (ApolloSession) -> any TradingActivityLoading
  typealias TTradeAssistantLoaderFactory = (ApolloSession) -> any TTradeAssistantLoading
  typealias TTradeControlLoaderFactory = (ApolloSession) -> any TTradeControlLoading
  typealias LimitUpBoardLoaderFactory = (ApolloSession) -> any LimitUpBoardLoading
  typealias TradeApprovalLoaderFactory = (ApolloSession) -> any TradeApprovalLoading
  typealias ManualOrderLoaderFactory = (ApolloSession) -> any ManualOrderLoading
  typealias OrderCancellationLoaderFactory = (ApolloSession) -> any OrderCancellationLoading
  typealias MarketLoaderFactory = (ApolloSession) -> any MarketDataLoading
  typealias LiquidationLoaderFactory = (ApolloSession) -> any LiquidationLoading
  typealias ExitPlanLoaderFactory = (ApolloSession) -> any ExitPlanLoading
  typealias PushNotificationLoaderFactory = (ApolloSession) -> any PushNotificationLoading

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
  @Published private(set) var pendingManualOrderDraft: ManualOrderDraftLink?
  @Published private(set) var pendingNotificationTradeRoute: NotificationNavigationRequest?
  @Published private(set) var pendingNotificationSystemRoute: NotificationNavigationRequest?
  @Published private(set) var marketState: MarketWorkspaceState = .idle
  @Published private(set) var marketRefreshInProgress = false
  @Published private(set) var watchlistMutationInProgress = false
  @Published private(set) var watchlistMutationErrorMessage: String?

  let configuration: APIConfiguration?
  let configurationErrorMessage: String?
  let liquidationStore: LiquidationStore
  let exitPlanWorkspace: ExitPlanWorkspace
  let tTradeControlStore: TTradeControlStore
  let manualTradingStore: ManualTradingStore
  let strategyWorkspace: StrategyWorkspace
  let notificationStore: PushNotificationStore
  private let healthClient: (any HealthChecking)?
  private let sessionClient: (any SessionServing)?
  private let tokenStore: any SessionTokenStore
  private let localAuthentication: any LocalAuthenticationProviding
  private let apolloSessionFactory: ApolloSessionFactory
  private let portfolioLoaderFactory: PortfolioLoaderFactory
  private let strategyLoaderFactory: StrategyLoaderFactory
  private let tradingLoaderFactory: TradingLoaderFactory
  private let tTradeAssistantLoaderFactory: TTradeAssistantLoaderFactory
  private let tTradeControlLoaderFactory: TTradeControlLoaderFactory
  private let limitUpBoardLoaderFactory: LimitUpBoardLoaderFactory
  private let tradeApprovalLoaderFactory: TradeApprovalLoaderFactory
  private let manualOrderLoaderFactory: ManualOrderLoaderFactory
  private let orderCancellationLoaderFactory: OrderCancellationLoaderFactory
  private let marketLoaderFactory: MarketLoaderFactory
  private let liquidationLoaderFactory: LiquidationLoaderFactory
  private let exitPlanLoaderFactory: ExitPlanLoaderFactory
  private let pushNotificationLoaderFactory: PushNotificationLoaderFactory
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
    liquidationStore = LiquidationStore(localAuthentication: localAuthentication)
    exitPlanWorkspace = ExitPlanWorkspace(localAuthentication: localAuthentication)
    tTradeControlStore = TTradeControlStore(localAuthentication: localAuthentication)
    manualTradingStore = ManualTradingStore(localAuthentication: localAuthentication)
    strategyWorkspace = StrategyWorkspace(localAuthentication: localAuthentication)
    notificationStore = PushNotificationStore(bundle: bundle)
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
    tTradeControlLoaderFactory = { session in
      TTradeControlRepository(client: session.client)
    }
    limitUpBoardLoaderFactory = { session in
      LimitUpBoardRepository(client: session.client)
    }
    tradeApprovalLoaderFactory = { session in
      TradeApprovalRepository(client: session.client)
    }
    manualOrderLoaderFactory = { session in
      ManualOrderRepository(client: session.client)
    }
    orderCancellationLoaderFactory = { session in
      OrderCancellationRepository(client: session.client)
    }
    marketLoaderFactory = { session in
      MarketRepository(client: session.client)
    }
    liquidationLoaderFactory = { session in
      LiquidationRepository(client: session.client)
    }
    exitPlanLoaderFactory = { session in
      ExitPlanRepository(client: session.client)
    }
    pushNotificationLoaderFactory = { session in
      PushNotificationRepository(client: session.client)
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
    configureLiquidationStore()
    configureExitPlanWorkspace()
    configureTTradeControlStore()
    configureManualTradingStore()
    configureStrategyWorkspace()
    configureNotificationStore()
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
    tTradeControlLoaderFactory: @escaping TTradeControlLoaderFactory = { session in
      TTradeControlRepository(client: session.client)
    },
    limitUpBoardLoaderFactory: @escaping LimitUpBoardLoaderFactory = { session in
      LimitUpBoardRepository(client: session.client)
    },
    tradeApprovalLoaderFactory: @escaping TradeApprovalLoaderFactory = { session in
      TradeApprovalRepository(client: session.client)
    },
    manualOrderLoaderFactory: @escaping ManualOrderLoaderFactory = { session in
      ManualOrderRepository(client: session.client)
    },
    orderCancellationLoaderFactory: @escaping OrderCancellationLoaderFactory = { session in
      OrderCancellationRepository(client: session.client)
    },
    marketLoaderFactory: @escaping MarketLoaderFactory = { session in
      MarketRepository(client: session.client)
    },
    liquidationLoaderFactory: @escaping LiquidationLoaderFactory = { session in
      LiquidationRepository(client: session.client)
    },
    exitPlanLoaderFactory: @escaping ExitPlanLoaderFactory = { session in
      ExitPlanRepository(client: session.client)
    },
    notificationStore: PushNotificationStore = .disabled(),
    pushNotificationLoaderFactory: @escaping PushNotificationLoaderFactory = { session in
      PushNotificationRepository(client: session.client)
    }
  ) {
    self.configuration = configuration
    configurationErrorMessage = nil
    self.healthClient = healthClient
    self.sessionClient = sessionClient
    self.tokenStore = tokenStore
    self.localAuthentication = localAuthentication
    liquidationStore = LiquidationStore(localAuthentication: localAuthentication)
    exitPlanWorkspace = ExitPlanWorkspace(localAuthentication: localAuthentication)
    tTradeControlStore = TTradeControlStore(localAuthentication: localAuthentication)
    manualTradingStore = ManualTradingStore(localAuthentication: localAuthentication)
    strategyWorkspace = StrategyWorkspace(localAuthentication: localAuthentication)
    self.notificationStore = notificationStore
    self.apolloSessionFactory = apolloSessionFactory
    self.portfolioLoaderFactory = portfolioLoaderFactory
    self.strategyLoaderFactory = strategyLoaderFactory
    self.tradingLoaderFactory = tradingLoaderFactory
    self.tTradeAssistantLoaderFactory = tTradeAssistantLoaderFactory
    self.tTradeControlLoaderFactory = tTradeControlLoaderFactory
    self.limitUpBoardLoaderFactory = limitUpBoardLoaderFactory
    self.tradeApprovalLoaderFactory = tradeApprovalLoaderFactory
    self.manualOrderLoaderFactory = manualOrderLoaderFactory
    self.orderCancellationLoaderFactory = orderCancellationLoaderFactory
    self.marketLoaderFactory = marketLoaderFactory
    self.liquidationLoaderFactory = liquidationLoaderFactory
    self.exitPlanLoaderFactory = exitPlanLoaderFactory
    self.pushNotificationLoaderFactory = pushNotificationLoaderFactory

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
    configureLiquidationStore()
    configureExitPlanWorkspace()
    configureTTradeControlStore()
    configureManualTradingStore()
    configureStrategyWorkspace()
    configureNotificationStore()
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

  var watchlistEditingUnavailableReason: String? {
    guard accountDataEnabled else {
      return "账户服务尚未开放，自选保持只读"
    }
    guard hasPermission("watchlist:write") else {
      return "当前会话没有 watchlist:write 权限，自选保持只读"
    }
    guard hasPermission("market:read"), hasPermission("portfolio:read") else {
      return "自选维护需要 market:read 与 portfolio:read 权限"
    }
    guard !localSessionLocked else {
      return "本机会话已锁定，解锁后才能维护自选"
    }
    guard
      let activeAccountID = authenticatedUser?.activeAccountID,
      portfolioState.snapshot?.account.id == activeAccountID,
      marketState.snapshot?.accountID == activeAccountID,
      marketRepository != nil
    else {
      return "当前主账户上下文不可用，自选保持只读"
    }
    return nil
  }

  var canEditWatchlist: Bool {
    watchlistEditingUnavailableReason == nil
  }

  func isInWatchlist(stockCode: String) -> Bool {
    let normalized =
      stockCode
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    return marketState.snapshot?.watchlist.contains {
      $0.stockCode == normalized
    } == true
  }

  func start() async {
    #if DEBUG
      if ProcessInfo.processInfo.arguments.contains("-QuantXLoginUITesting") {
        authenticationState = .signedOut
        return
      }
      if usesTransientRealBackendUITestSession {
        await startTransientRealBackendUITestSession()
        return
      }
      if ProcessInfo.processInfo.arguments.contains("-QuantXWatchlistReadOnlyUITesting") {
        startWatchlistReadOnlyUITestFixture()
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
    await notificationStore.prepareSystemState()
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
        let refreshed = try await sessionClient.refresh(refreshToken: tokens.refreshToken)
        try validateNativeSessionUser(
          refreshed.user,
          previousAuthorization: nil
        )
        try await tokenStore.save(refreshed.tokens)
        let user = try await sessionClient.current(
          accessToken: refreshed.tokens.accessToken
        )
        restored = AuthenticatedSession(tokens: refreshed.tokens, user: user)
      }
      try await activate(
        restored,
        configuration: configuration,
        persistTokens: false
      )
      await refreshAllReadOnlySnapshots()
    } catch {
      await handleAuthenticationFailure(error)
    }
  }

  func login(
    username: String,
    password: String
  ) async {
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
      await notificationStore.setLocalSessionUnlocked(true)
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
    await notificationStore.unregisterBeforeLogout()
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
        authorizedAccountIDs: activeAccountIDs(for: user)
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
        guard hasPermission("portfolio:read") else { return }
        guard let refreshedRepository = portfolioRepository,
          let refreshedUser = authenticatedUser
        else {
          throw PortfolioRepository.RepositoryError.unauthenticated
        }
        let result = try await refreshedRepository.load(
          authorizedAccountIDs: activeAccountIDs(for: refreshedUser)
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
      let snapshot = try await repository.loadWatchlist(
        accountID: accountID,
        authorizedAccountIDs: activeAccountIDs(for: user)
      )
      marketState = .loaded(snapshot, refreshWarning: nil)
      watchlistMutationErrorMessage = nil
    } catch is CancellationError {
      marketState = previousSnapshot.map { .loaded($0, refreshWarning: nil) } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        guard hasPermission("market:read"), hasPermission("portfolio:read") else {
          return
        }
        await refreshPortfolio()
        guard let refreshedAccountID = portfolioState.snapshot?.account.id,
          refreshedAccountID == accountID,
          let refreshedRepository = marketRepository,
          let refreshedUser = authenticatedUser
        else {
          throw ReadOnlyRepositoryError.accountScopeMismatch
        }
        let snapshot = try await refreshedRepository.loadWatchlist(
          accountID: refreshedAccountID,
          authorizedAccountIDs: activeAccountIDs(for: refreshedUser)
        )
        marketState = .loaded(snapshot, refreshWarning: nil)
        watchlistMutationErrorMessage = nil
      } catch {
        await handleReadOnlyRetryFailure(
          error,
          previousSnapshot: previousSnapshot,
          feature: .market
        )
      }
    } catch {
      let message = readOnlyErrorMessage(error, fallback: "行情与自选暂时无法读取")
      marketState =
        previousSnapshot.map {
          .loaded($0, refreshWarning: "刷新失败，正在显示上次成功获取的数据。\(message)")
        } ?? .failed(message)
    }
  }

  func addToWatchlist(_ instrument: MarketInstrument) async throws {
    let context = try prepareWatchlistMutation()
    let normalizedCode: String
    do {
      normalizedCode = try MarketRepository.normalizedAStockCode(instrument.stockCode)
      guard !context.snapshot.watchlist.contains(where: { $0.stockCode == normalizedCode }) else {
        throw WatchlistMutationError.invalidRequest("该证券已在自选列表中")
      }
    } catch {
      failWatchlistPreparation(error)
      throw error
    }

    let currentMaximumOrder = context.snapshot.watchlist.map(\.displayOrder).max() ?? 0
    guard currentMaximumOrder < Int(Int32.max) else {
      let error = WatchlistMutationError.invalidRequest("自选排序超出有效范围")
      failWatchlistPreparation(error)
      throw error
    }
    let displayOrder = currentMaximumOrder + 1
    let optimisticItem = MarketWatchItem(
      id: "optimistic-\(UUID().uuidString.lowercased())",
      accountID: context.accountID,
      stockCode: normalizedCode,
      instrumentName: instrument.name,
      displayOrder: displayOrder,
      groupName: nil,
      note: nil,
      updatedAt: Date(),
      quote: instrument.quote
    )
    let optimisticSnapshot = context.snapshot.replacingWatchlist(
      context.snapshot.watchlist + [optimisticItem]
    )
    marketState = .loaded(optimisticSnapshot, refreshWarning: nil)
    defer { watchlistMutationInProgress = false }

    do {
      let serverItem = try await performWatchlistMutation(
        context: context,
        optimisticSnapshot: optimisticSnapshot
      ) { repository, authorizedAccountIDs in
        try await repository.addWatchlistItem(
          accountID: context.accountID,
          stockCode: normalizedCode,
          instrumentName: instrument.name,
          displayOrder: displayOrder,
          authorizedAccountIDs: authorizedAccountIDs
        )
      }
      let authoritative = optimisticSnapshot.watchlist.map { item in
        item.stockCode == normalizedCode
          ? serverItem.hydrated(with: item.quote)
          : item
      }
      marketState = .loaded(
        optimisticSnapshot.replacingWatchlist(authoritative),
        refreshWarning: nil
      )
      watchlistMutationErrorMessage = nil
      await refreshMarket()
    } catch {
      rollbackWatchlistMutation(error, to: context.snapshot)
      throw error
    }
  }

  func removeFromWatchlist(stockCode: String) async throws {
    let context = try prepareWatchlistMutation()
    let normalizedCode: String
    do {
      normalizedCode = try MarketRepository.normalizedAStockCode(stockCode)
      guard context.snapshot.watchlist.contains(where: { $0.stockCode == normalizedCode }) else {
        throw WatchlistMutationError.invalidRequest("该证券不在当前自选列表中")
      }
    } catch {
      failWatchlistPreparation(error)
      throw error
    }

    let optimisticItems = context.snapshot.watchlist
      .filter { $0.stockCode != normalizedCode }
      .enumerated()
      .map { index, item in item.ordered(at: index + 1) }
    let optimisticSnapshot = context.snapshot.replacingWatchlist(optimisticItems)
    marketState = .loaded(optimisticSnapshot, refreshWarning: nil)
    defer { watchlistMutationInProgress = false }

    do {
      try await performWatchlistMutation(
        context: context,
        optimisticSnapshot: optimisticSnapshot
      ) { repository, authorizedAccountIDs in
        try await repository.removeWatchlistItem(
          accountID: context.accountID,
          stockCode: normalizedCode,
          authorizedAccountIDs: authorizedAccountIDs
        )
      }
      watchlistMutationErrorMessage = nil
      await refreshMarket()
    } catch {
      rollbackWatchlistMutation(error, to: context.snapshot)
      throw error
    }
  }

  func reorderWatchlist(stockCodes: [String]) async throws {
    let context = try prepareWatchlistMutation()
    let normalizedCodes: [String]
    do {
      normalizedCodes = try MarketRepository.validateReorderStockCodes(stockCodes)
      guard
        normalizedCodes.count == context.snapshot.watchlist.count,
        Set(normalizedCodes) == Set(context.snapshot.watchlist.map(\.stockCode))
      else {
        throw WatchlistMutationError.contextMismatch
      }
    } catch {
      failWatchlistPreparation(error)
      throw error
    }

    let currentByCode = Dictionary(
      uniqueKeysWithValues: context.snapshot.watchlist.map { ($0.stockCode, $0) }
    )
    let optimisticItems = normalizedCodes.enumerated().compactMap { index, code in
      currentByCode[code]?.ordered(at: index + 1)
    }
    let optimisticSnapshot = context.snapshot.replacingWatchlist(optimisticItems)
    marketState = .loaded(optimisticSnapshot, refreshWarning: nil)
    defer { watchlistMutationInProgress = false }

    do {
      let serverItems = try await performWatchlistMutation(
        context: context,
        optimisticSnapshot: optimisticSnapshot
      ) { repository, authorizedAccountIDs in
        try await repository.reorderWatchlist(
          accountID: context.accountID,
          stockCodes: normalizedCodes,
          authorizedAccountIDs: authorizedAccountIDs
        )
      }
      let optimisticByCode = Dictionary(
        uniqueKeysWithValues: optimisticItems.map { ($0.stockCode, $0) }
      )
      let authoritative = serverItems.map { item in
        item.hydrated(with: optimisticByCode[item.stockCode]?.quote)
      }
      marketState = .loaded(
        optimisticSnapshot.replacingWatchlist(authoritative),
        refreshWarning: nil
      )
      watchlistMutationErrorMessage = nil
      await refreshMarket()
    } catch {
      rollbackWatchlistMutation(error, to: context.snapshot)
      throw error
    }
  }

  func clearWatchlistMutationError() {
    watchlistMutationErrorMessage = nil
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
      guard hasPermission("market:read") else {
        throw ReadOnlyRepositoryError.forbidden
      }
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
      guard hasPermission("market:read") else {
        throw ReadOnlyRepositoryError.forbidden
      }
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
        guard hasPermission("strategy:read") else { return }
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
    defer {
      tradingRefreshInProgress = false
      manualTradingStore.reconcileCancellationProjection()
    }

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
        guard hasPermission("orders:read") else { return }
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
      tTradeAssistantState =
        previousSnapshot.map {
          .loaded($0, refreshWarning: nil)
        } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        guard hasPermission("strategy:read") else { return }
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
      tTradeAssistantState =
        previousSnapshot.map {
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
      limitUpBoardState =
        previousSnapshot.map {
          .loaded($0, refreshWarning: nil)
        } ?? .idle
    } catch ReadOnlyRepositoryError.unauthenticated {
      do {
        try await refreshAccessSession()
        guard hasPermission("strategy:read") else { return }
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
      limitUpBoardState =
        previousSnapshot.map {
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
      guard canApproveTrades else {
        throw tradeApprovalUnavailable("当前会话没有 trade:approve 权限")
      }
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
      guard canApproveTrades else {
        throw tradeApprovalUnavailable("当前会话没有 trade:approve 权限")
      }
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
      guard canApproveTrades else {
        throw tradeApprovalUnavailable("当前会话没有 trade:approve 权限")
      }
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
    guard !localSessionLocked, let activeAccountID = authenticatedUser?.activeAccountID else {
      throw tradeApprovalUnavailable("请先解锁并恢复账户会话")
    }
    return [activeAccountID]
  }

  private func tradeApprovalUnavailable(_ message: String) -> TradeApprovalRepositoryError {
    .rejected(code: "TRADE_APPROVAL_UNAVAILABLE", message: message)
  }

  var canPlaceManualOrders: Bool {
    manualTradingStore.canPlaceManualOrders
  }

  var manualOrderAvailabilityMessage: String? {
    manualTradingStore.manualOrderAvailabilityMessage
  }

  var manualOrderInProgress: Bool {
    manualTradingStore.manualOrderInProgress
  }

  var primaryTradingAccountID: String? {
    guard let accountID = portfolioState.snapshot?.account.id,
      authenticatedUser?.activeAccountID == accountID
    else {
      return nil
    }
    return accountID
  }

  func openManualOrder(
    instrumentCode: String,
    direction: ManualOrderDirection
  ) {
    pendingManualOrderDraft = ManualOrderDraftLink(
      id: UUID(),
      instrumentCode: instrumentCode,
      direction: direction
    )
    selectedTab = .trade
  }

  func consumePendingManualOrderDraft() -> ManualOrderDraftLink? {
    defer { pendingManualOrderDraft = nil }
    return pendingManualOrderDraft
  }

  func handleNotificationNavigation(_ request: NotificationNavigationRequest) async {
    guard !localSessionLocked, authenticatedUser != nil else {
      await notificationStore.receive(notificationEventID: request.eventID)
      return
    }
    switch request.destination {
    case .today:
      selectedTab = .today
      await refreshAllReadOnlySnapshots()
    case .tradingOrders:
      pendingNotificationTradeRoute = request
      selectedTab = .trade
      await refreshTradingActivity()
    case .tradingSafety:
      pendingNotificationTradeRoute = request
      selectedTab = .trade
      await refreshPortfolio()
      await refreshTradingActivity()
    case .quant:
      selectedTab = .quant
      await refreshStrategies()
      await refreshTTradeAssistant()
      await refreshLimitUpBoard()
    case .systemStatus:
      pendingNotificationSystemRoute = request
      selectedTab = .today
      await refreshHealth()
    }
  }

  func consumePendingNotificationTradeRoute() -> NotificationNavigationRequest? {
    defer { pendingNotificationTradeRoute = nil }
    return pendingNotificationTradeRoute
  }

  func consumePendingNotificationSystemRoute() -> NotificationNavigationRequest? {
    defer { pendingNotificationSystemRoute = nil }
    return pendingNotificationSystemRoute
  }

  func previewManualOrder(
    instrumentCode: String,
    direction: ManualOrderDirection,
    quoteType: ManualOrderQuoteType,
    executionMode: ManualOrderExecutionMode,
    volume: Int,
    limitPrice: Double?,
    idempotencyKey: UUID
  ) async throws -> ManualOrderPreviewTicket {
    try await manualTradingStore.preview(
      instrumentCode: instrumentCode,
      direction: direction,
      quoteType: quoteType,
      executionMode: executionMode,
      volume: volume,
      limitPrice: limitPrice,
      idempotencyKey: idempotencyKey
    )
  }

  func confirmManualOrder(
    _ preview: ManualOrderPreviewTicket
  ) async throws -> ManualOrderQueueConfirmation {
    try await manualTradingStore.confirm(preview)
  }

  private struct WatchlistMutationContext {
    let accountID: String
    let authorizedAccountIDs: Set<String>
    let repository: any MarketDataLoading
    let snapshot: MarketWorkspaceSnapshot
  }

  private func prepareWatchlistMutation() throws -> WatchlistMutationContext {
    if watchlistMutationInProgress {
      throw WatchlistMutationError.alreadyInProgress
    }
    guard !marketRefreshInProgress else {
      let error = WatchlistMutationError.unavailable("行情正在刷新，请稍后再调整自选")
      watchlistMutationErrorMessage = error.localizedDescription
      throw error
    }
    if let reason = watchlistEditingUnavailableReason {
      let error = WatchlistMutationError.unavailable(reason)
      watchlistMutationErrorMessage = error.localizedDescription
      throw error
    }
    guard
      let user = authenticatedUser,
      let accountID = user.activeAccountID,
      user.authorizedAccountIDs == [accountID],
      let repository = marketRepository,
      let snapshot = marketState.snapshot,
      snapshot.accountID == accountID,
      portfolioState.snapshot?.account.id == accountID
    else {
      let error = WatchlistMutationError.accountScopeMismatch
      watchlistMutationErrorMessage = error.localizedDescription
      throw error
    }
    watchlistMutationInProgress = true
    watchlistMutationErrorMessage = nil
    return WatchlistMutationContext(
      accountID: accountID,
      authorizedAccountIDs: [accountID],
      repository: repository,
      snapshot: snapshot
    )
  }

  private func failWatchlistPreparation(_ error: Error) {
    watchlistMutationInProgress = false
    watchlistMutationErrorMessage = watchlistMutationMessage(error)
  }

  private func performWatchlistMutation<Result>(
    context: WatchlistMutationContext,
    optimisticSnapshot: MarketWorkspaceSnapshot,
    operation: @MainActor (any MarketDataLoading, Set<String>) async throws -> Result
  ) async throws -> Result {
    do {
      return try await operation(context.repository, context.authorizedAccountIDs)
    } catch ReadOnlyRepositoryError.unauthenticated {
      try await refreshAccessSession()
      guard
        hasPermission("watchlist:write"),
        hasPermission("market:read"),
        hasPermission("portfolio:read"),
        let user = authenticatedUser,
        user.activeAccountID == context.accountID,
        user.authorizedAccountIDs == [context.accountID],
        let repository = marketRepository
      else {
        throw WatchlistMutationError.unavailable(
          "会话刷新后不再具有 watchlist:write 权限，自选保持只读"
        )
      }
      await refreshPortfolio()
      guard portfolioState.snapshot?.account.id == context.accountID else {
        throw WatchlistMutationError.accountScopeMismatch
      }
      marketState = .loaded(optimisticSnapshot, refreshWarning: nil)
      return try await operation(repository, [context.accountID])
    }
  }

  private func rollbackWatchlistMutation(
    _ error: Error,
    to previousSnapshot: MarketWorkspaceSnapshot
  ) {
    if hasPermission("market:read"),
      hasPermission("portfolio:read"),
      authenticatedUser?.activeAccountID == previousSnapshot.accountID
    {
      if error is CancellationError {
        marketState = .loaded(previousSnapshot, refreshWarning: nil)
      } else {
        marketState = .loaded(
          previousSnapshot,
          refreshWarning: "自选变更失败，已恢复服务端上次确认的列表。\(watchlistMutationMessage(error))"
        )
      }
    }
    watchlistMutationErrorMessage =
      error is CancellationError
      ? nil
      : watchlistMutationMessage(error)
  }

  private func watchlistMutationMessage(_ error: Error) -> String {
    (error as? LocalizedError)?.errorDescription ?? "自选变更暂时不可用"
  }

  private func activate(
    _ session: AuthenticatedSession,
    configuration: APIConfiguration,
    persistTokens: Bool = true,
    previousAuthorization: SessionUser? = nil
  ) async throws {
    try validateNativeSessionUser(
      session.user,
      previousAuthorization: previousAuthorization
    )
    let grantedScopes = Set(session.user.permissions)
    let newApolloSession = try apolloSessionFactory(
      configuration,
      session.tokens.accessToken
    )
    let notificationIdentity = PushNotificationSessionIdentity(
      userID: session.user.id,
      deviceSessionID: session.tokens.deviceSessionID,
      activeAccountID: session.user.activeAccountID ?? "",
      authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
      grantedScopes: grantedScopes
    )
    let pushRepository =
      grantedScopes.contains(NativeSessionScope.notificationManage.rawValue)
      ? pushNotificationLoaderFactory(newApolloSession)
      : nil
    if persistTokens {
      try await tokenStore.save(session.tokens)
    }
    if let oldApolloSession = apolloSession {
      try? await oldApolloSession.clearCache()
      await oldApolloSession.pauseSubscriptions()
    }
    apolloSession = newApolloSession
    portfolioRepository =
      grantedScopes.contains("portfolio:read")
      ? portfolioLoaderFactory(newApolloSession)
      : nil
    strategyRepository =
      grantedScopes.contains("strategy:read")
      ? strategyLoaderFactory(newApolloSession)
      : nil
    tradingRepository =
      grantedScopes.contains("orders:read")
      ? tradingLoaderFactory(newApolloSession)
      : nil
    tTradeAssistantRepository =
      grantedScopes.contains("strategy:read")
      ? tTradeAssistantLoaderFactory(newApolloSession)
      : nil
    limitUpBoardRepository =
      grantedScopes.contains("strategy:read")
      ? limitUpBoardLoaderFactory(newApolloSession)
      : nil
    tradeApprovalRepository =
      grantedScopes.contains("trade:approve")
      ? tradeApprovalLoaderFactory(newApolloSession)
      : nil
    let manualOrderRepository =
      grantedScopes.contains("trade:manual")
        && grantedScopes.contains("market:read")
      ? manualOrderLoaderFactory(newApolloSession)
      : nil
    let cancellationRepository =
      grantedScopes.contains("trade:manual")
      ? orderCancellationLoaderFactory(newApolloSession)
      : nil
    marketRepository =
      grantedScopes.contains("market:read")
      ? marketLoaderFactory(newApolloSession)
      : nil
    liquidationStore.activate(
      identity: LiquidationStore.SessionIdentity(
        userID: session.user.id,
        deviceSessionID: session.tokens.deviceSessionID,
        activeAccountID: session.user.activeAccountID,
        authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
        grantedScopes: grantedScopes
      ),
      repository: grantedScopes.contains("liquidation:control")
        ? liquidationLoaderFactory(newApolloSession)
        : nil
    )
    exitPlanWorkspace.activate(
      identity: ExitPlanWorkspace.SessionIdentity(
        userID: session.user.id,
        deviceSessionID: session.tokens.deviceSessionID,
        activeAccountID: session.user.activeAccountID,
        authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
        grantedScopes: grantedScopes
      ),
      repository: grantedScopes.contains("orders:read")
        ? exitPlanLoaderFactory(newApolloSession)
        : nil
    )
    tTradeControlStore.activate(
      identity: TTradeControlStore.SessionIdentity(
        userID: session.user.id,
        deviceSessionID: session.tokens.deviceSessionID,
        activeAccountID: session.user.activeAccountID,
        authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
        grantedScopes: grantedScopes
      ),
      repository: grantedScopes.contains("strategy:read")
        || grantedScopes.contains("t-trade:control")
        ? tTradeControlLoaderFactory(newApolloSession)
        : nil
    )
    manualTradingStore.activate(
      identity: ManualTradingStore.SessionIdentity(
        userID: session.user.id,
        deviceSessionID: session.tokens.deviceSessionID,
        activeAccountID: session.user.activeAccountID,
        authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
        grantedScopes: grantedScopes
      ),
      manualOrderRepository: manualOrderRepository,
      cancellationRepository: cancellationRepository
    )
    strategyWorkspace.activate(
      identity: StrategyWorkspace.SessionIdentity(
        userID: session.user.id,
        deviceSessionID: session.tokens.deviceSessionID,
        activeAccountID: session.user.activeAccountID,
        authorizedAccountIDs: Set(session.user.authorizedAccountIDs),
        grantedScopes: grantedScopes
      ),
      repository: strategyRepository as? any StrategyWorkspaceLoading
    )
    portfolioState =
      grantedScopes.contains("portfolio:read")
      ? .idle
      : .unavailable("当前会话没有 portfolio:read 权限")
    strategyState =
      grantedScopes.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    tradingState =
      grantedScopes.contains("orders:read")
      ? .idle
      : .unavailable("当前会话没有 orders:read 权限")
    tTradeAssistantState =
      grantedScopes.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    limitUpBoardState =
      grantedScopes.contains("strategy:read")
      ? .idle
      : .unavailable("当前会话没有 strategy:read 权限")
    if !grantedScopes.contains("market:read") {
      marketState = .unavailable("当前会话没有 market:read 权限")
    } else if !grantedScopes.contains("portfolio:read") {
      marketState = .unavailable("自选列表需要 portfolio:read 权限")
    } else {
      marketState = .idle
    }
    if !watchlistMutationInProgress {
      watchlistMutationErrorMessage = nil
    }
    localSessionLocked = false
    localUnlockErrorMessage = nil
    authenticationState = .authenticated(session.user)
    await notificationStore.activate(
      identity: notificationIdentity,
      repository: pushRepository,
      localSessionUnlocked: true
    )
  }

  private func refreshAccessSession() async throws {
    guard let sessionClient,
      let configuration,
      let tokens = try await tokenStore.load(),
      tokens.refreshTokenExpiresAt > Date()
    else {
      throw PortfolioRepository.RepositoryError.unauthenticated
    }
    let previousAuthorization = authenticatedUser
    let refreshed = try await sessionClient.refresh(
      refreshToken: tokens.refreshToken
    )
    try validateNativeSessionUser(
      refreshed.user,
      previousAuthorization: previousAuthorization
    )
    try await tokenStore.save(refreshed.tokens)
    let currentUser = try await sessionClient.current(
      accessToken: refreshed.tokens.accessToken
    )
    let verified = AuthenticatedSession(
      tokens: refreshed.tokens,
      user: currentUser
    )
    try await activate(
      verified,
      configuration: configuration,
      persistTokens: false,
      previousAuthorization: previousAuthorization
    )
  }

  private func validateNativeSessionUser(
    _ user: SessionUser,
    previousAuthorization: SessionUser?
  ) throws {
    guard
      let activeAccountID = user.activeAccountID,
      !activeAccountID.isEmpty,
      activeAccountID == activeAccountID.trimmingCharacters(in: .whitespacesAndNewlines),
      user.authorizedAccountIDs == [activeAccountID],
      Set(user.permissions).count == user.permissions.count,
      user.permissions.allSatisfy({ NativeSessionScope.v1AllowedValues.contains($0) })
    else {
      throw SessionClient.ClientError.invalidResponse
    }
    if let previousAuthorization {
      guard
        previousAuthorization.activeAccountID == activeAccountID,
        Set(user.permissions).isSubset(of: Set(previousAuthorization.permissions))
      else {
        throw SessionClient.ClientError.invalidResponse
      }
    }
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
    if case SessionClient.ClientError.invalidResponse = error {
      await clearLocalSession()
      authenticationState = .failed(error.localizedDescription)
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
    liquidationStore.clearSession()
    exitPlanWorkspace.clearSession()
    tTradeControlStore.clearSession()
    manualTradingStore.clearSession()
    strategyWorkspace.clearSession()
    notificationStore.clearSession()
    tradeApprovalInProgress = false
    watchlistMutationInProgress = false
    watchlistMutationErrorMessage = nil
    pendingManualOrderDraft = nil
    pendingNotificationTradeRoute = nil
    pendingNotificationSystemRoute = nil
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
          if phase == .background {
            liquidationStore.invalidateChallengeContext()
            exitPlanWorkspace.invalidateAuthorizationContext()
            tTradeControlStore.invalidateChallengeContext()
            strategyWorkspace.invalidateControlContext()
          }
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
        await notificationStore.refreshSystemAuthorization()
        await refreshHealth()
        if localSessionLocked {
          await unlockLocalSession()
        } else {
          await resumeSessionAfterForeground()
        }
      }
    case .inactive, .background:
      privacyShieldVisible = true
      if phase == .background {
        liquidationStore.invalidateChallengeContext()
        exitPlanWorkspace.invalidateAuthorizationContext()
        tTradeControlStore.invalidateChallengeContext()
        strategyWorkspace.invalidateControlContext()
      }
      if case .authenticated = authenticationState {
        localSessionLocked = true
        notificationStore.lockLocalSession()
      }
      Task { await apolloSession?.pauseSubscriptions() }
    @unknown default:
      privacyShieldVisible = true
    }
  }

  private func configureLiquidationStore() {
    liquidationStore.configure(
      contextProvider: { [weak self] in
        guard let self else {
          return LiquidationPortfolioContext(
            accountID: nil,
            instrumentCodes: [],
            localSessionLocked: true,
            accountDataEnabled: false
          )
        }
        let snapshot = self.portfolioState.snapshot
        return LiquidationPortfolioContext(
          accountID: snapshot?.account.id,
          instrumentCodes: Set(
            snapshot?.positions
              .filter { $0.volume > 0 }
              .map(\.stockCode) ?? []
          ),
          localSessionLocked: self.localSessionLocked,
          accountDataEnabled: self.accountDataEnabled
        )
      },
      refreshSession: { [weak self] in
        guard let self else {
          throw LiquidationStoreError.unavailable("个人账户会话已释放")
        }
        try await self.refreshAccessSession()
        await self.refreshPortfolio()
      },
      refreshReadModels: { [weak self] in
        guard let self else { return }
        await self.refreshPortfolio()
        await self.refreshTradingActivity()
      }
    )
  }

  private func configureExitPlanWorkspace() {
    exitPlanWorkspace.configure(
      contextProvider: { [weak self] in
        guard let self else {
          return ExitPlanRuntimeContext(
            accountID: nil,
            localSessionLocked: true,
            accountDataEnabled: false
          )
        }
        return ExitPlanRuntimeContext(
          accountID: self.portfolioState.snapshot?.account.id,
          localSessionLocked: self.localSessionLocked,
          accountDataEnabled: self.accountDataEnabled
        )
      },
      refreshSession: { [weak self] in
        guard let self else {
          throw ExitPlanWorkspaceError.unavailable("个人账户会话已释放")
        }
        try await self.refreshAccessSession()
        await self.refreshPortfolio()
      },
      refreshTradingTruth: { [weak self] in
        guard let self else { return }
        await self.refreshPortfolio()
        await self.refreshTradingActivity()
      }
    )
  }

  private func configureTTradeControlStore() {
    tTradeControlStore.configure(
      contextProvider: { [weak self] in
        guard let self else {
          return TTradeControlRuntimeContext(
            accountID: nil,
            localSessionLocked: true,
            accountDataEnabled: false
          )
        }
        return TTradeControlRuntimeContext(
          accountID: self.portfolioState.snapshot?.account.id,
          localSessionLocked: self.localSessionLocked,
          accountDataEnabled: self.accountDataEnabled
        )
      },
      refreshSession: { [weak self] in
        guard let self else {
          throw TTradeControlError.unavailable("个人账户会话已释放")
        }
        try await self.refreshAccessSession()
        await self.refreshPortfolio()
      },
      refreshAssistantProjection: { [weak self] in
        guard let self else { return }
        await self.refreshTTradeAssistant()
      }
    )
  }

  private func configureManualTradingStore() {
    manualTradingStore.configure(
      contextProvider: { [weak self] in
        guard let self else {
          return ManualTradingRuntimeContext(
            accountID: nil,
            todayOrders: [],
            hasTradingSnapshot: false,
            localSessionLocked: true,
            accountDataEnabled: false
          )
        }
        return ManualTradingRuntimeContext(
          accountID: self.tradingState.snapshot?.accountID
            ?? self.portfolioState.snapshot?.account.id,
          todayOrders: self.tradingState.snapshot?.todayOrders ?? [],
          hasTradingSnapshot: self.tradingState.snapshot != nil,
          localSessionLocked: self.localSessionLocked,
          accountDataEnabled: self.accountDataEnabled
        )
      },
      refreshSession: { [weak self] in
        guard let self else {
          throw ManualTradingStoreError.unavailable("个人账户会话已释放")
        }
        try await self.refreshAccessSession()
        await self.refreshPortfolio()
        await self.refreshTradingActivity()
      },
      refreshReadModels: { [weak self] in
        guard let self else { return }
        await self.refreshTradingActivity()
        await self.refreshPortfolio()
      }
    )
  }

  private func configureStrategyWorkspace() {
    strategyWorkspace.configure(
      contextProvider: { [weak self] in
        guard let self else {
          return StrategyWorkspaceRuntimeContext(
            accountID: nil,
            localSessionLocked: true,
            accountDataEnabled: false
          )
        }
        return StrategyWorkspaceRuntimeContext(
          accountID: self.portfolioState.snapshot?.account.id,
          localSessionLocked: self.localSessionLocked,
          accountDataEnabled: self.accountDataEnabled
        )
      },
      refreshSession: { [weak self] in
        guard let self else {
          throw StrategyWorkspaceError.unavailable("个人账户会话已释放")
        }
        try await self.refreshAccessSession()
        await self.refreshPortfolio()
      },
      refreshStrategies: { [weak self] in
        guard let self else { return }
        await self.refreshStrategies()
      }
    )
  }

  private func configureNotificationStore() {
    notificationStore.configure(sessionRefresh: { [weak self] in
      guard let self else {
        throw PushNotificationRepositoryError.unauthenticated
      }
      try await self.refreshAccessSession()
    })
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
    private func startWatchlistReadOnlyUITestFixture() {
      let accountID = "UI-WATCHLIST-ACCOUNT"
      let updatedAt = Date(timeIntervalSince1970: 1_786_752_000)
      authenticationState = .authenticated(
        SessionUser(
          id: "watchlist-ui-user",
          username: "watchlist-ui-user",
          displayName: "自选只读测试用户",
          permissions: ["portfolio:read", "market:read"],
          authorizedAccountIDs: [accountID]
        )
      )
      serviceState = .failed("UI 测试未连接服务")
      portfolioState = .failed("UI 测试未连接账户服务")
      marketState = .loaded(
        MarketWorkspaceSnapshot(
          accountID: accountID,
          watchlist: [
            MarketWatchItem(
              id: "watchlist-ui-1",
              accountID: accountID,
              stockCode: "600519.SH",
              instrumentName: "贵州茅台",
              displayOrder: 1,
              groupName: nil,
              note: nil,
              updatedAt: updatedAt,
              quote: MarketQuote(
                stockCode: "600519.SH",
                time: updatedAt,
                lastPrice: 1_598.50,
                open: 1_590,
                high: 1_605,
                low: 1_582,
                preClose: 1_588,
                change: 10.50,
                changePercent: 0.0066,
                volume: 12_300,
                amount: 1_965_000_000,
                turnoverRate: 0.004
              )
            ),
            MarketWatchItem(
              id: "watchlist-ui-2",
              accountID: accountID,
              stockCode: "000001.SZ",
              instrumentName: "平安银行",
              displayOrder: 2,
              groupName: nil,
              note: nil,
              updatedAt: updatedAt,
              quote: nil
            ),
          ],
          fetchedAt: updatedAt
        ),
        refreshWarning: nil
      )
      selectedTab = .market
    }

    private func startTransientRealBackendUITestSession() async {
      await refreshHealth()
      guard let configuration else {
        authenticationState = .failed("真实后端 UI 测试配置无效")
        return
      }
      do {
        guard
          let session = try DebugRealBackendUITestSession.make(
            arguments: ProcessInfo.processInfo.arguments,
            environment: ProcessInfo.processInfo.environment
          )
        else {
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

  private func activeAccountIDs(for user: SessionUser) -> Set<String> {
    user.activeAccountID.map { [$0] } ?? []
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
