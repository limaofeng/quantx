import Apollo
import Foundation

enum ManualOrderRepositoryError: Error, Equatable, LocalizedError {
  case rejected(code: String, message: String)
  case invalidRequest(String)
  case invalidResponse
  case accountScopeMismatch
  case contextMismatch

  var errorDescription: String? {
    switch self {
    case .rejected(let code, let message):
      return message.isEmpty ? "手动委托被拒绝（\(code)）" : "\(message)（\(code)）"
    case .invalidRequest(let message):
      return message
    case .invalidResponse:
      return "手动委托服务返回了无法验证的数据"
    case .accountScopeMismatch:
      return "委托账户与当前主账户不一致，已停止提交"
    case .contextMismatch:
      return "委托预览与当前票据不一致，请重新预览"
    }
  }
}

@MainActor
protocol ManualOrderLoading: AnyObject {
  func preview(
    _ request: ManualOrderRequest,
    authorizedAccountIDs: Set<String>
  ) async throws -> ManualOrderPreviewTicket

  func confirm(_ preview: ManualOrderPreviewTicket) async throws
    -> ManualOrderQueueConfirmation
}

@MainActor
final class ManualOrderRepository: ManualOrderLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func preview(
    _ request: ManualOrderRequest,
    authorizedAccountIDs: Set<String>
  ) async throws -> ManualOrderPreviewTicket {
    try Self.validate(request, authorizedAccountIDs: authorizedAccountIDs)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPreviewManualOrderMutation(
          input: QuantXAPI.ManualOrderPreviewInput(
            accountId: request.accountID,
            instrumentCode: request.normalizedInstrumentCode,
            side: .init(request.direction.graphQLValue),
            priceType: .init(request.quoteType.graphQLValue),
            volume: Int32(request.volume),
            idempotencyKey: request.idempotencyKey.uuidString.lowercased(),
            limitPrice: request.limitPrice.map(GraphQLNullable.some) ?? .null
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.previewManualOrder else {
        throw ManualOrderRepositoryError.invalidResponse
      }
      guard result.success, let value = result.preview else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      return try Self.mapPreview(
        value,
        request: request,
        authorizedAccountIDs: authorizedAccountIDs
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ManualOrderRepositoryError {
      throw error
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch is ReadOnlyMappingError {
      throw ManualOrderRepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }

  func confirm(_ preview: ManualOrderPreviewTicket) async throws
    -> ManualOrderQueueConfirmation
  {
    guard !preview.isExpired() else {
      throw ManualOrderRepositoryError.contextMismatch
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSConfirmManualOrderMutation(
          input: QuantXAPI.ManualOrderConfirmationInput(
            challengeId: preview.id,
            confirmationToken: preview.confirmationToken
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.confirmManualOrder else {
        throw ManualOrderRepositoryError.invalidResponse
      }
      guard result.success else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      guard
        result.challengeId == preview.id,
        let clientOrderID = Self.nonempty(result.clientOrderId),
        let status = Self.nonempty(result.status),
        status.uppercased() == "QUEUED"
      else {
        throw ManualOrderRepositoryError.contextMismatch
      }
      return ManualOrderQueueConfirmation(
        challengeID: preview.id,
        clientOrderID: clientOrderID,
        status: "QUEUED"
      )
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ManualOrderRepositoryError {
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
    _ request: ManualOrderRequest,
    authorizedAccountIDs: Set<String>
  ) throws {
    guard authorizedAccountIDs.contains(request.accountID) else {
      throw ManualOrderRepositoryError.accountScopeMismatch
    }
    guard isCanonicalAStockCode(request.normalizedInstrumentCode) else {
      throw ManualOrderRepositoryError.invalidRequest("请输入带市场后缀的 A 股代码，例如 600519.SH")
    }
    guard request.volume > 0, request.volume <= Int(Int32.max) else {
      throw ManualOrderRepositoryError.invalidRequest("委托数量必须为正整数")
    }
    switch request.quoteType {
    case .limit:
      guard let price = request.limitPrice, price.isFinite, price > 0 else {
        throw ManualOrderRepositoryError.invalidRequest("限价委托必须填写有效价格")
      }
    case .best:
      guard request.limitPrice == nil else {
        throw ManualOrderRepositoryError.invalidRequest("对手方最优价委托不能携带限价")
      }
    }
  }

  static func mapPreview(
    _ value: QuantXAPI.IOSPreviewManualOrderMutation.Data.PreviewManualOrder.Preview,
    request: ManualOrderRequest,
    authorizedAccountIDs: Set<String>
  ) throws -> ManualOrderPreviewTicket {
    guard
      authorizedAccountIDs.contains(value.accountId),
      value.accountId == request.accountID
    else {
      throw ManualOrderRepositoryError.accountScopeMismatch
    }
    guard
      value.instrumentCode.uppercased() == request.normalizedInstrumentCode,
      value.side == request.direction.graphQLValue,
      value.priceType == request.quoteType.graphQLValue,
      value.volume == request.volume,
      value.requestedVolume == request.volume,
      value.finalVolume > 0,
      value.finalVolume <= value.requestedVolume,
      value.idempotencyKey.lowercased() == request.idempotencyKey.uuidString.lowercased()
    else {
      throw ManualOrderRepositoryError.contextMismatch
    }
    switch request.quoteType {
    case .limit:
      guard let returnedPrice = value.limitPrice,
        let requestedPrice = request.limitPrice,
        returnedPrice == requestedPrice
      else {
        throw ManualOrderRepositoryError.contextMismatch
      }
    case .best:
      guard value.limitPrice == nil else {
        throw ManualOrderRepositoryError.contextMismatch
      }
    }
    let riskAction = value.riskAction
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .uppercased()
    guard
      nonempty(value.challengeId) != nil,
      nonempty(value.confirmationToken) != nil,
      nonempty(value.executionMode) != nil,
      let riskDecisionID = nonempty(value.riskDecisionId),
      let riskReasonCode = nonempty(value.riskReasonCode),
      ["ALLOW", "CAP"].contains(riskAction),
      (riskAction == "ALLOW" && value.finalVolume == value.requestedVolume)
        || (riskAction == "CAP" && value.finalVolume < value.requestedVolume),
      value.referencePrice.isFinite,
      value.referencePrice > 0,
      value.estimatedAmount.isFinite,
      value.estimatedAmount >= 0,
      value.availableCash.isFinite,
      value.availableCash >= 0,
      value.estimatedFees.map({ $0.isFinite && $0 >= 0 }) ?? true,
      value.availableVolume.map({ $0 >= 0 }) ?? true,
      request.direction != .sell || value.availableVolume != nil
    else {
      throw ManualOrderRepositoryError.invalidResponse
    }
    let quoteTimestamp = try ReadOnlyModelValidator.requireDate(
      value.quoteTimestamp,
      field: "manualOrder.quoteTimestamp"
    )
    let challengeExpiresAt = try ReadOnlyModelValidator.requireDate(
      value.challengeExpiresAt,
      field: "manualOrder.challengeExpiresAt"
    )
    guard challengeExpiresAt > Date() else {
      throw ManualOrderRepositoryError.contextMismatch
    }
    return ManualOrderPreviewTicket(
      id: value.challengeId,
      confirmationToken: value.confirmationToken,
      accountID: value.accountId,
      instrumentCode: value.instrumentCode.uppercased(),
      direction: request.direction,
      quoteType: request.quoteType,
      requestedVolume: value.requestedVolume,
      finalVolume: value.finalVolume,
      limitPrice: value.limitPrice,
      referencePrice: value.referencePrice,
      estimatedAmount: value.estimatedAmount,
      estimatedFees: value.estimatedFees,
      availableCash: value.availableCash,
      availableVolume: value.availableVolume,
      idempotencyKey: request.idempotencyKey,
      executionMode: value.executionMode,
      quoteTimestamp: quoteTimestamp,
      challengeExpiresAt: challengeExpiresAt,
      riskDecisionID: String(riskDecisionID.prefix(120)),
      riskAction: riskAction,
      riskReasonCode: String(riskReasonCode.prefix(120)),
      riskReasonDetail: String(
        value.riskReasonDetail
          .trimmingCharacters(in: .whitespacesAndNewlines)
          .prefix(300)
      ),
      warnings: value.warnings.map { String($0.prefix(300)) }
    )
  }

  private static func isCanonicalAStockCode(_ value: String) -> Bool {
    value.range(of: #"^[0-9]{6}\.(SH|SZ|BJ)$"#, options: .regularExpression) != nil
  }

  private static func rejected(code: String, message: String) -> ManualOrderRepositoryError {
    .rejected(
      code: String(code.prefix(80)),
      message: String(message.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
    )
  }

  private static func nonempty(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
  }
}
