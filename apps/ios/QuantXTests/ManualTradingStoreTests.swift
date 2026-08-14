import XCTest

@testable import QuantX

@MainActor
final class ManualTradingStoreTests: XCTestCase {
  func testLivePreviewFailsClosedWhenCapabilityOnlyAllowsPaper() async {
    let manualOrder = ManualOrderStoreLoaderSpy(
      capabilities: makeCapabilities(executionModes: [.paper], liveReady: false),
      preview: makePreview(executionMode: .paper)
    )
    let harness = makeHarness(manualOrder: manualOrder)
    await harness.store.loadCapabilities(instrumentCode: "600519.SH")

    await XCTAssertThrowsStoreError {
      _ = try await harness.store.preview(
        instrumentCode: "600519.SH",
        direction: .buy,
        quoteType: .limit,
        executionMode: .live,
        volume: 100,
        limitPrice: 10,
        idempotencyKey: UUID()
      )
    }

    XCTAssertEqual(manualOrder.previewCount, 0)
    XCTAssertEqual(
      harness.store.capabilityState.capabilities?.defaultExecutionMode,
      .paper
    )
  }

  func testLiveConfirmationRequiresBiometricsEveryTimeButPaperDoesNot() async throws {
    let authentication = ManualTradingAuthenticationSpy()
    let livePreview = makePreview(executionMode: .live)
    let manualOrder = ManualOrderStoreLoaderSpy(
      capabilities: makeCapabilities(executionModes: [.paper, .live], liveReady: true),
      preview: livePreview
    )
    let harness = makeHarness(
      manualOrder: manualOrder,
      authentication: authentication
    )
    await harness.store.loadCapabilities(instrumentCode: "600519.SH")

    _ = try await harness.store.confirm(livePreview)
    _ = try await harness.store.confirm(livePreview)

    XCTAssertEqual(authentication.tradeAuthorizationCount, 2)

    let paperPreview = makePreview(executionMode: .paper)
    _ = try await harness.store.confirm(paperPreview)
    XCTAssertEqual(authentication.tradeAuthorizationCount, 2)
  }

  func testBeijingBestFailsBeforePreviewEvenWithForgedServerCapability() async {
    let manualOrder = ManualOrderStoreLoaderSpy(
      capabilities: makeCapabilities(
        instrumentCode: "920001.BJ",
        executionModes: [.paper],
        liveReady: false
      ),
      preview: makePreview(
        instrumentCode: "920001.BJ",
        quoteType: .best,
        executionMode: .paper
      )
    )
    let harness = makeHarness(manualOrder: manualOrder)
    await harness.store.loadCapabilities(instrumentCode: "920001.BJ")

    await XCTAssertThrowsStoreError {
      _ = try await harness.store.preview(
        instrumentCode: "920001.BJ",
        direction: .buy,
        quoteType: .best,
        executionMode: .paper,
        volume: 100,
        limitPrice: nil,
        idempotencyKey: UUID()
      )
    }

    XCTAssertEqual(manualOrder.previewCount, 0)
  }

  func testCancellationEligibilityUsesExactBrokerStatusRemainingAndNumericID() {
    XCTAssertTrue(makeOrder(status: "REPORTED", volume: 100, tradedVolume: 0).canCancel)
    XCTAssertTrue(makeOrder(status: "PART_SUCC", volume: 100, tradedVolume: 40).canCancel)
    XCTAssertFalse(makeOrder(status: "PART_SUCC", volume: 100, tradedVolume: 100).canCancel)
    XCTAssertFalse(makeOrder(status: "WAIT_REPORTING", volume: 100, tradedVolume: 0).canCancel)
    XCTAssertFalse(makeOrder(id: "client-order", status: "REPORTED").canCancel)
  }

  func testCancelUsesPrimaryAccountStableIdempotencyAndDebouncesWithoutBiometrics() async throws {
    let order = makeOrder(status: "REPORTED")
    let runtime = ManualTradingRuntimeBox(todayOrders: [order])
    let cancellation = OrderCancellationLoaderSpy(suspends: true)
    let authentication = ManualTradingAuthenticationSpy()
    let harness = makeHarness(
      runtime: runtime,
      cancellation: cancellation,
      authentication: authentication
    )
    let key = UUID()

    let first = Task {
      try await harness.store.cancel(order, idempotencyKey: key)
    }
    await cancellation.waitUntilRequested()

    XCTAssertTrue(harness.store.isCancelling(orderID: order.id))
    await XCTAssertThrowsStoreError {
      _ = try await harness.store.cancel(order, idempotencyKey: UUID())
    }
    cancellation.resume()
    let confirmation = try await first.value

    XCTAssertEqual(cancellation.requests.count, 1)
    XCTAssertEqual(cancellation.requests.first?.accountID, "ACCOUNT-1")
    XCTAssertEqual(cancellation.requests.first?.idempotencyKey, key)
    XCTAssertEqual(confirmation.status, "QUEUED")
    XCTAssertEqual(authentication.tradeAuthorizationCount, 0)
    XCTAssertEqual(runtime.refreshCount, 1)
  }

  func testServiceRaceRefreshesProjectionAndHidesCancellation() async {
    let order = makeOrder(status: "REPORTED")
    let runtime = ManualTradingRuntimeBox(todayOrders: [order])
    runtime.onRefresh = {
      runtime.todayOrders = [self.makeOrder(status: "SUCCEEDED")]
    }
    let cancellation = OrderCancellationLoaderSpy(
      error: OrderCancellationRepositoryError.rejected(
        "订单状态 SUCCEEDED 不允许撤单"
      )
    )
    let harness = makeHarness(runtime: runtime, cancellation: cancellation)

    await XCTAssertThrowsStoreError {
      _ = try await harness.store.cancel(order)
    }

    XCTAssertEqual(runtime.refreshCount, 1)
    XCTAssertFalse(harness.store.canCancel(order))
  }

  func testTransportFailureKeepsStableCancellationIdempotencyKeyForRetry() async throws {
    let order = makeOrder(status: "REPORTED")
    let runtime = ManualTradingRuntimeBox(todayOrders: [order])
    let cancellation = OrderCancellationLoaderSpy(
      errors: [ReadOnlyRepositoryError.transport]
    )
    let harness = makeHarness(runtime: runtime, cancellation: cancellation)

    await XCTAssertThrowsStoreError {
      _ = try await harness.store.cancel(order)
    }
    let firstKey = try XCTUnwrap(cancellation.requests.first?.idempotencyKey)

    _ = try await harness.store.cancel(order)

    XCTAssertEqual(cancellation.requests.count, 2)
    XCTAssertEqual(cancellation.requests[1].idempotencyKey, firstKey)
    XCTAssertEqual(runtime.refreshCount, 2)
  }

  func testExplicitServiceRejectionClearsCancellationIdempotencyKey() async throws {
    let order = makeOrder(status: "REPORTED")
    let runtime = ManualTradingRuntimeBox(todayOrders: [order])
    let cancellation = OrderCancellationLoaderSpy(
      errors: [OrderCancellationRepositoryError.rejected("服务明确拒绝")]
    )
    let harness = makeHarness(runtime: runtime, cancellation: cancellation)
    let rejectedKey = UUID()

    await XCTAssertThrowsStoreError {
      _ = try await harness.store.cancel(
        order,
        idempotencyKey: rejectedKey
      )
    }
    _ = try await harness.store.cancel(order)

    XCTAssertEqual(cancellation.requests.first?.idempotencyKey, rejectedKey)
    XCTAssertNotEqual(cancellation.requests.last?.idempotencyKey, rejectedKey)
  }

  func testSessionRefreshDropsManualScopeAndPreventsCancelRetry() async {
    let order = makeOrder(status: "REPORTED")
    let runtime = ManualTradingRuntimeBox(todayOrders: [order])
    let cancellation = OrderCancellationLoaderSpy(
      errors: [ReadOnlyRepositoryError.unauthenticated]
    )
    let store = ManualTradingStore(
      localAuthentication: ManualTradingAuthenticationSpy()
    )
    store.configure(
      contextProvider: { runtime.context },
      refreshSession: {
        store.activate(
          identity: self.makeIdentity(scopes: ["orders:read"]),
          manualOrderRepository: nil,
          cancellationRepository: nil
        )
      },
      refreshReadModels: { runtime.refresh() }
    )
    store.activate(
      identity: makeIdentity(),
      manualOrderRepository: nil,
      cancellationRepository: cancellation
    )

    await XCTAssertThrowsStoreError {
      _ = try await store.cancel(order)
    }

    XCTAssertEqual(cancellation.requests.count, 1)
    XCTAssertEqual(runtime.refreshCount, 1)
  }

  private func makeHarness(
    runtime: ManualTradingRuntimeBox = ManualTradingRuntimeBox(todayOrders: []),
    manualOrder: ManualOrderStoreLoaderSpy? = nil,
    cancellation: OrderCancellationLoaderSpy? = nil,
    authentication: ManualTradingAuthenticationSpy = ManualTradingAuthenticationSpy()
  ) -> (store: ManualTradingStore, runtime: ManualTradingRuntimeBox) {
    let store = ManualTradingStore(localAuthentication: authentication)
    store.configure(
      contextProvider: { runtime.context },
      refreshSession: {},
      refreshReadModels: { runtime.refresh() }
    )
    store.activate(
      identity: makeIdentity(),
      manualOrderRepository: manualOrder,
      cancellationRepository: cancellation
    )
    return (store, runtime)
  }

  private func makeIdentity(
    scopes: Set<String> = ["market:read", "orders:read", "trade:manual"]
  ) -> ManualTradingStore.SessionIdentity {
    ManualTradingStore.SessionIdentity(
      userID: "user-1",
      deviceSessionID: "device-session-1",
      activeAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"],
      grantedScopes: scopes
    )
  }

  private func makeCapabilities(
    instrumentCode: String = "600519.SH",
    executionModes: Set<ManualOrderExecutionMode>,
    liveReady: Bool
  ) -> ManualOrderEntryCapabilities {
    ManualOrderEntryCapabilities(
      accountID: "ACCOUNT-1",
      instrumentCode: instrumentCode,
      canManualTrade: true,
      executionModes: executionModes,
      supportedDirections: [.buy, .sell],
      supportedQuoteTypes: [.limit, .best],
      liveReady: liveReady,
      liveBlockedReasons: liveReady ? [] : ["实盘未就绪"],
      warnings: []
    )
  }

  private func makePreview(
    instrumentCode: String = "600519.SH",
    quoteType: ManualOrderQuoteType = .limit,
    executionMode: ManualOrderExecutionMode
  ) -> ManualOrderPreviewTicket {
    ManualOrderPreviewTicket(
      id: UUID().uuidString,
      confirmationToken: "memory-only-token",
      accountID: "ACCOUNT-1",
      instrumentCode: instrumentCode,
      direction: .buy,
      quoteType: quoteType,
      requestedVolume: 100,
      finalVolume: 100,
      limitPrice: quoteType == .limit ? 10 : nil,
      referencePrice: 10,
      estimatedAmount: 1_000,
      estimatedFees: 1,
      availableCash: 10_000,
      availableVolume: nil,
      idempotencyKey: UUID(),
      executionMode: executionMode,
      quoteTimestamp: Date(),
      challengeExpiresAt: Date().addingTimeInterval(120),
      riskDecisionID: "risk-1",
      riskAction: "ALLOW",
      riskReasonCode: "ALLOW",
      riskReasonDetail: "允许",
      warnings: []
    )
  }

  private func makeOrder(
    id: String = "42",
    status: String,
    volume: Int = 100,
    tradedVolume: Int = 0
  ) -> OrderRecord {
    OrderRecord(
      id: id,
      systemID: "broker-42",
      stockCode: "600519.SH",
      stockName: "贵州茅台",
      side: "BUY",
      status: status,
      statusMessage: nil,
      price: 1_500,
      volume: volume,
      tradedVolume: tradedVolume,
      tradedPrice: tradedVolume > 0 ? 1_499 : 0,
      strategyName: nil,
      remark: nil,
      submittedAt: Date()
    )
  }
}

@MainActor
private final class ManualTradingRuntimeBox {
  var accountID: String? = "ACCOUNT-1"
  var todayOrders: [OrderRecord]
  var refreshCount = 0
  var onRefresh: (() -> Void)?

  init(todayOrders: [OrderRecord]) {
    self.todayOrders = todayOrders
  }

  var context: ManualTradingRuntimeContext {
    ManualTradingRuntimeContext(
      accountID: accountID,
      todayOrders: todayOrders,
      hasTradingSnapshot: true,
      localSessionLocked: false,
      accountDataEnabled: true
    )
  }

  func refresh() {
    refreshCount += 1
    onRefresh?()
  }
}

@MainActor
private final class ManualOrderStoreLoaderSpy: ManualOrderLoading {
  let capabilitiesResult: ManualOrderEntryCapabilities
  let previewResult: ManualOrderPreviewTicket
  private(set) var previewCount = 0
  private(set) var confirmedPreviews: [ManualOrderPreviewTicket] = []

  init(
    capabilities: ManualOrderEntryCapabilities,
    preview: ManualOrderPreviewTicket
  ) {
    capabilitiesResult = capabilities
    previewResult = preview
  }

  func capabilities(
    instrumentCode _: String,
    accountID _: String,
    authorizedAccountIDs _: Set<String>
  ) async throws -> ManualOrderEntryCapabilities {
    capabilitiesResult
  }

  func preview(
    _ request: ManualOrderRequest,
    authorizedAccountIDs _: Set<String>
  ) async throws -> ManualOrderPreviewTicket {
    previewCount += 1
    return previewResult
  }

  func confirm(
    _ preview: ManualOrderPreviewTicket
  ) async throws -> ManualOrderQueueConfirmation {
    confirmedPreviews.append(preview)
    return ManualOrderQueueConfirmation(
      challengeID: preview.id,
      clientOrderID: UUID().uuidString,
      status: "QUEUED"
    )
  }
}

@MainActor
private final class OrderCancellationLoaderSpy: OrderCancellationLoading {
  private(set) var requests: [OrderCancellationRequest] = []
  private var errors: [Error]
  private let suspends: Bool
  private var continuation: CheckedContinuation<Void, Never>?
  private var requestWaiters: [CheckedContinuation<Void, Never>] = []

  init(
    suspends: Bool = false,
    error: Error? = nil,
    errors: [Error] = []
  ) {
    self.suspends = suspends
    self.errors = error.map { [$0] } ?? errors
  }

  func cancel(
    _ request: OrderCancellationRequest,
    authorizedAccountIDs _: Set<String>
  ) async throws -> OrderCancellationQueueConfirmation {
    requests.append(request)
    requestWaiters.forEach { $0.resume() }
    requestWaiters = []
    if !errors.isEmpty {
      throw errors.removeFirst()
    }
    if suspends {
      await withCheckedContinuation { continuation = $0 }
    }
    return OrderCancellationQueueConfirmation(
      orderID: request.orderID,
      clientOrderID: "cancel-command-1",
      status: "QUEUED"
    )
  }

  func waitUntilRequested() async {
    if !requests.isEmpty { return }
    await withCheckedContinuation { requestWaiters.append($0) }
  }

  func resume() {
    continuation?.resume()
    continuation = nil
  }
}

@MainActor
private final class ManualTradingAuthenticationSpy: LocalAuthenticationProviding {
  private(set) var tradeAuthorizationCount = 0
  var tradeAuthorizationAvailable: Bool { true }

  func unlock(reason _: String) async throws {}

  func authorizeTrade(reason _: String) async throws {
    tradeAuthorizationCount += 1
  }
}

@MainActor
private func XCTAssertThrowsStoreError(
  _ expression: () async throws -> Void,
  file: StaticString = #filePath,
  line: UInt = #line
) async {
  do {
    try await expression()
    XCTFail("Expected error to be thrown", file: file, line: line)
  } catch {}
}
