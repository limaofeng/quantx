import Apollo
import Foundation

enum OrderCancellationRepositoryError: Error, Equatable, LocalizedError {
  case rejected(String)
  case invalidRequest(String)
  case invalidResponse
  case accountScopeMismatch
  case contextMismatch

  var errorDescription: String? {
    switch self {
    case .rejected(let message):
      message.isEmpty ? "撤单请求被服务端拒绝" : message
    case .invalidRequest(let message):
      message
    case .invalidResponse:
      "撤单服务返回了无法验证的数据"
    case .accountScopeMismatch:
      "撤单账户与当前唯一主账户不一致，已停止提交"
    case .contextMismatch:
      "撤单回包与当前委托不一致，已停止处理"
    }
  }
}

@MainActor
protocol OrderCancellationLoading: AnyObject {
  func cancel(
    _ request: OrderCancellationRequest,
    authorizedAccountIDs: Set<String>
  ) async throws -> OrderCancellationQueueConfirmation
}

@MainActor
final class OrderCancellationRepository: OrderCancellationLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func cancel(
    _ request: OrderCancellationRequest,
    authorizedAccountIDs: Set<String>
  ) async throws -> OrderCancellationQueueConfirmation {
    try Self.validate(request, authorizedAccountIDs: authorizedAccountIDs)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSCancelOrderMutation(
          input: Self.graphQLInput(request)
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.cancelOrder else {
        throw OrderCancellationRepositoryError.invalidResponse
      }
      return try Self.mapResult(
        success: result.success,
        message: result.message,
        orderID: result.orderId,
        clientOrderID: result.clientOrderId,
        status: result.status,
        request: request
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as OrderCancellationRepositoryError {
      throw error
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }

  static func validate(
    _ request: OrderCancellationRequest,
    authorizedAccountIDs: Set<String>
  ) throws {
    guard authorizedAccountIDs == Set([request.accountID]) else {
      throw OrderCancellationRepositoryError.accountScopeMismatch
    }
    guard request.orderID > 0, request.orderID <= Int(Int32.max) else {
      throw OrderCancellationRepositoryError.invalidRequest("委托编号无效，无法撤单")
    }
  }

  static func graphQLInput(
    _ request: OrderCancellationRequest
  ) -> QuantXAPI.CancelOrderInput {
    QuantXAPI.CancelOrderInput(
      accountId: .some(request.accountID),
      orderId: Int32(request.orderID),
      idempotencyKey: .some(request.idempotencyKey.uuidString.lowercased())
    )
  }

  static func mapResult(
    success: Bool,
    message: String,
    orderID: Int?,
    clientOrderID: String?,
    status: String?,
    request: OrderCancellationRequest
  ) throws -> OrderCancellationQueueConfirmation {
    guard success else {
      throw OrderCancellationRepositoryError.rejected(sanitized(message))
    }
    guard
      orderID == request.orderID,
      let clientOrderID = nonempty(clientOrderID),
      nonempty(status)?.uppercased() == "QUEUED"
    else {
      throw OrderCancellationRepositoryError.contextMismatch
    }
    return OrderCancellationQueueConfirmation(
      orderID: request.orderID,
      clientOrderID: clientOrderID,
      status: "QUEUED"
    )
  }

  private static func nonempty(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : String(trimmed.prefix(160))
  }

  private static func sanitized(_ value: String) -> String {
    String(value.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
  }
}
