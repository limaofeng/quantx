import Apollo
import Foundation

struct TAssistantLegacyScope: Equatable, Sendable {
  let accountID: String
  let configID: String
  let runID: String
  let headVersion: Int

  func validate(_ context: TTradeControlRepositoryContext) throws {
    guard !context.userID.isEmpty, !context.deviceSessionID.isEmpty,
      context.activeAccountID == accountID, context.authorizedAccountIDs == [accountID],
      !accountID.isEmpty, !configID.isEmpty, !runID.isEmpty,
      headVersion > 0, Int32(exactly: headVersion) != nil
    else { throw TTradeControlError.contextChanged }
  }

  var fields: [String: GraphQLJSON] {
    [
      "account_id": .string(accountID), "config_id": .string(configID),
      "run_id": .string(runID), "expected_head_version": .integer(headVersion),
    ]
  }
}

struct TAssistantLegacyInventory: Equatable, Sendable {
  let operationID: String
  let hash: String
  let scope: TAssistantLegacyScope
  let manifest: GraphQLJSON

  static func validated(_ evidence: GraphQLJSON, scope: TAssistantLegacyScope, commandID: String)
    throws -> Self
  {
    let fields = try object(evidence)
    guard Set(fields.keys) == ["inventory_operation_id", "manifest_hash", "manifest"],
      fields["inventory_operation_id"] == .string("legacy-inventory:\(commandID)"),
      case .string(let hash) = fields["manifest_hash"], validHash(hash),
      let raw = fields["manifest"]
    else { throw TTradeControlError.invalidResponse }
    let manifest = try object(raw)
    guard manifest["schema"] == .string("legacy-t-obligations.v1"),
      manifest["account_id"] == .string(scope.accountID),
      manifest["config_id"] == .string(scope.configID),
      manifest["run_id"] == .string(scope.runID),
      manifest["head_version"] == .integer(scope.headVersion)
    else { throw TTradeControlError.contextChanged }
    for key in [
      "intents", "pending", "correlations", "commands", "runtime_events", "batches", "exit_plans",
      "retained_client_order_ids", "unsubmitted_intent_ids_for_review",
    ] {
      guard case .array = manifest[key] else { throw TTradeControlError.invalidResponse }
    }
    return Self(
      operationID: "legacy-inventory:\(commandID)", hash: hash, scope: scope, manifest: raw)
  }

  static func object(_ value: GraphQLJSON) throws -> [String: GraphQLJSON] {
    guard case .object(let fields) = value, Set(fields.map(\.key)).count == fields.count else {
      throw TTradeControlError.invalidResponse
    }
    return Dictionary(uniqueKeysWithValues: fields.map { ($0.key, $0.value) })
  }

  static func validHash(_ value: String) -> Bool {
    value.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
  }
}

struct TAssistantLegacyDrainTicket: Equatable, Sendable {
  let challengeID: String
  let token: String
  let expiresAt: Date
  let inventory: TAssistantLegacyInventory
  let windowStart: Date
  let windowEnd: Date
  let context: TTradeControlRepositoryContext

  func validate(_ current: TTradeControlRepositoryContext) throws {
    try inventory.scope.validate(current)
    guard current == context else { throw TTradeControlError.contextChanged }
  }
}

struct TAssistantLegacyOperation: Equatable, Sendable {
  enum Status: String, Sendable {
    case notFound = "NOT_FOUND"
    case pending = "PENDING"
    case processing = "PROCESSING"
    case failed = "FAILED"
    case succeeded = "SUCCEEDED"
  }
  let commandID: String
  let status: Status
  let evidence: GraphQLJSON?
}

@MainActor
protocol TAssistantLegacyMaintenanceLoading {
  func prepare(
    _ scope: TAssistantLegacyScope, requestID: UUID, context: TTradeControlRepositoryContext
  ) async throws -> String
  func operation(_ commandID: String, context: TTradeControlRepositoryContext) async throws
    -> TAssistantLegacyOperation
  func preview(
    _ inventory: TAssistantLegacyInventory, windowStart: Date, windowEnd: Date,
    context: TTradeControlRepositoryContext
  ) async throws -> TAssistantLegacyDrainTicket
  func confirm(_ ticket: TAssistantLegacyDrainTicket, context: TTradeControlRepositoryContext)
    async throws -> String
}

@MainActor
final class TAssistantLegacyMaintenanceRepository: TAssistantLegacyMaintenanceLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)
  init(client: ApolloClient) { self.client = client }

  func prepare(
    _ scope: TAssistantLegacyScope, requestID: UUID, context: TTradeControlRepositoryContext
  ) async throws -> String {
    try scope.validate(context)
    let identity = requestID.uuidString.lowercased()
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPrepareTAssistantLegacyInventoryMutation(
        requestId: identity,
        request: .init(
          accountId: scope.accountID, configId: scope.configID, runId: scope.runID,
          expectedHeadVersion: Int32(scope.headVersion))), requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.prepareTAssistantLegacyInventory, result.success,
      result.code == "INVENTORY_QUEUED", result.engineCommandId == identity
    else { throw TTradeControlError.unavailable("清单准备未确认，请使用原请求重试") }
    return identity
  }

  func operation(_ commandID: String, context: TTradeControlRepositoryContext) async throws
    -> TAssistantLegacyOperation
  {
    guard UUID(uuidString: commandID) != nil, !context.userID.isEmpty,
      !context.deviceSessionID.isEmpty,
      !context.activeAccountID.isEmpty, context.authorizedAccountIDs == [context.activeAccountID]
    else { throw TTradeControlError.contextChanged }
    let response = try await client.fetch(
      query: QuantXAPI.IOSTAssistantLegacyMaintenanceOperationQuery(
        accountId: context.activeAccountID, commandId: commandID), cachePolicy: .networkOnly,
      requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.tAssistantLegacyMaintenanceOperation,
      result.commandId == commandID,
      let status = TAssistantLegacyOperation.Status(rawValue: result.status),
      (status == .succeeded) == (result.evidence != nil)
    else { throw TTradeControlError.invalidResponse }
    return .init(commandID: commandID, status: status, evidence: result.evidence)
  }

  func preview(
    _ inventory: TAssistantLegacyInventory, windowStart: Date, windowEnd: Date,
    context: TTradeControlRepositoryContext
  ) async throws -> TAssistantLegacyDrainTicket {
    try inventory.scope.validate(context)
    guard windowStart.timeIntervalSince1970.isFinite, windowEnd.timeIntervalSince1970.isFinite,
      windowStart < windowEnd, windowEnd > Date(),
      TAssistantLegacyInventory.validHash(inventory.hash)
    else { throw TTradeControlError.invalidRequest("维护窗口或清单无效") }
    let iso = ISO8601DateFormatter()
    iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let scope = inventory.scope
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPreviewTAssistantLegacyDrainMutation(
        request: .init(
          accountId: scope.accountID, configId: scope.configID, runId: scope.runID,
          expectedHeadVersion: Int32(scope.headVersion),
          inventoryOperationId: inventory.operationID, expectedInventoryHash: inventory.hash,
          windowStart: iso.string(from: windowStart), windowEnd: iso.string(from: windowEnd))),
      requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.previewTAssistantLegacyDrain, result.success,
      result.code == "PREVIEW_READY", let preview = result.preview
    else {
      throw TTradeControlError.unavailable("维护预览未通过，请重新核对清单")
    }
    let request = try TAssistantLegacyInventory.object(preview.request)
    var expected = scope.fields
    expected["inventory_operation_id"] = .string(inventory.operationID)
    expected["expected_inventory_hash"] = .string(inventory.hash)
    guard Set(request.keys) == Set(expected.keys).union(["window_start", "window_end"]),
      expected.allSatisfy({ request[$0.key] == $0.value }),
      case .string(let start) = request["window_start"],
      case .string(let end) = request["window_end"],
      UUID(uuidString: preview.challengeId) != nil, !preview.confirmationToken.isEmpty
    else { throw TTradeControlError.invalidResponse }
    let returnedStart = try ReadOnlyModelValidator.requireDate(start, field: "legacy.windowStart")
    let returnedEnd = try ReadOnlyModelValidator.requireDate(end, field: "legacy.windowEnd")
    let expires = try ReadOnlyModelValidator.requireDate(
      preview.expiresAt, field: "legacy.expiresAt")
    guard abs(returnedStart.timeIntervalSince(windowStart)) < 0.001,
      abs(returnedEnd.timeIntervalSince(windowEnd)) < 0.001, expires > Date(),
      expires <= returnedEnd
    else { throw TTradeControlError.invalidResponse }
    return .init(
      challengeID: preview.challengeId, token: preview.confirmationToken, expiresAt: expires,
      inventory: inventory, windowStart: returnedStart, windowEnd: returnedEnd, context: context)
  }

  func confirm(_ ticket: TAssistantLegacyDrainTicket, context: TTradeControlRepositoryContext)
    async throws -> String
  {
    try ticket.validate(context)
    // The store gates first confirmation by expiry and biometrics; an uncertain
    // attempt must retain this exact credential for the server's consumed replay.
    let response = try await client.perform(
      mutation: QuantXAPI.IOSConfirmTAssistantLegacyDrainMutation(
        challengeId: ticket.challengeID, confirmationToken: ticket.token),
      requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.confirmTAssistantLegacyDrain, result.success,
      result.code == "DRAIN_QUEUED", let commandID = result.engineCommandId,
      UUID(uuidString: commandID) != nil
    else { throw TTradeControlError.unavailable("排空结果未明确，请保留原确认请求") }
    return commandID
  }
}
