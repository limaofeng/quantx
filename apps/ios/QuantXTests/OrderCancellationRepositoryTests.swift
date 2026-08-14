import XCTest

@testable import QuantX

@MainActor
final class OrderCancellationRepositoryTests: XCTestCase {
  func testInputAlwaysCarriesExplicitAccountAndUUIDIdempotencyKey() throws {
    let key = try XCTUnwrap(UUID(uuidString: "730BD66E-C81A-4D91-95C4-A878B755B948"))
    let request = OrderCancellationRequest(
      accountID: "ACCOUNT-1",
      orderID: 42,
      idempotencyKey: key
    )

    let input = OrderCancellationRepository.graphQLInput(request)

    XCTAssertEqual(input.accountId.unwrapped, "ACCOUNT-1")
    XCTAssertEqual(input.orderId, 42)
    XCTAssertEqual(
      input.idempotencyKey.unwrapped,
      "730bd66e-c81a-4d91-95c4-a878b755b948"
    )
  }

  func testValidationRequiresExactSingletonAccountScope() {
    let request = makeRequest()

    XCTAssertThrowsError(
      try OrderCancellationRepository.validate(
        request,
        authorizedAccountIDs: ["ACCOUNT-1", "ACCOUNT-2"]
      )
    ) { error in
      XCTAssertEqual(
        error as? OrderCancellationRepositoryError,
        .accountScopeMismatch
      )
    }
  }

  func testQueuedResultRequiresMatchingOrderAndQueuedStatus() throws {
    let request = makeRequest()
    let confirmation = try OrderCancellationRepository.mapResult(
      success: true,
      message: "撤单命令已排队",
      orderID: request.orderID,
      clientOrderID: "cancel-command-1",
      status: "QUEUED",
      request: request
    )

    XCTAssertEqual(confirmation.orderID, request.orderID)
    XCTAssertEqual(confirmation.status, "QUEUED")
    XCTAssertEqual(OrderCancellationQueueConfirmation.title, "撤单命令已排队")
    XCTAssertEqual(
      OrderCancellationQueueConfirmation.message,
      "等待券商委托投影更新；排队不代表订单已经撤销。"
    )

    XCTAssertThrowsError(
      try OrderCancellationRepository.mapResult(
        success: true,
        message: "ok",
        orderID: request.orderID,
        clientOrderID: "cancel-command-1",
        status: "CANCELED",
        request: request
      )
    ) { error in
      XCTAssertEqual(
        error as? OrderCancellationRepositoryError,
        .contextMismatch
      )
    }
  }

  func testServiceRejectionRemainsRejectionInsteadOfInventingCanceledState() {
    let request = makeRequest()

    XCTAssertThrowsError(
      try OrderCancellationRepository.mapResult(
        success: false,
        message: "订单状态 SUCCEEDED 不允许撤单",
        orderID: request.orderID,
        clientOrderID: nil,
        status: "REJECTED",
        request: request
      )
    ) { error in
      XCTAssertEqual(
        error as? OrderCancellationRepositoryError,
        .rejected("订单状态 SUCCEEDED 不允许撤单")
      )
    }
  }

  private func makeRequest() -> OrderCancellationRequest {
    OrderCancellationRequest(
      accountID: "ACCOUNT-1",
      orderID: 42,
      idempotencyKey: UUID()
    )
  }
}
