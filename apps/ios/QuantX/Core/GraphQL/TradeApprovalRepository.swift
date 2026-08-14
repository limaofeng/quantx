import Apollo
import Foundation

enum TradeApprovalRepositoryError: Error, Equatable, LocalizedError {
  case rejected(code: String, message: String)
  case invalidResponse
  case accountScopeMismatch
  case contextMismatch

  var errorDescription: String? {
    switch self {
    case .rejected(let code, let message):
      return message.isEmpty ? "交易确认失败（\(code)）" : "\(message)（\(code)）"
    case .invalidResponse:
      return "交易确认服务返回了无效数据"
    case .accountScopeMismatch:
      return "交易确认返回了未授权账户数据"
    case .contextMismatch:
      return "交易确认上下文已变化，请刷新后重试"
    }
  }
}

@MainActor
protocol TradeApprovalLoading: AnyObject {
  func previewTTradeEntry(
    runID: String,
    intentID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> TradeApprovalPreview

  func confirmTTradeEntry(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation

  func previewStrategyTradeIntent(
    runID: String,
    intentID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> TradeApprovalPreview

  func confirmStrategyTradeIntent(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation
}

@MainActor
final class TradeApprovalRepository: TradeApprovalLoading {
  private let client: ApolloClient

  init(client: ApolloClient) {
    self.client = client
  }

  func previewTTradeEntry(
    runID: String,
    intentID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> TradeApprovalPreview {
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPreviewTTradeEntryApprovalMutation(
        runId: runID,
        intentId: intentID
      )
    )
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.previewTTradeEntryApproval else {
      throw TradeApprovalRepositoryError.invalidResponse
    }
    guard result.success, let value = result.preview else {
      throw rejected(code: result.code, message: result.message)
    }
    return try mapPreview(
      challengeID: value.challengeId,
      confirmationToken: value.confirmationToken,
      action: value.action,
      accountID: value.accountId,
      runID: value.runId,
      intentID: value.intentId,
      instrumentCode: value.instrumentCode,
      side: value.side,
      bucket: value.bucket,
      reason: value.reason,
      targetVolume: value.targetVolume,
      referencePrice: value.referencePrice,
      estimatedAmount: value.estimatedAmount,
      signalExpiresAt: value.signalExpiresAt,
      challengeExpiresAt: value.challengeExpiresAt,
      warnings: value.warnings,
      expectedKind: .tTradeEntry,
      expectedRunID: runID,
      expectedIntentID: intentID,
      authorizedAccountIDs: authorizedAccountIDs
    )
  }

  func confirmTTradeEntry(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation
  {
    guard preview.kind == .tTradeEntry, !preview.isExpired() else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    let response = try await client.perform(
      mutation: QuantXAPI.IOSConfirmTTradeEntryApprovalMutation(
        runId: preview.runID,
        intentId: preview.intentID,
        confirmationToken: preview.confirmationToken
      )
    )
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.confirmTTradeEntryApproval else {
      throw TradeApprovalRepositoryError.invalidResponse
    }
    return try mapConfirmation(
      success: result.success,
      code: result.code,
      message: result.message,
      challengeID: result.challengeId,
      expectedChallengeID: preview.id
    )
  }

  func previewStrategyTradeIntent(
    runID: String,
    intentID: String,
    authorizedAccountIDs: Set<String>
  ) async throws -> TradeApprovalPreview {
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPreviewStrategyTradeIntentApprovalMutation(
        runId: runID,
        intentId: intentID
      )
    )
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.previewStrategyTradeIntentApproval else {
      throw TradeApprovalRepositoryError.invalidResponse
    }
    guard result.success, let value = result.preview else {
      throw rejected(code: result.code, message: result.message)
    }
    return try mapPreview(
      challengeID: value.challengeId,
      confirmationToken: value.confirmationToken,
      action: value.action,
      accountID: value.accountId,
      runID: value.runId,
      intentID: value.intentId,
      instrumentCode: value.instrumentCode,
      side: value.side,
      bucket: value.bucket,
      reason: value.reason,
      targetVolume: value.targetVolume,
      referencePrice: value.referencePrice,
      estimatedAmount: value.estimatedAmount,
      signalExpiresAt: value.signalExpiresAt,
      challengeExpiresAt: value.challengeExpiresAt,
      warnings: value.warnings,
      expectedKind: .strategyTradeIntent,
      expectedRunID: runID,
      expectedIntentID: intentID,
      authorizedAccountIDs: authorizedAccountIDs
    )
  }

  func confirmStrategyTradeIntent(_ preview: TradeApprovalPreview) async throws
    -> TradeApprovalConfirmation
  {
    guard preview.kind == .strategyTradeIntent, !preview.isExpired() else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    let response = try await client.perform(
      mutation: QuantXAPI.IOSConfirmStrategyTradeIntentApprovalMutation(
        runId: preview.runID,
        intentId: preview.intentID,
        confirmationToken: preview.confirmationToken
      )
    )
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.confirmStrategyTradeIntentApproval else {
      throw TradeApprovalRepositoryError.invalidResponse
    }
    return try mapConfirmation(
      success: result.success,
      code: result.code,
      message: result.message,
      challengeID: result.challengeId,
      expectedChallengeID: preview.id
    )
  }

  private func mapPreview(
    challengeID: String,
    confirmationToken: String,
    action: String,
    accountID: String,
    runID: String,
    intentID: String,
    instrumentCode: String,
    side: String,
    bucket: String,
    reason: String,
    targetVolume: Int?,
    referencePrice: Double?,
    estimatedAmount: Double?,
    signalExpiresAt: String?,
    challengeExpiresAt: String,
    warnings: [String],
    expectedKind: TradeApprovalKind,
    expectedRunID: String,
    expectedIntentID: String,
    authorizedAccountIDs: Set<String>
  ) throws -> TradeApprovalPreview {
    guard
      let kind = TradeApprovalKind(rawValue: action),
      kind == expectedKind,
      runID == expectedRunID,
      intentID == expectedIntentID
    else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    guard authorizedAccountIDs.contains(accountID) else {
      throw TradeApprovalRepositoryError.accountScopeMismatch
    }
    try ReadOnlyModelValidator.requireNonempty(challengeID, field: "approval.challengeId")
    try ReadOnlyModelValidator.requireNonempty(
      confirmationToken,
      field: "approval.confirmationToken"
    )
    try ReadOnlyModelValidator.requireNonempty(instrumentCode, field: "approval.instrument")
    guard side.uppercased() == "BUY" else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    if let targetVolume {
      try ReadOnlyModelValidator.requireNonnegative(
        [targetVolume],
        field: "approval.targetVolume"
      )
    }
    try ReadOnlyModelValidator.requireFinite(
      [referencePrice, estimatedAmount].compactMap { $0 },
      field: "approval.amount"
    )
    let expiresAt = try ReadOnlyModelValidator.requireDate(
      challengeExpiresAt,
      field: "approval.challengeExpiresAt"
    )
    guard expiresAt > Date(), !warnings.isEmpty else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    return TradeApprovalPreview(
      id: challengeID,
      confirmationToken: confirmationToken,
      kind: kind,
      accountID: accountID,
      runID: runID,
      intentID: intentID,
      instrumentCode: instrumentCode,
      side: side,
      bucket: bucket,
      reason: reason,
      targetVolume: targetVolume,
      referencePrice: referencePrice,
      estimatedAmount: estimatedAmount,
      signalExpiresAt: signalExpiresAt.flatMap(PortfolioDateParser.parse),
      challengeExpiresAt: expiresAt,
      warnings: warnings.map { String($0.prefix(300)) }
    )
  }

  private func mapConfirmation(
    success: Bool,
    code: String,
    message: String,
    challengeID: String?,
    expectedChallengeID: String
  ) throws -> TradeApprovalConfirmation {
    guard success else {
      throw rejected(code: code, message: message)
    }
    guard challengeID == expectedChallengeID else {
      throw TradeApprovalRepositoryError.contextMismatch
    }
    return TradeApprovalConfirmation(
      success: true,
      code: code,
      message: safeMessage(message),
      challengeID: expectedChallengeID
    )
  }

  private func rejected(code: String, message: String) -> TradeApprovalRepositoryError {
    .rejected(
      code: String(code.prefix(80)),
      message: safeMessage(message)
    )
  }

  private func safeMessage(_ value: String) -> String {
    String(value.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
  }
}
