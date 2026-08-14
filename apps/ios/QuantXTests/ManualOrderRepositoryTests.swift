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
    XCTAssertFalse(preview.isExpired())
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

  private func makeRequest(
    accountID: String = "ACCOUNT-1",
    instrumentCode: String = "600519.SH",
    direction: ManualOrderDirection = .buy,
    quoteType: ManualOrderQuoteType = .limit,
    volume: Int = 100,
    limitPrice: Double? = 1_500
  ) -> ManualOrderRequest {
    ManualOrderRequest(
      accountID: accountID,
      instrumentCode: instrumentCode,
      direction: direction,
      quoteType: quoteType,
      volume: volume,
      limitPrice: limitPrice,
      idempotencyKey: UUID()
    )
  }

  private func makeGraphQLPreview(
    accountID: String,
    request: ManualOrderRequest,
    finalVolume: Int? = nil,
    riskAction: String = "ALLOW"
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
          "executionMode": "LIVE",
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
}
