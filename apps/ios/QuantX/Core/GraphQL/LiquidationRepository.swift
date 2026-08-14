import Apollo
import Foundation

struct LiquidationRepositoryContext {
  let activeAccountID: String
  let authorizedAccountIDs: Set<String>
  let portfolioInstrumentCodes: Set<String>
  let contextID: UUID
}

@MainActor
protocol LiquidationLoading: AnyObject {
  func preview(
    _ request: LiquidationPreviewRequest,
    context: LiquidationRepositoryContext
  ) async throws -> LiquidationPreviewTicket

  func confirm(
    _ preview: LiquidationPreviewTicket,
    context: LiquidationRepositoryContext,
    resultRecovery: Bool
  ) async throws -> LiquidationConfirmation
}

@MainActor
final class LiquidationRepository: LiquidationLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func preview(
    _ request: LiquidationPreviewRequest,
    context: LiquidationRepositoryContext
  ) async throws -> LiquidationPreviewTicket {
    try Self.validate(request, context: context)
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPreviewLiquidationMutation(
          input: QuantXAPI.LiquidationPreviewInput(
            accountId: request.accountID,
            scope: .init(request.scope.graphQLValue),
            completionStrategy: .init(request.completionStrategy.graphQLValue),
            conflictStrategy: .init(request.conflictStrategy.graphQLValue),
            idempotencyKey: request.idempotencyKeyValue,
            instrumentCodes: request.scope == .all
              ? .null
              : .some(request.instrumentCodes),
            executionMode: .init(request.executionMode.graphQLValue)
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.previewLiquidation else {
        throw LiquidationRepositoryError.invalidResponse
      }
      guard result.success, result.code == "PREVIEW_READY", let value = result.preview else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      let items = try value.items.map { item in
        let conflicts = try item.conflicts.map { conflict in
          try Self.mapConflict(
            planID: conflict.planId,
            sourceType: conflict.sourceType,
            status: conflict.status,
            remainingVolume: conflict.remainingVolume,
            configVersion: conflict.configVersion,
            pending: conflict.pending
          )
        }
        return try Self.mapItem(
          instrumentCode: item.instrumentCode,
          instrumentName: item.instrumentName,
          totalVolume: item.totalVolume,
          availableVolume: item.availableVolume,
          frozenVolume: item.frozenVolume,
          t1UnavailableVolume: item.t1UnavailableVolume,
          protectedVolume: item.protectedVolume,
          pendingSellVolume: item.pendingSellVolume,
          maxProtectedVolume: item.maxProtectedVolume,
          included: item.included,
          reasonCode: item.reasonCode,
          reasonDetail: item.reasonDetail,
          positionUpdatedAt: item.positionUpdatedAt,
          conflicts: conflicts
        )
      }
      let preview = try Self.mapPreview(
        challengeID: value.challengeId,
        confirmationToken: value.confirmationToken,
        groupID: value.groupId,
        accountID: value.accountId,
        scope: value.scope.value,
        instrumentCodes: value.instrumentCodes,
        completionStrategy: value.completionStrategy.value,
        conflictStrategy: value.conflictStrategy.value,
        executionMode: value.executionMode.value,
        idempotencyKey: value.idempotencyKey,
        snapshotVersion: value.snapshotVersion,
        accountUpdatedAt: value.accountUpdatedAt,
        rolloutSnapshotID: value.rolloutSnapshotId,
        rolloutSnapshotHash: value.rolloutSnapshotHash,
        challengeExpiresAt: value.challengeExpiresAt,
        includedCount: value.includedCount,
        skippedCount: value.skippedCount,
        items: items,
        warnings: value.warnings,
        request: request,
        context: context
      )
      return preview
    } catch {
      throw map(error)
    }
  }

  func confirm(
    _ preview: LiquidationPreviewTicket,
    context: LiquidationRepositoryContext,
    resultRecovery: Bool
  ) async throws -> LiquidationConfirmation {
    try Self.validateConfirmationContext(
      preview,
      context: context,
      resultRecovery: resultRecovery
    )
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSConfirmLiquidationMutation(
          input: QuantXAPI.LiquidationConfirmationInput(
            challengeId: preview.id,
            confirmationToken: preview.confirmationToken
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.confirmLiquidation else {
        throw LiquidationRepositoryError.invalidResponse
      }
      guard let status = result.status else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      let plans = try result.plans.map { plan in
        try Self.mapPlan(
          instrumentCode: plan.instrumentCode,
          success: plan.success,
          planID: plan.planId,
          protectedVolume: plan.protectedVolume,
          conflictPlanIDs: plan.conflictPlanIds,
          error: plan.error,
          preview: preview
        )
      }
      return try Self.mapConfirmation(
        success: result.success,
        code: result.code,
        message: result.message,
        challengeID: result.challengeId,
        groupID: result.groupId,
        commandID: result.commandId,
        status: status,
        createdCount: result.createdCount,
        failedCount: result.failedCount,
        plans: plans,
        preview: preview
      )
    } catch {
      throw map(error)
    }
  }

  static func validate(
    _ request: LiquidationPreviewRequest,
    context: LiquidationRepositoryContext
  ) throws {
    try validateAccount(request.accountID, context: context)
    let normalizedCodes = try normalizedInstrumentCodes(request.instrumentCodes)
    guard normalizedCodes.count <= 200 else {
      throw LiquidationRepositoryError.invalidRequest("一次最多预览 200 只持仓")
    }
    switch request.scope {
    case .single:
      guard normalizedCodes.count == 1 else {
        throw LiquidationRepositoryError.invalidRequest("单只清仓必须且只能选择一只持仓")
      }
    case .selected:
      guard !normalizedCodes.isEmpty else {
        throw LiquidationRepositoryError.invalidRequest("请至少选择一只持仓")
      }
    case .all:
      guard normalizedCodes.isEmpty else {
        throw LiquidationRepositoryError.invalidRequest("全部持仓预览不能携带证券代码")
      }
      guard context.portfolioInstrumentCodes.count <= 200 else {
        throw LiquidationRepositoryError.invalidRequest("全部持仓超过 200 只，请改用选中持仓")
      }
    }
    guard
      normalizedCodes == request.instrumentCodes,
      request.scope == .all || Set(normalizedCodes).isSubset(of: context.portfolioInstrumentCodes)
    else {
      throw LiquidationRepositoryError.contextMismatch
    }
  }

  static func mapConflict(
    planID: String,
    sourceType: String,
    status: String,
    remainingVolume: Int,
    configVersion: Int,
    pending: Bool
  ) throws -> LiquidationConflict {
    guard
      let normalizedPlanID = LiquidationDomainValidator.nonempty(planID, maximumLength: 120),
      let normalizedSource = LiquidationDomainValidator.nonempty(sourceType, maximumLength: 80),
      let normalizedStatus = LiquidationDomainValidator.nonempty(status, maximumLength: 80),
      remainingVolume >= 0,
      configVersion >= 0
    else {
      throw LiquidationRepositoryError.invalidResponse
    }
    return LiquidationConflict(
      planID: normalizedPlanID,
      sourceType: normalizedSource,
      status: normalizedStatus,
      remainingVolume: remainingVolume,
      configVersion: configVersion,
      pending: pending
    )
  }

  static func mapItem(
    instrumentCode: String,
    instrumentName: String?,
    totalVolume: Int,
    availableVolume: Int,
    frozenVolume: Int,
    t1UnavailableVolume: Int,
    protectedVolume: Int,
    pendingSellVolume: Int,
    maxProtectedVolume: Int,
    included: Bool,
    reasonCode: String,
    reasonDetail: String,
    positionUpdatedAt: String?,
    conflicts: [LiquidationConflict]
  ) throws -> LiquidationPreviewItem {
    let normalizedCode = try LiquidationDomainValidator.canonicalInstrumentCode(instrumentCode)
    guard normalizedCode == instrumentCode else {
      throw LiquidationRepositoryError.invalidResponse
    }
    let quantities = [
      totalVolume,
      availableVolume,
      frozenVolume,
      t1UnavailableVolume,
      protectedVolume,
      pendingSellVolume,
      maxProtectedVolume,
    ]
    guard
      quantities.allSatisfy({ $0 >= 0 }),
      availableVolume <= totalVolume,
      frozenVolume <= totalVolume,
      t1UnavailableVolume <= totalVolume,
      protectedVolume <= totalVolume,
      pendingSellVolume <= totalVolume,
      maxProtectedVolume <= totalVolume,
      availableVolume + frozenVolume + t1UnavailableVolume <= totalVolume,
      conflicts.count <= 200,
      conflicts.map(\.planID).uniquedCount == conflicts.count,
      conflicts.reduce(0, { $0 + $1.remainingVolume }) == protectedVolume,
      included == (maxProtectedVolume > 0),
      let normalizedReason = LiquidationDomainValidator.nonempty(reasonCode, maximumLength: 80),
      let normalizedDetail = LiquidationDomainValidator.nonempty(reasonDetail)
    else {
      throw LiquidationRepositoryError.invalidResponse
    }
    let updatedAt = try positionUpdatedAt.map {
      try ReadOnlyModelValidator.requireDate($0, field: "liquidation.positionUpdatedAt")
    }
    if included, updatedAt == nil {
      throw LiquidationRepositoryError.invalidResponse
    }
    return LiquidationPreviewItem(
      instrumentCode: normalizedCode,
      instrumentName: LiquidationDomainValidator.nonempty(instrumentName, maximumLength: 120),
      totalVolume: totalVolume,
      availableVolume: availableVolume,
      frozenVolume: frozenVolume,
      t1UnavailableVolume: t1UnavailableVolume,
      protectedVolume: protectedVolume,
      pendingSellVolume: pendingSellVolume,
      maxProtectedVolume: maxProtectedVolume,
      included: included,
      reasonCode: normalizedReason,
      reasonDetail: normalizedDetail,
      positionUpdatedAt: updatedAt,
      conflicts: conflicts
    )
  }

  static func mapPreview(
    challengeID: String,
    confirmationToken: String,
    groupID: String,
    accountID: String,
    scope: QuantXAPI.LiquidationScope?,
    instrumentCodes: [String],
    completionStrategy: QuantXAPI.LiquidationCompletionStrategy?,
    conflictStrategy: QuantXAPI.LiquidationConflictStrategy?,
    executionMode: QuantXAPI.LiquidationExecutionMode?,
    idempotencyKey: String,
    snapshotVersion: String,
    accountUpdatedAt: String,
    rolloutSnapshotID: String?,
    rolloutSnapshotHash: String?,
    challengeExpiresAt: String,
    includedCount: Int,
    skippedCount: Int,
    items: [LiquidationPreviewItem],
    warnings: [String],
    request: LiquidationPreviewRequest,
    context: LiquidationRepositoryContext
  ) throws -> LiquidationPreviewTicket {
    try validateAccount(accountID, context: context)
    guard
      scope == request.scope.graphQLValue,
      completionStrategy == request.completionStrategy.graphQLValue,
      conflictStrategy == request.conflictStrategy.graphQLValue,
      executionMode == request.executionMode.graphQLValue,
      idempotencyKey.lowercased() == request.idempotencyKeyValue,
      let normalizedChallengeID = LiquidationDomainValidator.nonempty(challengeID, maximumLength: 120),
      UUID(uuidString: normalizedChallengeID) != nil,
      let normalizedToken = LiquidationDomainValidator.nonempty(confirmationToken, maximumLength: 512),
      let normalizedGroupID = LiquidationDomainValidator.nonempty(groupID, maximumLength: 120),
      snapshotVersion.range(of: #"^[0-9a-fA-F]{64}$"#, options: .regularExpression) != nil,
      includedCount >= 0,
      skippedCount >= 0,
      includedCount + skippedCount == items.count,
      items.count <= 200,
      includedCount == items.filter(\.included).count,
      Set(items.map(\.instrumentCode)).count == items.count,
      warnings.count <= 50
    else {
      throw LiquidationRepositoryError.contextMismatch
    }

    let responseCodes = try normalizedInstrumentCodes(instrumentCodes)
    guard responseCodes == instrumentCodes else {
      throw LiquidationRepositoryError.contextMismatch
    }
    let itemCodes = Set(items.map(\.instrumentCode))
    switch request.scope {
    case .single, .selected:
      guard responseCodes == request.instrumentCodes, itemCodes == Set(request.instrumentCodes) else {
        throw LiquidationRepositoryError.contextMismatch
      }
    case .all:
      guard responseCodes.isEmpty, itemCodes == context.portfolioInstrumentCodes else {
        throw LiquidationRepositoryError.contextMismatch
      }
    }

    let accountDate = try ReadOnlyModelValidator.requireDate(
      accountUpdatedAt,
      field: "liquidation.accountUpdatedAt"
    )
    let expiresAt = try ReadOnlyModelValidator.requireDate(
      challengeExpiresAt,
      field: "liquidation.challengeExpiresAt"
    )
    guard expiresAt > Date() else {
      throw LiquidationRepositoryError.contextMismatch
    }
    let rolloutID = LiquidationDomainValidator.nonempty(rolloutSnapshotID, maximumLength: 160)
    let rolloutHash = LiquidationDomainValidator.nonempty(rolloutSnapshotHash, maximumLength: 160)
    if request.executionMode == .live, rolloutID == nil || rolloutHash == nil {
      throw LiquidationRepositoryError.invalidResponse
    }
    return LiquidationPreviewTicket(
      id: normalizedChallengeID,
      confirmationToken: normalizedToken,
      contextID: context.contextID,
      groupID: normalizedGroupID,
      accountID: accountID,
      scope: request.scope,
      instrumentCodes: responseCodes,
      completionStrategy: request.completionStrategy,
      conflictStrategy: request.conflictStrategy,
      executionMode: request.executionMode,
      idempotencyKey: request.idempotencyKey,
      snapshotVersion: snapshotVersion.lowercased(),
      accountUpdatedAt: accountDate,
      rolloutSnapshotID: rolloutID,
      rolloutSnapshotHash: rolloutHash,
      challengeExpiresAt: expiresAt,
      includedCount: includedCount,
      skippedCount: skippedCount,
      items: items,
      warnings: warnings.compactMap {
        LiquidationDomainValidator.nonempty($0)
      }
    )
  }

  static func mapPlan(
    instrumentCode: String,
    success: Bool,
    planID: String?,
    protectedVolume: Int?,
    conflictPlanIDs: [String],
    error: String?,
    preview: LiquidationPreviewTicket
  ) throws -> LiquidationPlanOutcome {
    let normalizedCode = try LiquidationDomainValidator.canonicalInstrumentCode(instrumentCode)
    guard
      normalizedCode == instrumentCode,
      let previewItem = preview.includedItems.first(where: { $0.instrumentCode == normalizedCode }),
      conflictPlanIDs.count <= 200,
      Set(conflictPlanIDs).count == conflictPlanIDs.count,
      conflictPlanIDs.allSatisfy({ LiquidationDomainValidator.nonempty($0, maximumLength: 120) != nil }),
      Set(conflictPlanIDs).isSubset(of: Set(previewItem.conflicts.map(\.planID)))
    else {
      throw LiquidationRepositoryError.contextMismatch
    }
    let normalizedPlanID = LiquidationDomainValidator.nonempty(planID, maximumLength: 120)
    let normalizedError = LiquidationDomainValidator.nonempty(error)
    if success {
      guard
        normalizedPlanID != nil,
        let protectedVolume,
        protectedVolume > 0,
        protectedVolume <= previewItem.maxProtectedVolume,
        normalizedError == nil
      else {
        throw LiquidationRepositoryError.invalidResponse
      }
    } else if let protectedVolume,
      protectedVolume < 0 || protectedVolume > previewItem.maxProtectedVolume
    {
      throw LiquidationRepositoryError.invalidResponse
    }
    return LiquidationPlanOutcome(
      instrumentCode: normalizedCode,
      success: success,
      planID: normalizedPlanID,
      protectedVolume: protectedVolume,
      conflictPlanIDs: conflictPlanIDs,
      error: normalizedError
    )
  }

  static func mapConfirmation(
    success: Bool,
    code: String,
    message: String,
    challengeID: String?,
    groupID: String?,
    commandID: String?,
    status: String,
    createdCount: Int,
    failedCount: Int,
    plans: [LiquidationPlanOutcome],
    preview: LiquidationPreviewTicket
  ) throws -> LiquidationConfirmation {
    let normalizedStatus = status.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    let commandStatus = LiquidationCommandStatus(serverValue: normalizedStatus)
    guard
      challengeID == preview.id,
      groupID == preview.groupID,
      let normalizedCommandID = LiquidationDomainValidator.nonempty(commandID, maximumLength: 120),
      createdCount >= 0,
      failedCount >= 0,
      Set(plans.map(\.instrumentCode)).count == plans.count
    else {
      throw LiquidationRepositoryError.contextMismatch
    }

    let actualCreated = plans.filter(\.success).count
    let actualFailed = plans.count - actualCreated
    switch commandStatus {
    case .pending, .processing:
      guard
        success,
        code == "LIQUIDATION_QUEUED",
        createdCount == 0,
        failedCount == 0,
        plans.isEmpty
      else {
        throw LiquidationRepositoryError.invalidResponse
      }
    case .succeeded:
      guard
        createdCount == actualCreated,
        failedCount == actualFailed,
        Set(plans.map(\.instrumentCode)) == Set(preview.includedItems.map(\.instrumentCode))
      else {
        throw LiquidationRepositoryError.contextMismatch
      }
      if actualCreated == plans.count {
        guard success, code == "LIQUIDATION_CREATED" else {
          throw LiquidationRepositoryError.invalidResponse
        }
      } else if actualCreated > 0 {
        guard !success, code == "LIQUIDATION_PARTIAL" else {
          throw LiquidationRepositoryError.invalidResponse
        }
      } else {
        guard !success, code == "LIQUIDATION_REJECTED" else {
          throw LiquidationRepositoryError.invalidResponse
        }
      }
    case .failed:
      guard
        !success,
        code == "LIQUIDATION_FAILED",
        createdCount == 0,
        failedCount == 0,
        plans.isEmpty
      else {
        throw LiquidationRepositoryError.invalidResponse
      }
    case .unknown:
      guard
        !normalizedStatus.isEmpty,
        createdCount == 0,
        failedCount == 0,
        plans.isEmpty
      else {
        throw LiquidationRepositoryError.invalidResponse
      }
    }

    return LiquidationConfirmation(
      success: success,
      code: String(code.prefix(80)),
      message: String(message.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300)),
      challengeID: preview.id,
      groupID: preview.groupID,
      commandID: normalizedCommandID,
      status: commandStatus,
      createdCount: createdCount,
      failedCount: failedCount,
      plans: plans
    )
  }

  static func validateConfirmationContext(
    _ preview: LiquidationPreviewTicket,
    context: LiquidationRepositoryContext,
    resultRecovery: Bool = false
  ) throws {
    try validateAccount(preview.accountID, context: context)
    guard preview.contextID == context.contextID else {
      throw LiquidationRepositoryError.contextMismatch
    }
    if !resultRecovery,
      !Set(preview.signedInstrumentCodes).isSubset(of: context.portfolioInstrumentCodes)
    {
      throw LiquidationRepositoryError.contextMismatch
    }
  }

  private static func validateAccount(
    _ accountID: String,
    context: LiquidationRepositoryContext
  ) throws {
    guard
      !accountID.isEmpty,
      accountID == context.activeAccountID,
      context.authorizedAccountIDs == Set([accountID])
    else {
      throw LiquidationRepositoryError.accountScopeMismatch
    }
  }

  private static func normalizedInstrumentCodes(_ values: [String]) throws -> [String] {
    let normalized = try values.map(LiquidationDomainValidator.canonicalInstrumentCode)
    guard Set(normalized).count == normalized.count else {
      throw LiquidationRepositoryError.invalidRequest("证券集合不能包含重复代码")
    }
    return normalized.sorted()
  }

  private static func rejected(code: String, message: String) -> LiquidationRepositoryError {
    .rejected(
      code: String(code.trimmingCharacters(in: .whitespacesAndNewlines).prefix(80)),
      message: String(message.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
    )
  }

  private func map(_ error: Error) -> Error {
    if error is CancellationError { return CancellationError() }
    if let error = error as? LiquidationRepositoryError { return error }
    if let error = error as? ReadOnlyRepositoryError { return error }
    if error is ReadOnlyMappingError { return LiquidationRepositoryError.invalidResponse }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      return ApolloReadOnlyResponseValidator.mapResponseCode(error)
    }
    return ReadOnlyRepositoryError.transport
  }
}

private extension Sequence where Element: Hashable {
  var uniquedCount: Int { Set(self).count }
}
