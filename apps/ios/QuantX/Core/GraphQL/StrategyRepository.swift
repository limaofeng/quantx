import Apollo
import Foundation

@MainActor
protocol StrategyMonitoringLoading: AnyObject {
  func load() async throws -> StrategyMonitorSnapshot
}

struct StrategyWorkspaceRepositoryContext: Equatable, Sendable {
  let userID: String
  let deviceSessionID: String
  let activeAccountID: String
  let authorizedAccountIDs: Set<String>
  let sessionContextID: UUID
}

@MainActor
protocol StrategyWorkspaceLoading: AnyObject {
  func loadMobileParameters(instanceID: String) async throws
    -> StrategyMobileParameterSnapshot

  func updateMobileParameters(
    instanceID: String,
    values: [String: StrategyMobileParameterValue],
    expectedVersion: String,
    applyImmediately: Bool
  ) async throws

  func pause(instanceID: String) async throws -> String
  func resumePaper(instanceID: String) async throws -> String

  func previewLiveControl(
    action: StrategyLiveControlAction,
    instanceID: String,
    expectedConfigVersion: String,
    idempotencyKey: UUID,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlPreviewTicket

  func confirmLiveControl(
    _ preview: StrategyControlPreviewTicket,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlConfirmation
}

enum ReadOnlyRepositoryError: LocalizedError, Equatable {
  case unauthenticated
  case forbidden
  case accountScopeMismatch
  case invalidResponse
  case transport
  case graphQL(code: String, requestID: String?)

  var errorDescription: String? {
    switch self {
    case .unauthenticated:
      "会话已失效，请重新登录"
    case .forbidden:
      "当前用户没有读取此数据的权限"
    case .accountScopeMismatch:
      "服务返回了授权范围之外的账户数据，已停止展示"
    case .invalidResponse:
      "服务返回了无法验证的数据"
    case .transport:
      "无法刷新数据，请检查私网或 VPN 连接"
    case .graphQL(let code, let requestID):
      if let requestID, !requestID.isEmpty {
        "服务端拒绝了数据请求（\(code)），请求 ID：\(requestID)"
      } else {
        "服务端拒绝了数据请求（\(code)）"
      }
    }
  }
}

enum ApolloReadOnlyResponseValidator {
  static func validate(_ errors: [GraphQLError]?) throws {
    guard let errors, !errors.isEmpty else { return }
    let codes = Set(
      errors.compactMap { error in
        (error.extensions?["code"] as? String)?.uppercased()
      }
    )
    if codes.contains("UNAUTHENTICATED") {
      throw ReadOnlyRepositoryError.unauthenticated
    }
    if codes.contains("FORBIDDEN") || codes.contains("PERMISSION_DENIED") {
      throw ReadOnlyRepositoryError.forbidden
    }
    let first = errors[0]
    let code = (first.extensions?["code"] as? String)?.uppercased() ?? "GRAPHQL_ERROR"
    let requestID = first.extensions?["requestId"] as? String
    throw ReadOnlyRepositoryError.graphQL(code: code, requestID: requestID)
  }

  static func mapResponseCode(_ error: ResponseCodeInterceptor.ResponseCodeError) -> Error {
    switch error.response.statusCode {
    case 401:
      ReadOnlyRepositoryError.unauthenticated
    case 403:
      ReadOnlyRepositoryError.forbidden
    default:
      ReadOnlyRepositoryError.transport
    }
  }
}

@MainActor
final class StrategyRepository: StrategyMonitoringLoading, StrategyWorkspaceLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func load() async throws -> StrategyMonitorSnapshot {
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSStrategyInstancesQuery(),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let graphQLInstances = response.data?.strategyInstances else {
        throw ReadOnlyRepositoryError.invalidResponse
      }
      let instances =
        try graphQLInstances
        .map(StrategyMonitorItem.init(graphQL:))
        .sorted {
          if $0.updatedAt == $1.updatedAt {
            return $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending
          }
          return $0.updatedAt > $1.updatedAt
        }
      return StrategyMonitorSnapshot(instances: instances, fetchedAt: Date())
    } catch is CancellationError {
      throw CancellationError()
    } catch let error as ReadOnlyRepositoryError {
      throw error
    } catch is ReadOnlyMappingError {
      throw ReadOnlyRepositoryError.invalidResponse
    } catch let error as ResponseCodeInterceptor.ResponseCodeError {
      throw ApolloReadOnlyResponseValidator.mapResponseCode(error)
    } catch {
      throw ReadOnlyRepositoryError.transport
    }
  }

  func loadMobileParameters(
    instanceID: String
  ) async throws -> StrategyMobileParameterSnapshot {
    let instanceID = try Self.normalizedInstanceID(instanceID)
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSStrategyInstanceMobileParametersQuery(
          instanceId: instanceID
        ),
        cachePolicy: .networkOnly
      )
      try Self.validateStrategyErrors(response.errors)
      guard let result = response.data?.strategyInstanceMobileParameters else {
        throw StrategyWorkspaceError.invalidResponse
      }
      let parameters = try result.parameters.map { item in
        try StrategyMobileParameterMapper.parameter(
          key: item.key,
          title: item.title,
          description: item.description,
          valueType: item.valueType,
          currentValue: item.currentValue,
          unit: item.unit,
          minimum: item.minimum,
          maximum: item.maximum,
          step: item.step,
          enumValues: item.enumValues,
          applyImmediately: item.applyImmediately,
          riskLevel: item.riskLevel
        )
      }
      return try StrategyMobileParameterMapper.snapshot(
        requestedInstanceID: instanceID,
        instanceID: result.instanceId,
        configVersion: result.configVersion,
        editable: result.editable,
        parameters: parameters
      )
    } catch {
      throw Self.map(error)
    }
  }

  func updateMobileParameters(
    instanceID: String,
    values: [String: StrategyMobileParameterValue],
    expectedVersion: String,
    applyImmediately: Bool
  ) async throws {
    let instanceID = try Self.normalizedInstanceID(instanceID)
    let expectedVersion = try Self.normalizedVersion(expectedVersion)
    guard
      !values.isEmpty,
      values.count <= 100,
      values.keys.allSatisfy({
        !$0.isEmpty
          && $0 == $0.trimmingCharacters(in: .whitespacesAndNewlines)
          && $0.count <= 128
      })
    else {
      throw StrategyWorkspaceError.invalidRequest("没有可保存的移动参数变更")
    }
    let parameters = GraphQLJSON(
      object: values.mapValues(\.graphQLJSON)
    )
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSUpdateStrategyInstanceParametersMutation(
          instanceId: instanceID,
          input: QuantXAPI.StrategyInstanceParameterUpdateInput(
            parameters: parameters,
            applyImmediately: applyImmediately,
            expectedVersion: .some(expectedVersion)
          )
        ),
        requestConfiguration: noCache
      )
      try Self.validateStrategyErrors(response.errors)
      guard
        let result = response.data?.updateStrategyInstanceParameters,
        result.id == instanceID,
        !result.parameterVersion.isEmpty,
        PortfolioDateParser.parse(result.updatedAt) != nil
      else {
        throw StrategyWorkspaceError.invalidResponse
      }
    } catch {
      throw Self.map(error)
    }
  }

  func pause(instanceID: String) async throws -> String {
    let instanceID = try Self.normalizedInstanceID(instanceID)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPauseStrategyInstanceMutation(instanceId: instanceID),
        requestConfiguration: noCache
      )
      try Self.validateStrategyErrors(response.errors)
      guard let result = response.data?.pauseStrategyInstance else {
        throw StrategyWorkspaceError.invalidResponse
      }
      return try Self.operationMessage(
        success: result.success,
        message: result.message,
        fallbackCode: "STRATEGY_PAUSE_REJECTED"
      )
    } catch {
      throw Self.map(error)
    }
  }

  func resumePaper(instanceID: String) async throws -> String {
    let instanceID = try Self.normalizedInstanceID(instanceID)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSResumeStrategyInstanceMutation(instanceId: instanceID),
        requestConfiguration: noCache
      )
      try Self.validateStrategyErrors(response.errors)
      guard let result = response.data?.resumeStrategyInstance else {
        throw StrategyWorkspaceError.invalidResponse
      }
      return try Self.operationMessage(
        success: result.success,
        message: result.message,
        fallbackCode: "STRATEGY_RESUME_REJECTED"
      )
    } catch {
      throw Self.map(error)
    }
  }

  func previewLiveControl(
    action: StrategyLiveControlAction,
    instanceID: String,
    expectedConfigVersion: String,
    idempotencyKey: UUID,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlPreviewTicket {
    let instanceID = try Self.normalizedInstanceID(instanceID)
    let version = try Self.normalizedVersion(expectedConfigVersion)
    try Self.validate(context)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPreviewStrategyControlMutation(
          input: QuantXAPI.StrategyControlPreviewInput(
            accountId: context.activeAccountID,
            instanceId: instanceID,
            action: .init(action.graphQLValue),
            expectedConfigVersion: version,
            idempotencyKey: idempotencyKey.uuidString.lowercased()
          )
        ),
        requestConfiguration: noCache
      )
      try Self.validateStrategyErrors(response.errors)
      guard let result = response.data?.previewStrategyControl else {
        throw StrategyWorkspaceError.invalidResponse
      }
      guard result.success,
        result.code == "STRATEGY_CONTROL_PREVIEW_READY",
        let preview = result.preview
      else {
        throw Self.rejection(code: result.code, message: result.message)
      }
      let checks = try preview.checks.map { item in
        let code = item.code.trimmingCharacters(in: .whitespacesAndNewlines)
        let message = item.message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty, !message.isEmpty else {
          throw StrategyWorkspaceError.invalidResponse
        }
        return StrategyControlReadinessCheck(
          code: code,
          passed: item.passed,
          message: message
        )
      }
      return try StrategyControlPreviewMapper.map(
        challengeID: preview.challengeId,
        confirmationToken: preview.confirmationToken,
        sessionContextID: context.sessionContextID,
        userID: context.userID,
        deviceSessionID: context.deviceSessionID,
        accountID: context.activeAccountID,
        responseAccountID: preview.accountId,
        requestedInstanceID: instanceID,
        responseInstanceID: preview.instanceId,
        targetInstanceID: preview.targetInstanceId,
        requestedAction: action,
        responseAction: preview.action.rawValue,
        expectedConfigVersion: version,
        responseConfigVersion: preview.configVersion,
        currentMode: preview.currentMode,
        currentStatus: preview.currentStatus,
        readinessStatus: preview.readinessStatus,
        snapshotID: preview.snapshotId,
        snapshotAt: preview.snapshotAt,
        expiresAt: preview.challengeExpiresAt,
        checks: checks,
        warnings: preview.warnings
      )
    } catch {
      throw Self.map(error)
    }
  }

  func confirmLiveControl(
    _ preview: StrategyControlPreviewTicket,
    context: StrategyWorkspaceRepositoryContext
  ) async throws -> StrategyControlConfirmation {
    try Self.validate(preview, context: context)
    guard !preview.isExpired() else {
      throw StrategyWorkspaceError.challengeExpired
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSConfirmStrategyControlMutation(
          input: QuantXAPI.StrategyControlConfirmationInput(
            challengeId: preview.id,
            confirmationToken: preview.confirmationToken
          )
        ),
        requestConfiguration: noCache
      )
      try Self.validateStrategyErrors(response.errors)
      guard let result = response.data?.confirmStrategyControl else {
        throw StrategyWorkspaceError.invalidResponse
      }
      guard result.success,
        result.code == "STRATEGY_CONTROL_APPLIED",
        result.challengeId == preview.id,
        result.instanceId == preview.targetInstanceID,
        result.status == "APPLIED"
      else {
        if !result.success {
          throw Self.rejection(code: result.code, message: result.message)
        }
        throw StrategyWorkspaceError.invalidResponse
      }
      return StrategyControlConfirmation(
        challengeID: preview.id,
        instanceID: preview.targetInstanceID,
        status: "APPLIED",
        message: Self.normalizedMessage(result.message, fallback: "策略控制已应用")
      )
    } catch {
      throw Self.map(error)
    }
  }

  private static func validateStrategyErrors(_ errors: [GraphQLError]?) throws {
    if let errors,
      errors.contains(where: { error in
        let code = (error.extensions?["code"] as? String)?.uppercased() ?? ""
        return code.contains("VERSION_CONFLICT")
          || (error.message ?? "").uppercased()
            .contains("STRATEGY_CONFIG_VERSION_CONFLICT")
      })
    {
      throw StrategyWorkspaceError.versionConflict
    }
    try ApolloReadOnlyResponseValidator.validate(errors)
  }

  private static func normalizedInstanceID(_ value: String) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalized.isEmpty, normalized == value, normalized.count <= 64 else {
      throw StrategyWorkspaceError.invalidRequest("策略实例 ID 无效")
    }
    return normalized
  }

  private static func normalizedVersion(_ value: String) throws -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard
      !normalized.isEmpty,
      normalized.allSatisfy(\.isNumber),
      let numeric = Int(normalized),
      numeric > 0,
      String(numeric) == normalized
    else {
      throw StrategyWorkspaceError.invalidRequest("策略配置版本无效")
    }
    return normalized
  }

  private static func validate(_ context: StrategyWorkspaceRepositoryContext) throws {
    guard
      !context.userID.isEmpty,
      !context.deviceSessionID.isEmpty,
      !context.activeAccountID.isEmpty,
      context.authorizedAccountIDs == [context.activeAccountID]
    else {
      throw StrategyWorkspaceError.contextChanged
    }
  }

  private static func validate(
    _ preview: StrategyControlPreviewTicket,
    context: StrategyWorkspaceRepositoryContext
  ) throws {
    try validate(context)
    guard
      preview.sessionContextID == context.sessionContextID,
      preview.userID == context.userID,
      preview.deviceSessionID == context.deviceSessionID,
      preview.accountID == context.activeAccountID,
      !preview.instanceID.isEmpty,
      !preview.configVersion.isEmpty
    else {
      throw StrategyWorkspaceError.contextChanged
    }
  }

  private static func operationMessage(
    success: Bool,
    message: String,
    fallbackCode: String
  ) throws -> String {
    guard success else {
      throw StrategyWorkspaceError.rejected(
        code: fallbackCode,
        message: normalizedMessage(message, fallback: "策略操作未应用")
      )
    }
    return normalizedMessage(message, fallback: "策略操作已应用")
  }

  private static func rejection(code: String, message: String) -> StrategyWorkspaceError {
    let code = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    if code.contains("VERSION_CONFLICT") {
      return .versionConflict
    }
    if ["CONFIRMATION_EXPIRED", "CHALLENGE_EXPIRED"].contains(code) {
      return .challengeExpired
    }
    return .rejected(
      code: String((code.isEmpty ? "STRATEGY_CONTROL_REJECTED" : code).prefix(100)),
      message: normalizedMessage(message, fallback: "策略控制未应用")
    )
  }

  private static func normalizedMessage(_ value: String, fallback: String) -> String {
    let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return normalized.isEmpty ? fallback : String(normalized.prefix(500))
  }

  private static func map(_ error: Error) -> Error {
    if error is CancellationError { return CancellationError() }
    if let error = error as? StrategyWorkspaceError { return error }
    if let error = error as? ReadOnlyRepositoryError { return error }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      return ApolloReadOnlyResponseValidator.mapResponseCode(error)
    }
    return ReadOnlyRepositoryError.transport
  }
}
