import Apollo
import Foundation

enum NativeAccountControlAction: String, Sendable {
  case beginControlledWindow = "BEGIN_CONTROLLED_WINDOW"
  case killSwitch = "KILL_SWITCH"

  var graphQLValue: QuantXAPI.AccountExecutionControlAction {
    switch self {
    case .beginControlledWindow: .beginControlledWindow
    case .killSwitch: .killSwitch
    }
  }
  var title: String {
    switch self {
    case .beginControlledWindow: "建立账户实盘窗口"
    case .killSwitch: "触发账户紧急熔断"
    }
  }
}

struct NativeAccountControlTicket: Equatable, Identifiable, Sendable {
  let id: String
  let token: String
  let context: TTradeControlRepositoryContext
  let action: NativeAccountControlAction
  let stateVersion: Int
  let snapshotID: String
  let reason: String
  let expiresAt: Date
  let summary: String
  let blockedReasons: [String]
}

@MainActor
protocol AccountExecutionControlLoading: AnyObject {
  func preview(
    action: NativeAccountControlAction, reason: String, context: TTradeControlRepositoryContext
  )
    async throws -> NativeAccountControlTicket
  func confirm(_ ticket: NativeAccountControlTicket, context: TTradeControlRepositoryContext)
    async throws -> String
}

@MainActor
final class AccountExecutionControlRepository: AccountExecutionControlLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)
  init(client: ApolloClient) { self.client = client }

  func preview(
    action: NativeAccountControlAction, reason: String, context: TTradeControlRepositoryContext
  )
    async throws -> NativeAccountControlTicket
  {
    try Self.validate(context)
    let reason = reason.trimmingCharacters(in: .whitespacesAndNewlines)
    guard reason.count <= 512, action != .killSwitch || !reason.isEmpty else {
      throw TTradeControlError.invalidRequest("请输入有效的处置原因")
    }
    let state = try await client.fetch(
      query: QuantXAPI.IOSAccountControlSafetyQuery(accountId: context.activeAccountID),
      cachePolicy: .networkOnly, requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(state.errors)
    guard let safety = state.data?.accountExecutionSafety,
      safety.accountId == context.activeAccountID, safety.stateVersion >= 0
    else {
      throw TTradeControlError.contextChanged
    }
    let snapshotID = action == .beginControlledWindow ? (safety.snapshotId ?? "") : ""
    guard action != .beginControlledWindow || !snapshotID.isEmpty else {
      throw TTradeControlError.unavailable("当前没有可绑定的账户快照")
    }
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPreviewAccountControlMutation(
        input:
          QuantXAPI.AccountExecutionControlPreviewInput(
            accountId: context.activeAccountID,
            action: .init(action.graphQLValue), stateVersion: Int32(safety.stateVersion),
            idempotencyKey: UUID().uuidString.lowercased(), snapshotId: .some(snapshotID),
            reason: .some(reason))),
      requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.previewAccountExecutionControl else {
      throw TTradeControlError.invalidResponse
    }
    guard result.success, result.code == "PREVIEW_READY", let preview = result.preview else {
      throw TTradeControlError.rejected(code: result.code, message: result.message)
    }
    guard preview.tokenIssued, let token = preview.confirmationToken, !token.isEmpty,
      UUID(uuidString: preview.challengeId) != nil,
      preview.accountId == context.activeAccountID, preview.action.rawValue == action.rawValue,
      preview.stateVersion == safety.stateVersion, preview.snapshotId == snapshotID,
      preview.reason == reason, preview.challengeStatus == "PENDING",
      preview.operationStatus == "PENDING",
      preview.safety.accountId == context.activeAccountID,
      preview.safety.stateVersion == safety.stateVersion
    else { throw TTradeControlError.contextChanged }
    let expires = try ReadOnlyModelValidator.requireDate(
      preview.challengeExpiresAt, field: "account.challengeExpiresAt")
    guard expires > Date(), expires <= Date().addingTimeInterval(121) else {
      throw TTradeControlError.challengeExpired
    }
    return NativeAccountControlTicket(
      id: preview.challengeId, token: token, context: context,
      action: action, stateVersion: safety.stateVersion, snapshotID: snapshotID, reason: reason,
      expiresAt: expires, summary: preview.safety.summary,
      blockedReasons: preview.safety.blockedReasons)
  }

  func confirm(_ ticket: NativeAccountControlTicket, context: TTradeControlRepositoryContext)
    async throws -> String
  {
    try Self.validate(context)
    guard ticket.context == context else { throw TTradeControlError.contextChanged }
    guard ticket.expiresAt > Date() else { throw TTradeControlError.challengeExpired }
    let response = try await client.perform(
      mutation: QuantXAPI.IOSConfirmAccountControlMutation(
        input:
          QuantXAPI.AccountExecutionControlConfirmationInput(
            challengeId: ticket.id, confirmationToken: ticket.token)),
      requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.confirmAccountExecutionControl else {
      throw TTradeControlError.invalidResponse
    }
    if result.operationStatus == "DISPATCHING" { throw TTradeControlError.resultUncertain }
    guard result.success else {
      throw TTradeControlError.rejected(code: result.code, message: result.message)
    }
    try Self.validateApplied(
      ticket: ticket, challengeID: result.challengeId,
      action: result.action?.rawValue, code: result.code, status: result.operationStatus,
      accountID: result.safety?.accountId, stateVersion: result.safety?.stateVersion,
      killSwitch: result.safety?.killSwitch, windowActive: result.safety?.executionWindowActive)
    return result.message
  }

  static func validateApplied(
    ticket: NativeAccountControlTicket, challengeID: String?,
    action: String?, code: String, status: String, accountID: String?, stateVersion: Int?,
    killSwitch: Bool?, windowActive: Bool?
  ) throws {
    guard challengeID == ticket.id, action == ticket.action.rawValue,
      code == "\(ticket.action.rawValue)_APPLIED", status == "APPLIED",
      accountID == ticket.context.activeAccountID, let stateVersion,
      stateVersion >= ticket.stateVersion,
      ticket.action == .killSwitch ? killSwitch == true : windowActive == true
    else { throw TTradeControlError.invalidResponse }
  }

  private static func validate(_ context: TTradeControlRepositoryContext) throws {
    guard !context.userID.isEmpty, !context.deviceSessionID.isEmpty,
      !context.activeAccountID.isEmpty,
      context.authorizedAccountIDs == [context.activeAccountID]
    else { throw TTradeControlError.contextChanged }
  }
}
