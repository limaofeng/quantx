@_spi(Unsafe) import ApolloAPI
import XCTest

@testable import QuantX

@MainActor
final class ManualOrderRepositoryTests: XCTestCase {
  func testRequestValidationRequiresCanonicalCodeAndExplicitAuthorizedAccount() {
    XCTAssertThrowsError(
      try ManualOrderRepository.validate(
        makeRequest(instrumentCode: "600519"),
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    )
    XCTAssertThrowsError(
      try ManualOrderRepository.validate(
        makeRequest(accountID: "ACCOUNT-2"),
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    ) { error in
      XCTAssertEqual(error as? ManualOrderRepositoryError, .accountScopeMismatch)
    }
  }

  func testPreviewInputAlwaysCarriesExplicitExecutionMode() {
    let paperInput = ManualOrderRepository.graphQLInput(
      makeRequest(executionMode: .paper)
    )
    let liveInput = ManualOrderRepository.graphQLInput(
      makeRequest(executionMode: .live)
    )

    XCTAssertEqual(paperInput.executionMode?.rawValue, "PAPER")
    XCTAssertEqual(liveInput.executionMode?.rawValue, "LIVE")
  }

  func testBestPriceRequestCannotCarryLimitPrice() {
    XCTAssertThrowsError(
      try ManualOrderRepository.validate(
        makeRequest(quoteType: .best, limitPrice: 10),
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    ) { error in
      XCTAssertEqual(
        error as? ManualOrderRepositoryError,
        .invalidRequest("对手方最优价委托不能携带限价")
      )
    }
  }

  func testBeijingBestPriceRequestIsRejectedEvenIfForgedLocally() {
    XCTAssertThrowsError(
      try ManualOrderRepository.validate(
        makeRequest(
          instrumentCode: "920001.BJ",
          quoteType: .best,
          limitPrice: nil
        ),
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    ) { error in
      XCTAssertEqual(
        error as? ManualOrderRepositoryError,
        .invalidRequest("北交所暂不支持对手方最优价委托")
      )
    }
  }

  func testPreviewMappingRejectsServerAccountSubstitution() {
    let request = makeRequest()
    XCTAssertThrowsError(
      try ManualOrderRepository.mapPreview(
        makeGraphQLPreview(accountID: "ACCOUNT-2", request: request),
        request: request,
        authorizedAccountIDs: ["ACCOUNT-1", "ACCOUNT-2"]
      )
    ) { error in
      XCTAssertEqual(error as? ManualOrderRepositoryError, .accountScopeMismatch)
    }
  }

  func testPreviewMappingKeepsExactBoundContext() throws {
    let request = makeRequest()
    let preview = try ManualOrderRepository.mapPreview(
      makeGraphQLPreview(accountID: request.accountID, request: request),
      request: request,
      authorizedAccountIDs: [request.accountID]
    )

    XCTAssertEqual(preview.accountID, request.accountID)
    XCTAssertEqual(preview.instrumentCode, "600519.SH")
    XCTAssertEqual(preview.direction, .buy)
    XCTAssertEqual(preview.quoteType, .limit)
    XCTAssertEqual(preview.requestedVolume, 100)
    XCTAssertEqual(preview.finalVolume, 100)
    XCTAssertFalse(preview.wasCapped)
    XCTAssertEqual(preview.idempotencyKey, request.idempotencyKey)
    XCTAssertEqual(preview.executionMode, .paper)
    XCTAssertFalse(preview.isExpired())
  }

  func testPreviewMappingRejectsExecutionModeSubstitution() {
    let request = makeRequest(executionMode: .paper)
    XCTAssertThrowsError(
      try ManualOrderRepository.mapPreview(
        makeGraphQLPreview(
          accountID: request.accountID,
          request: request,
          executionMode: "LIVE"
        ),
        request: request,
        authorizedAccountIDs: [request.accountID]
      )
    ) { error in
      XCTAssertEqual(error as? ManualOrderRepositoryError, .contextMismatch)
    }
  }

  func testSellPreviewRequiresAuthoritativeAvailableVolume() {
    let request = makeRequest(direction: .sell)
    XCTAssertThrowsError(
      try ManualOrderRepository.mapPreview(
        makeGraphQLPreview(accountID: request.accountID, request: request),
        request: request,
        authorizedAccountIDs: [request.accountID]
      )
    ) { error in
      XCTAssertEqual(error as? ManualOrderRepositoryError, .invalidResponse)
    }
  }

  func testCapPreviewPreservesRequestedVolumeAndUsesOnlyServerFinalVolume() throws {
    let request = makeRequest(volume: 150)
    let preview = try ManualOrderRepository.mapPreview(
      makeGraphQLPreview(
        accountID: request.accountID,
        request: request,
        finalVolume: 100,
        riskAction: "CAP"
      ),
      request: request,
      authorizedAccountIDs: [request.accountID]
    )

    XCTAssertEqual(preview.requestedVolume, 150)
    XCTAssertEqual(preview.finalVolume, 100)
    XCTAssertTrue(preview.wasCapped)
  }

  func testCapabilitiesKeepPaperAsOnlyDefaultAndExposeReadyLive() throws {
    let capabilities = try ManualOrderRepository.mapCapabilities(
      makeGraphQLCapabilities(
        executionModes: [.paper, .live],
        liveReady: true
      ),
      requestedInstrumentCode: "600519.SH",
      requestedAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"]
    )

    XCTAssertEqual(capabilities.defaultExecutionMode, .paper)
    XCTAssertEqual(capabilities.selectableExecutionModes, [.paper, .live])
    XCTAssertTrue(
      capabilities.supports(
        direction: .buy,
        quoteType: .best,
        executionMode: .live
      )
    )
  }

  func testCapabilitiesHideLiveWhenServerDoesNotReturnReadyLive() throws {
    let capabilities = try ManualOrderRepository.mapCapabilities(
      makeGraphQLCapabilities(executionModes: [.paper], liveReady: false),
      requestedInstrumentCode: "600519.SH",
      requestedAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"]
    )

    XCTAssertEqual(capabilities.selectableExecutionModes, [.paper])
    XCTAssertFalse(capabilities.canSelectLive)
  }

  func testCapabilitiesFailClosedForUnknownEnum() {
    let value = makeGraphQLCapabilities(
      rawExecutionModes: [
        GraphQLEnum(QuantXAPI.ManualOrderExecutionMode.paper),
        GraphQLEnum<QuantXAPI.ManualOrderExecutionMode>(rawValue: "SHADOW"),
      ],
      liveReady: false
    )

    XCTAssertThrowsError(
      try ManualOrderRepository.mapCapabilities(
        value,
        requestedInstrumentCode: "600519.SH",
        requestedAccountID: "ACCOUNT-1",
        authorizedAccountIDs: ["ACCOUNT-1"]
      )
    ) { error in
      XCTAssertEqual(error as? ManualOrderRepositoryError, .invalidResponse)
    }
  }

  func testBeijingCapabilitiesNeverPermitBest() throws {
    let capabilities = try ManualOrderRepository.mapCapabilities(
      makeGraphQLCapabilities(
        instrumentCode: "920001.BJ",
        executionModes: [.paper],
        supportedPriceTypes: [.limit, .best],
        liveReady: false
      ),
      requestedInstrumentCode: "920001.BJ",
      requestedAccountID: "ACCOUNT-1",
      authorizedAccountIDs: ["ACCOUNT-1"]
    )

    XCTAssertFalse(
      capabilities.supports(
        direction: .buy,
        quoteType: .best,
        executionMode: .paper
      )
    )
  }

  private func makeRequest(
    accountID: String = "ACCOUNT-1",
    instrumentCode: String = "600519.SH",
    direction: ManualOrderDirection = .buy,
    quoteType: ManualOrderQuoteType = .limit,
    executionMode: ManualOrderExecutionMode = .paper,
    volume: Int = 100,
    limitPrice: Double? = 1_500
  ) -> ManualOrderRequest {
    ManualOrderRequest(
      accountID: accountID,
      instrumentCode: instrumentCode,
      direction: direction,
      quoteType: quoteType,
      executionMode: executionMode,
      volume: volume,
      limitPrice: limitPrice,
      idempotencyKey: UUID()
    )
  }

  private func makeGraphQLPreview(
    accountID: String,
    request: ManualOrderRequest,
    finalVolume: Int? = nil,
    riskAction: String = "ALLOW",
    executionMode: String? = nil
  ) -> QuantXAPI.IOSPreviewManualOrderMutation.Data.PreviewManualOrder.Preview {
    QuantXAPI.IOSPreviewManualOrderMutation.Data.PreviewManualOrder.Preview(
      _dataDict: DataDict(
        data: [
          "__typename": "ManualOrderPreview",
          "challengeId": "challenge-1",
          "confirmationToken": "memory-only-token",
          "accountId": accountID,
          "instrumentCode": request.normalizedInstrumentCode,
          "side": GraphQLEnum(request.direction.graphQLValue),
          "priceType": GraphQLEnum(request.quoteType.graphQLValue),
          "volume": request.volume,
          "requestedVolume": request.volume,
          "finalVolume": finalVolume ?? request.volume,
          "limitPrice": request.limitPrice,
          "referencePrice": 1_499.50,
          "estimatedAmount": 150_000.0,
          "estimatedFees": 18.0,
          "availableCash": 200_000.0,
          "idempotencyKey": request.idempotencyKey.uuidString.lowercased(),
          "executionMode": executionMode ?? request.executionMode.rawValue,
          "quoteTimestamp": ISO8601DateFormatter().string(from: Date()),
          "challengeExpiresAt": ISO8601DateFormatter().string(
            from: Date().addingTimeInterval(60)
          ),
          "riskDecisionId": "risk-decision-1",
          "riskAction": riskAction,
          "riskReasonCode": riskAction == "CAP" ? "ORDER_SIZER_CAP" : "ALLOW",
          "riskReasonDetail": riskAction == "CAP"
            ? "请求数量已按 A 股合法规则缩减"
            : "统一风控允许请求数量",
          "warnings": ["确认时仍会重新风控"],
        ],
        fulfilledFragments: [
          ObjectIdentifier(
            QuantXAPI.IOSPreviewManualOrderMutation.Data.PreviewManualOrder.Preview.self
          )
        ]
      )
    )
  }

  private func makeGraphQLCapabilities(
    accountID: String = "ACCOUNT-1",
    instrumentCode: String = "600519.SH",
    executionModes: [QuantXAPI.ManualOrderExecutionMode] = [.paper],
    rawExecutionModes: [GraphQLEnum<QuantXAPI.ManualOrderExecutionMode>]? = nil,
    supportedPriceTypes: [QuantXAPI.ManualOrderPriceType] = [.limit, .best],
    liveReady: Bool
  ) -> QuantXAPI.IOSOrderEntryCapabilitiesQuery.Data.OrderEntryCapabilities {
    QuantXAPI.IOSOrderEntryCapabilitiesQuery.Data.OrderEntryCapabilities(
      _dataDict: DataDict(
        data: [
          "__typename": "OrderEntryCapabilities",
          "accountId": accountID,
          "instrumentCode": instrumentCode,
          "canManualTrade": true,
          "defaultExecutionMode": GraphQLEnum(
            QuantXAPI.ManualOrderExecutionMode.paper
          ),
          "executionModes": rawExecutionModes
            ?? executionModes.map(GraphQLEnum.init),
          "supportedSides": [
            GraphQLEnum(QuantXAPI.ManualOrderSide.buy),
            GraphQLEnum(QuantXAPI.ManualOrderSide.sell),
          ],
          "supportedPriceTypes": supportedPriceTypes.map(GraphQLEnum.init),
          "liveReady": liveReady,
          "liveBlockedReasons": liveReady ? [] : ["实盘未就绪"],
          "warnings": ["能力只决定可展示选项"],
        ],
        fulfilledFragments: [
          ObjectIdentifier(
            QuantXAPI.IOSOrderEntryCapabilitiesQuery.Data.OrderEntryCapabilities.self
          )
        ]
      )
    )
  }
}
