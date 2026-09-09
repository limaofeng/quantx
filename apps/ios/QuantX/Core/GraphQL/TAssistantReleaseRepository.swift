import Apollo
import Foundation

struct TAssistantReleaseDraft: Equatable, Sendable, Decodable {
  let accountID: String
  let sourceExecutionID: String
  let configVersionID: String
  let configHash: String
  let headVersion: Int
  let evaluationID: String
  let reportHash: String
  let policyHash: String
  let windowStart: Date
  let windowEnd: Date

  enum CodingKeys: String, CodingKey {
    case accountID = "accountId"
    case sourceExecutionID = "sourceExecutionId"
    case configVersionID = "configVersionId"
    case configHash = "expectedConfigHash"
    case headVersion = "expectedHeadVersion"
    case evaluationID = "evaluationId"
    case reportHash = "expectedReportHash"
    case policyHash = "expectedPolicyHash"
    case windowStart, windowEnd
  }

  static func decodeReleaseDocument(_ data: Data) throws -> Self {
    guard data.count <= 16_384,
      let document = try JSONSerialization.jsonObject(with: data) as? [String: Any],
      Set(document.keys) == ["schema", "request"],
      document["schema"] as? String == "quantx.t-assistant-release-request.v1",
      let request = document["request"] as? [String: Any],
      Set(request.keys) == [
        "accountId", "sourceExecutionId", "configVersionId", "expectedConfigHash",
        "expectedHeadVersion", "evaluationId", "expectedReportHash", "expectedPolicyHash",
        "windowStart", "windowEnd",
      ]
    else { throw TTradeControlError.invalidRequest("发布请求文件格式无效") }
    let decoder = JSONDecoder()
    decoder.dateDecodingStrategy = .custom { decoder in
      let value = try decoder.singleValueContainer().decode(String.self)
      return try ReadOnlyModelValidator.requireDate(value, field: "release.window")
    }
    return try decoder.decode(Self.self, from: JSONSerialization.data(withJSONObject: request))
  }

  func validate(context: TTradeControlRepositoryContext) throws {
    guard !context.userID.isEmpty, !context.deviceSessionID.isEmpty,
      context.activeAccountID == accountID,
      context.authorizedAccountIDs == [accountID], !accountID.isEmpty,
      !sourceExecutionID.isEmpty, !configVersionID.isEmpty,
      headVersion > 0, Int32(exactly: headVersion) != nil,
      UUID(uuidString: evaluationID)?.uuidString.lowercased() == evaluationID,
      [configHash, reportHash, policyHash].allSatisfy({
        $0.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
      }),
      windowStart.timeIntervalSince1970.isFinite,
      windowEnd.timeIntervalSince1970.isFinite,
      windowEnd > windowStart
    else { throw TTradeControlError.contextChanged }
  }
}

// The credential remains in memory and is never included in a persisted status record.
struct TAssistantReleaseTicket: Equatable, Sendable {
  let challengeID: String
  let confirmationToken: String
  let expiresAt: Date
  let draft: TAssistantReleaseDraft
  let context: TTradeControlRepositoryContext

  func validate(context: TTradeControlRepositoryContext) throws {
    try draft.validate(context: context)
    guard self.context == context else { throw TTradeControlError.contextChanged }
  }
}

// Read-only identity survives local lock; it cannot authorize a confirmation.
struct TAssistantReleaseReference: Equatable, Sendable {
  let challengeID: String
  let userID: String
  let deviceSessionID: String
  let accountID: String

  init(ticket: TAssistantReleaseTicket) {
    challengeID = ticket.challengeID
    userID = ticket.context.userID
    deviceSessionID = ticket.context.deviceSessionID
    accountID = ticket.context.activeAccountID
  }

  init(challengeID: String, context: TTradeControlRepositoryContext) {
    self.challengeID = challengeID
    userID = context.userID
    deviceSessionID = context.deviceSessionID
    accountID = context.activeAccountID
  }

  func validate(context: TTradeControlRepositoryContext) throws {
    guard context.userID == userID, context.deviceSessionID == deviceSessionID,
      context.activeAccountID == accountID, context.authorizedAccountIDs == [accountID],
      !userID.isEmpty, !deviceSessionID.isEmpty, !accountID.isEmpty,
      UUID(uuidString: challengeID) != nil
    else { throw TTradeControlError.contextChanged }
  }
}

struct TAssistantReleaseOperation: Equatable, Identifiable, Sendable {
  let reference: TAssistantReleaseReference
  let configVersionID: String
  let createdAt: Date
  var id: String { reference.challengeID }
}

struct TAssistantReleaseStatus: Equatable, Sendable {
  enum Phase: String, Sendable {
    case awaitingConfirmation = "AWAITING_CONFIRMATION"
    case expired = "EXPIRED"
    case pending = "PENDING"
    case processing = "PROCESSING"
    case failed = "FAILED"
    case succeeded = "SUCCEEDED"
    case unknown = "UNKNOWN"
  }
  let challengeID: String
  let phase: Phase
  let commandID: String?
  let executionID: String?
  let executionStatus: String?
  let reasonCode: String?

  static func validated(
    challengeID: String, phase: String, commandID: String?, executionID: String?,
    executionStatus: String?, reasonCode: String?, expectedChallengeID: String
  ) throws -> Self {
    guard challengeID == expectedChallengeID, let phase = Phase(rawValue: phase),
      [commandID, executionID, executionStatus, reasonCode].allSatisfy({
        $0 == nil || !($0?.isEmpty ?? true)
      })
    else { throw TTradeControlError.invalidResponse }
    if [.pending, .processing, .failed, .succeeded].contains(phase), commandID == nil {
      throw TTradeControlError.invalidResponse
    }
    if phase == .succeeded {
      guard executionID != nil, executionStatus != nil, reasonCode == nil else {
        throw TTradeControlError.invalidResponse
      }
    } else if executionID != nil || executionStatus != nil {
      throw TTradeControlError.invalidResponse
    }
    return Self(
      challengeID: challengeID, phase: phase, commandID: commandID,
      executionID: executionID, executionStatus: executionStatus, reasonCode: reasonCode)
  }
}

@MainActor
protocol TAssistantReleaseLoading: AnyObject {
  func recentOperations(context: TTradeControlRepositoryContext) async throws
    -> [TAssistantReleaseOperation]
  func preview(_ draft: TAssistantReleaseDraft, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseTicket
  func confirm(_ ticket: TAssistantReleaseTicket, context: TTradeControlRepositoryContext)
    async throws -> String
  func status(_ reference: TAssistantReleaseReference, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseStatus
}

@MainActor
final class TAssistantReleaseRepository: TAssistantReleaseLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) { self.client = client }

  func recentOperations(context: TTradeControlRepositoryContext) async throws
    -> [TAssistantReleaseOperation]
  {
    guard !context.userID.isEmpty, !context.deviceSessionID.isEmpty,
      !context.activeAccountID.isEmpty, context.authorizedAccountIDs == [context.activeAccountID]
    else { throw TTradeControlError.contextChanged }
    let response = try await client.fetch(
      query: QuantXAPI.IOSTAssistantLiveReleaseOperationsQuery(
        accountId: context.activeAccountID, limit: 20),
      cachePolicy: .networkOnly, requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let values = response.data?.tAssistantLiveReleaseOperations else {
      throw TTradeControlError.invalidResponse
    }
    return try values.map { value in
      guard value.accountId == context.activeAccountID, !value.configVersionId.isEmpty else {
        throw TTradeControlError.contextChanged
      }
      let reference = TAssistantReleaseReference(challengeID: value.challengeId, context: context)
      try reference.validate(context: context)
      return TAssistantReleaseOperation(
        reference: reference, configVersionID: value.configVersionId,
        createdAt: try ReadOnlyModelValidator.requireDate(
          value.createdAt, field: "release.createdAt"))
    }
  }

  func preview(_ draft: TAssistantReleaseDraft, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseTicket
  {
    try draft.validate(context: context)
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let response = try await client.perform(
      mutation: QuantXAPI.IOSPreviewTAssistantLiveReleaseMutation(
        request:
          QuantXAPI.TAssistantReleaseRequest(
            accountId: draft.accountID,
            sourceExecutionId: draft.sourceExecutionID, configVersionId: draft.configVersionID,
            expectedConfigHash: draft.configHash, expectedHeadVersion: Int32(draft.headVersion),
            evaluationId: draft.evaluationID, expectedReportHash: draft.reportHash,
            expectedPolicyHash: draft.policyHash,
            windowStart: formatter.string(from: draft.windowStart),
            windowEnd: formatter.string(from: draft.windowEnd))), requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.previewTAssistantLiveRelease,
      result.success, let preview = result.preview
    else {
      throw TTradeControlError.unavailable("发布预览未通过，请核对审核证据和当前配置")
    }
    let start = try ReadOnlyModelValidator.requireDate(
      preview.windowStart, field: "release.windowStart")
    let end = try ReadOnlyModelValidator.requireDate(preview.windowEnd, field: "release.windowEnd")
    let expires = try ReadOnlyModelValidator.requireDate(
      preview.expiresAt, field: "release.expiresAt")
    guard preview.accountId == draft.accountID,
      preview.sourceExecutionId == draft.sourceExecutionID,
      preview.configVersionId == draft.configVersionID,
      preview.configSnapshotHash == draft.configHash,
      preview.reportHash == draft.reportHash, preview.policyHash == draft.policyHash,
      abs(start.timeIntervalSince(draft.windowStart)) < 0.001,
      abs(end.timeIntervalSince(draft.windowEnd)) < 0.001,
      UUID(uuidString: preview.challengeId) != nil, !preview.confirmationToken.isEmpty,
      expires > Date(), expires <= end, expires <= Date().addingTimeInterval(61)
    else { throw TTradeControlError.contextChanged }
    return TAssistantReleaseTicket(
      challengeID: preview.challengeId,
      confirmationToken: preview.confirmationToken, expiresAt: expires, draft: draft,
      context: context)
  }

  func confirm(_ ticket: TAssistantReleaseTicket, context: TTradeControlRepositoryContext)
    async throws -> String
  {
    try ticket.validate(context: context)
    guard ticket.expiresAt > Date() else { throw TTradeControlError.challengeExpired }
    let response = try await client.perform(
      mutation: QuantXAPI.IOSConfirmTAssistantLiveReleaseMutation(
        challengeId: ticket.challengeID,
        confirmationToken: ticket.confirmationToken), requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let result = response.data?.confirmTAssistantLiveRelease, result.success,
      result.code == "RELEASE_QUEUED", let commandID = result.engineCommandId, !commandID.isEmpty
    else { throw TTradeControlError.unavailable("发布确认未返回有效命令，请查询原操作状态") }
    return commandID
  }

  func status(_ reference: TAssistantReleaseReference, context: TTradeControlRepositoryContext)
    async throws -> TAssistantReleaseStatus
  {
    try reference.validate(context: context)
    let response = try await client.fetch(
      query: QuantXAPI.IOSTAssistantLiveReleaseStatusQuery(challengeId: reference.challengeID),
      cachePolicy: .networkOnly, requestConfiguration: noCache)
    try ApolloReadOnlyResponseValidator.validate(response.errors)
    guard let value = response.data?.tAssistantLiveReleaseStatus else {
      throw TTradeControlError.invalidResponse
    }
    return try TAssistantReleaseStatus.validated(
      challengeID: value.challengeId,
      phase: value.status, commandID: value.engineCommandId, executionID: value.executionId,
      executionStatus: value.executionStatus, reasonCode: value.reasonCode,
      expectedChallengeID: reference.challengeID)
  }
}
