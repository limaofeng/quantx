import XCTest

@testable import QuantX

@MainActor
final class AppModelWatchlistTests: XCTestCase {
  func testMissingWritePermissionKeepsWatchlistReadOnly() async {
    let initial = makeSnapshot(["600519.SH"])
    let loader = WatchlistLoaderSpy(loadSnapshots: [initial])
    let model = makeModel(
      grantedScopes: ["portfolio:read", "market:read"],
      loader: loader
    )
    await model.restoreSession(requireLocalUnlock: false)

    XCTAssertFalse(model.canEditWatchlist)
    XCTAssertEqual(
      model.watchlistEditingUnavailableReason,
      "当前会话没有 watchlist:write 权限，自选保持只读"
    )
    do {
      try await model.addToWatchlist(makeInstrument("000001.SZ"))
      XCTFail("缺少 watchlist:write 权限时不应写入")
    } catch let error as WatchlistMutationError {
      XCTAssertEqual(
        error,
        .unavailable("当前会话没有 watchlist:write 权限，自选保持只读")
      )
    } catch {
      XCTFail("收到意外错误：\(error)")
    }
    XCTAssertEqual(loader.saveCount, 0)
    XCTAssertEqual(model.marketState.snapshot, initial)
  }

  func testAddIsOptimisticAndRefreshesToAuthoritativeServerOrder() async {
    let initial = makeSnapshot(["600519.SH"])
    let refreshed = makeSnapshot(["000001.SZ", "600519.SH"])
    let loader = WatchlistLoaderSpy(loadSnapshots: [initial, refreshed])
    let model = makeModel(loader: loader)
    await model.restoreSession(requireLocalUnlock: false)

    let mutation = Task { @MainActor in
      try await model.addToWatchlist(makeInstrument("000001.SZ"))
    }
    await waitUntil { loader.saveCount == 1 }

    XCTAssertEqual(
      model.marketState.snapshot?.watchlist.map(\.stockCode),
      ["600519.SH", "000001.SZ"]
    )
    XCTAssertTrue(model.watchlistMutationInProgress)
    XCTAssertEqual(loader.lastMutationAccountID, "ACCOUNT-1")
    XCTAssertEqual(loader.lastAuthorizedAccountIDs, ["ACCOUNT-1"])

    loader.succeedSave(makeItem("000001.SZ", displayOrder: 2))
    do {
      try await mutation.value
    } catch {
      XCTFail("添加自选不应失败：\(error)")
    }

    XCTAssertEqual(
      model.marketState.snapshot?.watchlist.map(\.stockCode),
      ["000001.SZ", "600519.SH"]
    )
    XCTAssertEqual(loader.loadCount, 2)
    XCTAssertFalse(model.watchlistMutationInProgress)
  }

  func testRemoveFailureRollsBackOptimisticState() async {
    let initial = makeSnapshot(["600519.SH", "000001.SZ"])
    let loader = WatchlistLoaderSpy(loadSnapshots: [initial])
    let model = makeModel(loader: loader)
    await model.restoreSession(requireLocalUnlock: false)

    let mutation = Task { @MainActor in
      try await model.removeFromWatchlist(stockCode: "600519.SH")
    }
    await waitUntil { loader.removeCount == 1 }

    XCTAssertEqual(
      model.marketState.snapshot?.watchlist.map(\.stockCode),
      ["000001.SZ"]
    )
    XCTAssertEqual(loader.lastMutationAccountID, "ACCOUNT-1")
    XCTAssertEqual(loader.lastAuthorizedAccountIDs, ["ACCOUNT-1"])

    loader.failRemove(.rejected("服务端拒绝移除"))
    do {
      try await mutation.value
      XCTFail("服务端拒绝后应向调用方返回错误")
    } catch let error as WatchlistMutationError {
      XCTAssertEqual(error, .rejected("服务端拒绝移除"))
    } catch {
      XCTFail("收到意外错误：\(error)")
    }

    XCTAssertEqual(model.marketState.snapshot, initial)
    XCTAssertEqual(model.watchlistMutationErrorMessage, "服务端拒绝移除")
    XCTAssertFalse(model.watchlistMutationInProgress)
  }

  func testReorderIsOptimisticThenUsesRefreshedServerOrderAsTruth() async {
    let initial = makeSnapshot(["600519.SH", "000001.SZ", "300750.SZ"])
    let refreshed = makeSnapshot(["300750.SZ", "600519.SH", "000001.SZ"])
    let loader = WatchlistLoaderSpy(loadSnapshots: [initial, refreshed])
    let model = makeModel(loader: loader)
    await model.restoreSession(requireLocalUnlock: false)

    let requested = ["000001.SZ", "600519.SH", "300750.SZ"]
    let mutation = Task { @MainActor in
      try await model.reorderWatchlist(stockCodes: requested)
    }
    await waitUntil { loader.reorderCount == 1 }

    XCTAssertEqual(model.marketState.snapshot?.watchlist.map(\.stockCode), requested)
    XCTAssertEqual(loader.lastReorderCodes, requested)
    XCTAssertEqual(loader.lastMutationAccountID, "ACCOUNT-1")

    loader.succeedReorder(
      makeSnapshot(["000001.SZ", "300750.SZ", "600519.SH"]).watchlist
    )
    do {
      try await mutation.value
    } catch {
      XCTFail("排序不应失败：\(error)")
    }

    XCTAssertEqual(
      model.marketState.snapshot?.watchlist.map(\.stockCode),
      ["300750.SZ", "600519.SH", "000001.SZ"]
    )
    XCTAssertEqual(loader.loadCount, 2)
    XCTAssertFalse(model.watchlistMutationInProgress)
  }

  private func makeModel(
    grantedScopes: [String] = ["portfolio:read", "market:read", "watchlist:write"],
    loader: WatchlistLoaderSpy
  ) -> AppModel {
    let user = SessionUser(
      id: "watchlist-user",
      username: "watchlist-user",
      displayName: "自选用户",
      permissions: grantedScopes,
      authorizedAccountIDs: ["ACCOUNT-1"]
    )
    return AppModel(
      configuration: APIConfiguration(
        environment: .staging,
        graphQLHTTPURL: URL(string: "https://quantx.test/graphql")!,
        graphQLWebSocketURL: URL(string: "wss://quantx.test/graphql")!,
        healthURL: URL(string: "https://quantx.test/health")!,
        authBaseURL: URL(string: "https://quantx.test")!,
        accountDataEnabled: true
      ),
      sessionClient: WatchlistSessionService(user: user),
      tokenStore: WatchlistTokenStore(),
      localAuthentication: WatchlistLocalAuthentication(),
      portfolioLoaderFactory: { _ in
        WatchlistPortfolioLoader(snapshot: self.makePortfolio())
      },
      marketLoaderFactory: { _ in loader }
    )
  }

  private func makeSnapshot(_ stockCodes: [String]) -> MarketWorkspaceSnapshot {
    MarketWorkspaceSnapshot(
      accountID: "ACCOUNT-1",
      watchlist: stockCodes.enumerated().map { index, code in
        makeItem(code, displayOrder: index + 1)
      },
      fetchedAt: Date(timeIntervalSince1970: 1_786_752_000)
    )
  }

  private func makeItem(_ stockCode: String, displayOrder: Int) -> MarketWatchItem {
    MarketWatchItem(
      id: "item-\(stockCode)",
      accountID: "ACCOUNT-1",
      stockCode: stockCode,
      instrumentName: stockCode,
      displayOrder: displayOrder,
      note: nil,
      updatedAt: Date(timeIntervalSince1970: 1_786_752_000),
      quote: nil
    )
  }

  private func makeInstrument(_ stockCode: String) -> MarketInstrument {
    MarketInstrument(
      stockCode: stockCode,
      market: nil,
      instrumentID: stockCode,
      name: stockCode,
      abbreviation: nil,
      exchangeCode: nil,
      previousClose: nil,
      upperLimit: nil,
      lowerLimit: nil,
      priceTick: nil,
      isTrading: true,
      quote: nil
    )
  }

  private func makePortfolio() -> PortfolioSnapshot {
    let date = Date(timeIntervalSince1970: 1_786_752_000)
    return PortfolioSnapshot(
      account: PortfolioAccount(
        id: "ACCOUNT-1",
        name: "主账户",
        type: "STOCK",
        totalAsset: 100_000,
        cash: 100_000,
        frozenCash: 0,
        marketValue: 0,
        totalProfitLoss: 0,
        profitLossPercent: 0,
        updatedAt: date
      ),
      metrics: PortfolioMetrics(
        accountID: "ACCOUNT-1",
        accountName: "主账户",
        totalAsset: 100_000,
        cash: 100_000,
        marketValue: 0,
        totalProfitLoss: 0,
        totalProfitLossPercent: 0,
        todayProfitLoss: 0,
        todayProfitLossPercent: 0,
        positionCount: 0,
        updatedAt: date
      ),
      positions: [],
      fetchedAt: date
    )
  }

  private func waitUntil(
    _ condition: @escaping @MainActor () -> Bool
  ) async {
    for _ in 0..<1_000 {
      if condition() { return }
      await Task.yield()
    }
    XCTFail("等待异步自选操作超时")
  }
}

@MainActor
private final class WatchlistLoaderSpy: MarketDataLoading {
  private var loadSnapshots: [MarketWorkspaceSnapshot]
  private var lastSnapshot: MarketWorkspaceSnapshot
  private var saveContinuation: CheckedContinuation<MarketWatchItem, any Error>?
  private var removeContinuation: CheckedContinuation<Void, any Error>?
  private var reorderContinuation: CheckedContinuation<[MarketWatchItem], any Error>?

  private(set) var loadCount = 0
  private(set) var saveCount = 0
  private(set) var removeCount = 0
  private(set) var reorderCount = 0
  private(set) var lastMutationAccountID: String?
  private(set) var lastAuthorizedAccountIDs: Set<String>?
  private(set) var lastReorderCodes: [String]?

  init(loadSnapshots: [MarketWorkspaceSnapshot]) {
    precondition(!loadSnapshots.isEmpty)
    self.loadSnapshots = loadSnapshots
    lastSnapshot = loadSnapshots[0]
  }

  func loadWatchlist(
    accountID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> MarketWorkspaceSnapshot {
    loadCount += 1
    lastMutationAccountID = accountID
    lastAuthorizedAccountIDs = authorizedAccountIDs
    if !loadSnapshots.isEmpty {
      lastSnapshot = loadSnapshots.removeFirst()
    }
    return lastSnapshot
  }

  func search(term _: String) async throws -> [MarketInstrument] { [] }

  func loadInstrument(
    stockCode _: String,
    period _: MarketPeriod
  ) async throws -> MarketInstrumentSnapshot? { nil }

  func saveWatchlistItem(
    accountID: String,
    stockCode _: String,
    instrumentName _: String?,
    authorizedAccountIDs: Set<String>
  ) async throws -> MarketWatchItem {
    saveCount += 1
    lastMutationAccountID = accountID
    lastAuthorizedAccountIDs = authorizedAccountIDs
    return try await withCheckedThrowingContinuation { continuation in
      saveContinuation = continuation
    }
  }

  func removeWatchlistItem(
    accountID: String,
    stockCode _: String,
    authorizedAccountIDs: Set<String>
  ) async throws {
    removeCount += 1
    lastMutationAccountID = accountID
    lastAuthorizedAccountIDs = authorizedAccountIDs
    try await withCheckedThrowingContinuation { continuation in
      removeContinuation = continuation
    }
  }

  func reorderWatchlistItems(
    accountID: String,
    itemIDs: [String],
    authorizedAccountIDs: Set<String>
  ) async throws -> [MarketWatchItem] {
    reorderCount += 1
    lastMutationAccountID = accountID
    lastAuthorizedAccountIDs = authorizedAccountIDs
    lastReorderCodes = itemIDs.compactMap { itemID in
      lastSnapshot.watchlist.first(where: { $0.id == itemID })?.stockCode
    }
    return try await withCheckedThrowingContinuation { continuation in
      reorderContinuation = continuation
    }
  }

  func quoteUpdates(
    stockCode _: String
  ) throws -> AsyncThrowingStream<MarketLiveQuote, any Error> {
    AsyncThrowingStream { $0.finish() }
  }

  func depthUpdates(
    stockCode _: String
  ) throws -> AsyncThrowingStream<MarketDepthSnapshot, any Error> {
    AsyncThrowingStream { $0.finish() }
  }

  func succeedSave(_ item: MarketWatchItem) {
    let continuation = saveContinuation
    saveContinuation = nil
    continuation?.resume(returning: item)
  }

  func failRemove(_ error: WatchlistMutationError) {
    let continuation = removeContinuation
    removeContinuation = nil
    continuation?.resume(throwing: error)
  }

  func succeedReorder(_ items: [MarketWatchItem]) {
    let continuation = reorderContinuation
    reorderContinuation = nil
    continuation?.resume(returning: items)
  }
}

@MainActor
private final class WatchlistPortfolioLoader: PortfolioLoading {
  let snapshot: PortfolioSnapshot

  init(snapshot: PortfolioSnapshot) {
    self.snapshot = snapshot
  }

  func load(authorizedAccountIDs _: Set<String>) async throws -> PortfolioLoadResult {
    .snapshot(snapshot)
  }
}

private actor WatchlistTokenStore: SessionTokenStore {
  private var tokens = SessionTokens(
    accessToken: "access-token",
    refreshToken: "refresh-token",
    accessTokenExpiresAt: Date().addingTimeInterval(600),
    refreshTokenExpiresAt: Date().addingTimeInterval(3_600),
    deviceSessionID: "watchlist-device"
  )

  func load() -> SessionTokens? { tokens }
  func save(_ tokens: SessionTokens) { self.tokens = tokens }
  func delete() {}
}

private actor WatchlistSessionService: SessionServing {
  let user: SessionUser

  init(user: SessionUser) {
    self.user = user
  }

  func login(
    username _: String,
    password _: String,
    deviceName _: String
  ) throws -> AuthenticatedSession {
    throw SessionClient.ClientError.invalidResponse
  }

  func refresh(refreshToken _: String) throws -> AuthenticatedSession {
    throw SessionClient.ClientError.invalidResponse
  }

  func current(accessToken _: String) -> SessionUser { user }
  func logout(accessToken _: String, allDevices _: Bool) {}
}

@MainActor
private final class WatchlistLocalAuthentication: LocalAuthenticationProviding {
  func unlock(reason _: String) async throws {}
}
