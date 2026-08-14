import Apollo
import Foundation

struct ExitPlanRepositoryContext: Equatable, Sendable {
  let userID: String
  let deviceSessionID: String
  let activeAccountID: String
  let authorizedAccountIDs: Set<String>
  let sessionContextID: UUID
}

@MainActor
protocol ExitPlanLoading: AnyObject {
  func loadPlans(context: ExitPlanRepositoryContext) async throws -> ExitPlanListSnapshot

  func loadDetail(
    plan: ExitPlanItem,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanDetailSnapshot

  func previewAuthorization(
    plan: ExitPlanItem,
    idempotencyKey: UUID,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationTicket

  func confirmAuthorization(
    _ ticket: ExitPlanAuthorizationTicket,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationConfirmation
}

struct ExitPlanRawPlan: Sendable {
  let planID: String
  let groupID: String?
  let accountID: String
  let instrumentCode: String
  let bucket: String
  let sourceType: String
  let sourceID: String
  let strategyRunID: String?
  let enabled: Bool
  let status: String
  let executionMode: String
  let autoExitAuthorized: Bool
  let autoExitAuthorizationConfigVersion: Int?
  let autoExitAuthorizationExpiresAt: String?
  let configVersion: Int
  let completionStrategy: String?
  let completionNote: String?
  let protectedVolume: Int
  let exitedVolume: Int
  let remainingVolume: Int
  let entryAveragePrice: Double
  let rules: GraphQLJSON
  let metadata: GraphQLJSON
  let canEditRules: Bool
  let editRoute: String?
  let phase: String
  let dataQuality: String
  let lastDecision: String?
  let peakPrice: Double
  let peakDrawdownPercent: Double
  let trailingFloorPercent: Double?
  let pendingClientOrderID: String?
  let pendingIntentID: String?
  let lastEvaluatedAt: String?
  let lastError: String?
  let createdAt: String?
  let updatedAt: String?
}

struct ExitPlanRawAuthorizationPosition: Sendable {
  let totalVolume: Int
  let availableVolume: Int
  let frozenVolume: Int
  let yesterdayVolume: Int
  let t1UnavailableVolume: Int
  let updatedAt: String?
}

struct ExitPlanRawAuthorizationConflict: Sendable {
  let planID: String
  let sourceType: String
  let status: String
  let remainingVolume: Int
  let configVersion: Int
  let pending: Bool
}

struct ExitPlanRawAuthorizationPreview: Sendable {
  let challengeID: String
  let confirmationToken: String
  let accountID: String
  let planID: String
  let instrumentCode: String
  let bucket: String
  let sourceType: String
  let executionMode: String
  let configVersion: Int
  let protectedVolume: Int
  let exitedVolume: Int
  let remainingVolume: Int
  let rules: GraphQLJSON
  let t1Policy: String
  let executionPolicy: GraphQLJSON
  let position: ExitPlanRawAuthorizationPosition
  let otherProtections: [ExitPlanRawAuthorizationConflict]
  let readiness: GraphQLJSON
  let authorizationFingerprint: String
  let authorizationExpiresAt: String
  let challengeExpiresAt: String
  let warnings: [String]
}

@MainActor
final class ExitPlanRepository: ExitPlanLoading {
  private let client: ApolloClient
  private let noCache = RequestConfiguration(writeResultsToCache: false)

  init(client: ApolloClient) {
    self.client = client
  }

  func loadPlans(
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanListSnapshot {
    try Self.validate(context)
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSExitPlansQuery(accountId: context.activeAccountID),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let data = response.data else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      let plans = try data.exitPlans.map {
        try Self.mapPlan(
          fragment: $0.fragments.iOSExitPlanFields,
          context: context
        )
      }
      guard Set(plans.map(\.id)).count == plans.count else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      let capabilities = try Self.mapCapabilities(data.exitPlanCapabilities)
      return ExitPlanListSnapshot(
        accountID: context.activeAccountID,
        plans: plans.sorted(by: Self.planSort),
        capabilities: capabilities,
        fetchedAt: Date()
      )
    } catch {
      throw Self.map(error)
    }
  }

  func loadDetail(
    plan: ExitPlanItem,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanDetailSnapshot {
    try Self.validate(context)
    try Self.validate(plan: plan, context: context)
    do {
      let response = try await client.fetch(
        query: QuantXAPI.IOSExitPlanDetailQuery(
          planId: plan.id,
          accountId: context.activeAccountID,
          instrumentCode: plan.instrumentCode
        ),
        cachePolicy: .networkOnly
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard
        let data = response.data,
        let graphQLPlan = data.exitPlan
      else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      let mappedPlan = try Self.mapPlan(
        fragment: graphQLPlan.fragments.iOSExitPlanFields,
        context: context
      )
      guard mappedPlan.id == plan.id, mappedPlan.instrumentCode == plan.instrumentCode else {
        throw ExitPlanWorkspaceError.contextChanged
      }
      let capacity = try Self.mapCapacity(
        data.exitPlanHoldingCapacity,
        expectedPlan: mappedPlan,
        context: context
      )
      let events = try data.exitPlanEvents.map {
        try Self.mapEvent(
          eventID: $0.eventId,
          planID: $0.planId,
          eventType: $0.eventType,
          payload: $0.payload,
          createdAt: $0.createdAt,
          expectedPlanID: mappedPlan.id
        )
      }
      guard Set(events.map(\.id)).count == events.count else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      return ExitPlanDetailSnapshot(
        plan: mappedPlan,
        capacity: capacity,
        events: events.sorted { $0.createdAt > $1.createdAt },
        fetchedAt: Date()
      )
    } catch {
      throw Self.map(error)
    }
  }

  func previewAuthorization(
    plan: ExitPlanItem,
    idempotencyKey: UUID,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationTicket {
    try Self.validate(context)
    try Self.validate(plan: plan, context: context)
    guard plan.executionMode == .live else {
      throw ExitPlanWorkspaceError.invalidRequest("只有明确的 LIVE 退出计划需要自动实盘授权")
    }
    guard plan.status.isAuthorizable, plan.remainingVolume > 0 else {
      throw ExitPlanWorkspaceError.invalidRequest("只有仍有保护量的活动计划可以授权")
    }
    guard plan.configVersion <= Int(Int32.max) else {
      throw ExitPlanWorkspaceError.invalidRequest("计划配置版本超出移动端可确认范围")
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSPreviewExitPlanAuthorizationMutation(
          input: QuantXAPI.ExitPlanAuthorizationPreviewInput(
            accountId: context.activeAccountID,
            planId: plan.id,
            expectedConfigVersion: Int32(plan.configVersion),
            idempotencyKey: idempotencyKey.uuidString.lowercased()
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.previewExitPlanAuthorization else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      guard result.success, let preview = result.preview else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      return try Self.mapAuthorizationPreview(
        preview,
        plan: plan,
        idempotencyKey: idempotencyKey,
        context: context
      )
    } catch {
      throw Self.map(error)
    }
  }

  func confirmAuthorization(
    _ ticket: ExitPlanAuthorizationTicket,
    context: ExitPlanRepositoryContext
  ) async throws -> ExitPlanAuthorizationConfirmation {
    try Self.validate(context)
    try Self.validate(ticket: ticket, context: context)
    guard ticket.review.configVersion <= Int(Int32.max) else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    do {
      let response = try await client.perform(
        mutation: QuantXAPI.IOSConfirmExitPlanAuthorizationMutation(
          input: QuantXAPI.ExitPlanAuthorizationConfirmationInput(
            accountId: ticket.review.accountID,
            planId: ticket.review.planID,
            expectedConfigVersion: Int32(ticket.review.configVersion),
            idempotencyKey: ticket.idempotencyKey.uuidString.lowercased(),
            challengeId: ticket.review.id,
            confirmationToken: ticket.confirmationToken
          )
        ),
        requestConfiguration: noCache
      )
      try ApolloReadOnlyResponseValidator.validate(response.errors)
      guard let result = response.data?.confirmExitPlanAuthorization else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
      guard result.success else {
        throw Self.rejected(code: result.code, message: result.message)
      }
      guard
        result.authorized,
        result.challengeId == ticket.review.id,
        result.planId == ticket.review.planID,
        result.configVersion == ticket.review.configVersion,
        let expiresAtValue = result.authorizationExpiresAt,
        let expiresAt = PortfolioDateParser.parse(expiresAtValue),
        expiresAt == ticket.review.authorizationExpiresAt,
        let auditEventID = Self.nonempty(result.auditEventId, maximumLength: 160)
      else {
        throw ExitPlanWorkspaceError.contextChanged
      }
      return ExitPlanAuthorizationConfirmation(
        challengeID: ticket.review.id,
        planID: ticket.review.planID,
        configVersion: ticket.review.configVersion,
        authorizationExpiresAt: expiresAt,
        auditEventID: auditEventID,
        message: result.message
      )
    } catch {
      throw Self.map(error)
    }
  }
}

extension ExitPlanRepository {
  static func mapPlan(
    fragment: QuantXAPI.IOSExitPlanFields,
    context: ExitPlanRepositoryContext
  ) throws -> ExitPlanItem {
    try mapPlan(
      ExitPlanRawPlan(
        planID: fragment.planId,
        groupID: fragment.groupId,
        accountID: fragment.accountId,
        instrumentCode: fragment.instrumentCode,
        bucket: fragment.bucket,
        sourceType: fragment.sourceType,
        sourceID: fragment.sourceId,
        strategyRunID: fragment.strategyRunId,
        enabled: fragment.enabled,
        status: fragment.status,
        executionMode: fragment.executionMode,
        autoExitAuthorized: fragment.autoExitAuthorized,
        autoExitAuthorizationConfigVersion: fragment.autoExitAuthorizationConfigVersion,
        autoExitAuthorizationExpiresAt: fragment.autoExitAuthorizationExpiresAt,
        configVersion: fragment.configVersion,
        completionStrategy: fragment.completionStrategy,
        completionNote: fragment.completionNote,
        protectedVolume: fragment.protectedVolume,
        exitedVolume: fragment.exitedVolume,
        remainingVolume: fragment.remainingVolume,
        entryAveragePrice: fragment.entryAvgPrice,
        rules: fragment.rules,
        metadata: fragment.metadata,
        canEditRules: fragment.canEditRules,
        editRoute: fragment.editRoute,
        phase: fragment.phase,
        dataQuality: fragment.dataQuality,
        lastDecision: fragment.lastDecision,
        peakPrice: fragment.peakPrice,
        peakDrawdownPercent: fragment.peakDrawdownPct,
        trailingFloorPercent: fragment.trailingFloorPct,
        pendingClientOrderID: fragment.pendingClientOrderId,
        pendingIntentID: fragment.pendingIntentId,
        lastEvaluatedAt: fragment.lastEvaluatedAt,
        lastError: fragment.lastError,
        createdAt: fragment.createdAt,
        updatedAt: fragment.updatedAt
      ),
      context: context
    )
  }

  static func mapPlan(
    _ raw: ExitPlanRawPlan,
    context: ExitPlanRepositoryContext
  ) throws -> ExitPlanItem {
    try validate(context)
    guard raw.accountID == context.activeAccountID else {
      throw ExitPlanWorkspaceError.accountScopeMismatch
    }
    let planID = try required(raw.planID, field: "planId", maximumLength: 128)
    let instrumentCode: String
    do {
      instrumentCode = try LiquidationDomainValidator.canonicalInstrumentCode(
        raw.instrumentCode
      )
    } catch {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    guard instrumentCode == raw.instrumentCode else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    let bucket = try required(raw.bucket, field: "bucket", maximumLength: 80)
    let sourceType = try required(raw.sourceType, field: "sourceType", maximumLength: 80)
    let sourceID = try required(raw.sourceID, field: "sourceId", maximumLength: 160)
    let phase = try required(raw.phase, field: "phase", maximumLength: 80)
    let dataQuality = try required(raw.dataQuality, field: "dataQuality", maximumLength: 80)
    let status = ExitPlanStatus(serverValue: raw.status)
    let executionMode = ExitPlanExecutionMode(serverValue: raw.executionMode)
    guard
      raw.configVersion > 0,
      raw.configVersion <= Int(Int32.max),
      raw.protectedVolume >= 0,
      raw.exitedVolume >= 0,
      raw.remainingVolume >= 0,
      raw.exitedVolume <= raw.protectedVolume,
      raw.remainingVolume <= raw.protectedVolume,
      raw.entryAveragePrice.isFinite,
      raw.entryAveragePrice >= 0,
      raw.peakPrice.isFinite,
      raw.peakPrice >= 0,
      raw.peakDrawdownPercent.isFinite,
      raw.peakDrawdownPercent >= 0,
      raw.trailingFloorPercent?.isFinite != false
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    let authorizationExpiry = try optionalDate(
      raw.autoExitAuthorizationExpiresAt,
      field: "autoExitAuthorizationExpiresAt"
    )
    if raw.autoExitAuthorized {
      guard
        executionMode == .live,
        raw.autoExitAuthorizationConfigVersion != nil,
        authorizationExpiry != nil
      else {
        throw ExitPlanWorkspaceError.invalidResponse
      }
    }
    return ExitPlanItem(
      id: planID,
      groupID: optional(raw.groupID, maximumLength: 128),
      accountID: raw.accountID,
      instrumentCode: instrumentCode,
      bucket: bucket,
      sourceType: sourceType,
      sourceID: sourceID,
      strategyRunID: optional(raw.strategyRunID, maximumLength: 160),
      enabled: raw.enabled,
      status: status,
      executionMode: executionMode,
      autoExitAuthorized: raw.autoExitAuthorized,
      autoExitAuthorizationConfigVersion: raw.autoExitAuthorizationConfigVersion,
      autoExitAuthorizationExpiresAt: authorizationExpiry,
      configVersion: raw.configVersion,
      completionStrategy: optional(raw.completionStrategy, maximumLength: 80),
      completionNote: optional(raw.completionNote, maximumLength: 500),
      protectedVolume: raw.protectedVolume,
      exitedVolume: raw.exitedVolume,
      remainingVolume: raw.remainingVolume,
      entryAveragePrice: raw.entryAveragePrice,
      rules: ExitPlanStructuredValue(graphQL: raw.rules),
      metadata: ExitPlanStructuredValue(graphQL: raw.metadata),
      canEditRules: raw.canEditRules,
      editRoute: optional(raw.editRoute, maximumLength: 240),
      phase: phase,
      dataQuality: dataQuality,
      lastDecision: optional(raw.lastDecision, maximumLength: 500),
      peakPrice: raw.peakPrice,
      peakDrawdownPercent: raw.peakDrawdownPercent,
      trailingFloorPercent: raw.trailingFloorPercent,
      pendingClientOrderID: optional(raw.pendingClientOrderID, maximumLength: 160),
      pendingIntentID: optional(raw.pendingIntentID, maximumLength: 160),
      lastEvaluatedAt: try optionalDate(raw.lastEvaluatedAt, field: "lastEvaluatedAt"),
      lastError: optional(raw.lastError, maximumLength: 500),
      createdAt: try optionalDate(raw.createdAt, field: "createdAt"),
      updatedAt: try optionalDate(raw.updatedAt, field: "updatedAt")
    )
  }

  static func mapCapabilities(
    _ graphQL: QuantXAPI.IOSExitPlansQuery.Data.ExitPlanCapabilities
  ) throws -> ExitPlanCapabilitiesSnapshot {
    let ruleTypes = try graphQL.ruleTypes.map {
      ExitPlanRuleCapability(
        ruleType: try required($0.ruleType, field: "ruleType", maximumLength: 80),
        label: try required($0.label, field: "ruleLabel", maximumLength: 120),
        category: try required($0.category, field: "ruleCategory", maximumLength: 80),
        parameters: ExitPlanStructuredValue(graphQL: $0.parameters)
      )
    }
    guard
      Set(ruleTypes.map(\.ruleType)).count == ruleTypes.count,
      graphQL.completionStrategies.allSatisfy({ optional($0, maximumLength: 80) != nil }),
      graphQL.conflictStrategies.allSatisfy({ optional($0, maximumLength: 80) != nil }),
      graphQL.executionModes.allSatisfy({ optional($0, maximumLength: 80) != nil })
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    return ExitPlanCapabilitiesSnapshot(
      ruleTypes: ruleTypes,
      completionStrategies: graphQL.completionStrategies,
      conflictStrategies: graphQL.conflictStrategies,
      executionModes: graphQL.executionModes,
      ruleSemantics: try required(
        graphQL.ruleSemantics,
        field: "ruleSemantics",
        maximumLength: 1_000
      )
    )
  }

  static func mapCapacity(
    _ graphQL: QuantXAPI.IOSExitPlanDetailQuery.Data.ExitPlanHoldingCapacity,
    expectedPlan: ExitPlanItem,
    context: ExitPlanRepositoryContext
  ) throws -> ExitPlanHoldingCapacitySnapshot {
    guard
      graphQL.accountId == context.activeAccountID,
      graphQL.accountId == expectedPlan.accountID,
      graphQL.instrumentCode == expectedPlan.instrumentCode,
      graphQL.totalVolume >= 0,
      graphQL.availableVolume >= 0,
      graphQL.frozenVolume >= 0,
      graphQL.protectedVolume >= 0,
      graphQL.pendingVolume >= 0,
      graphQL.unallocatedVolume >= 0,
      graphQL.availableVolume <= graphQL.totalVolume,
      graphQL.frozenVolume <= graphQL.totalVolume,
      graphQL.unallocatedVolume <= graphQL.totalVolume
    else {
      throw ExitPlanWorkspaceError.accountScopeMismatch
    }
    let conflicts = try graphQL.conflicts.map {
      ExitPlanCapacityConflict(
        planID: try required($0.planId, field: "conflict.planId", maximumLength: 128),
        sourceType: try required(
          $0.sourceType,
          field: "conflict.sourceType",
          maximumLength: 80
        ),
        status: ExitPlanStatus(serverValue: $0.status),
        remainingVolume: $0.remainingVolume,
        pending: $0.pending
      )
    }
    guard
      conflicts.allSatisfy({ $0.remainingVolume >= 0 }),
      Set(conflicts.map(\.planID)).count == conflicts.count
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    return ExitPlanHoldingCapacitySnapshot(
      accountID: graphQL.accountId,
      instrumentCode: graphQL.instrumentCode,
      totalVolume: graphQL.totalVolume,
      availableVolume: graphQL.availableVolume,
      frozenVolume: graphQL.frozenVolume,
      protectedVolume: graphQL.protectedVolume,
      pendingVolume: graphQL.pendingVolume,
      unallocatedVolume: graphQL.unallocatedVolume,
      conflicts: conflicts
    )
  }

  static func mapEvent(
    eventID: String,
    planID: String,
    eventType: String,
    payload: GraphQLJSON,
    createdAt: String,
    expectedPlanID: String
  ) throws -> ExitPlanEvent {
    guard planID == expectedPlanID else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    guard let date = PortfolioDateParser.parse(createdAt) else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    return ExitPlanEvent(
      id: try required(eventID, field: "eventId", maximumLength: 160),
      planID: planID,
      type: try required(eventType, field: "eventType", maximumLength: 100),
      payload: ExitPlanStructuredValue(graphQL: payload),
      createdAt: date
    )
  }

  static func mapAuthorizationPreview(
    _ graphQL: QuantXAPI.IOSPreviewExitPlanAuthorizationMutation.Data
      .PreviewExitPlanAuthorization.Preview,
    plan: ExitPlanItem,
    idempotencyKey: UUID,
    context: ExitPlanRepositoryContext
  ) throws -> ExitPlanAuthorizationTicket {
    try mapAuthorizationPreview(
      ExitPlanRawAuthorizationPreview(
        challengeID: graphQL.challengeId,
        confirmationToken: graphQL.confirmationToken,
        accountID: graphQL.accountId,
        planID: graphQL.planId,
        instrumentCode: graphQL.instrumentCode,
        bucket: graphQL.bucket,
        sourceType: graphQL.sourceType,
        executionMode: graphQL.executionMode,
        configVersion: graphQL.configVersion,
        protectedVolume: graphQL.protectedVolume,
        exitedVolume: graphQL.exitedVolume,
        remainingVolume: graphQL.remainingVolume,
        rules: graphQL.rules,
        t1Policy: graphQL.t1Policy,
        executionPolicy: graphQL.executionPolicy,
        position: ExitPlanRawAuthorizationPosition(
          totalVolume: graphQL.position.totalVolume,
          availableVolume: graphQL.position.availableVolume,
          frozenVolume: graphQL.position.frozenVolume,
          yesterdayVolume: graphQL.position.yesterdayVolume,
          t1UnavailableVolume: graphQL.position.t1UnavailableVolume,
          updatedAt: graphQL.position.positionUpdatedAt
        ),
        otherProtections: graphQL.otherProtections.map {
          ExitPlanRawAuthorizationConflict(
            planID: $0.planId,
            sourceType: $0.sourceType,
            status: $0.status,
            remainingVolume: $0.remainingVolume,
            configVersion: $0.configVersion,
            pending: $0.pending
          )
        },
        readiness: graphQL.readiness,
        authorizationFingerprint: graphQL.authorizationFingerprint,
        authorizationExpiresAt: graphQL.authorizationExpiresAt,
        challengeExpiresAt: graphQL.challengeExpiresAt,
        warnings: graphQL.warnings
      ),
      plan: plan,
      idempotencyKey: idempotencyKey,
      context: context
    )
  }

  static func mapAuthorizationPreview(
    _ raw: ExitPlanRawAuthorizationPreview,
    plan: ExitPlanItem,
    idempotencyKey: UUID,
    context: ExitPlanRepositoryContext,
    now: Date = Date()
  ) throws -> ExitPlanAuthorizationTicket {
    try validate(context)
    try validate(plan: plan, context: context)
    let mode = ExitPlanExecutionMode(serverValue: raw.executionMode)
    guard
      plan.executionMode == .live,
      mode == .live,
      raw.accountID == context.activeAccountID,
      raw.accountID == plan.accountID,
      raw.planID == plan.id,
      raw.instrumentCode == plan.instrumentCode,
      raw.bucket == plan.bucket,
      raw.sourceType == plan.sourceType,
      raw.configVersion == plan.configVersion,
      raw.protectedVolume == plan.protectedVolume,
      raw.exitedVolume == plan.exitedVolume,
      raw.remainingVolume == plan.remainingVolume,
      ExitPlanStructuredValue(graphQL: raw.rules) == plan.rules,
      raw.protectedVolume >= 0,
      raw.exitedVolume >= 0,
      raw.remainingVolume > 0
    else {
      throw ExitPlanWorkspaceError.contextChanged
    }
    let challengeID = try required(
      raw.challengeID,
      field: "challengeId",
      maximumLength: 160
    )
    let confirmationToken = try required(
      raw.confirmationToken,
      field: "confirmationToken",
      maximumLength: 512
    )
    let fingerprint = try required(
      raw.authorizationFingerprint,
      field: "authorizationFingerprint",
      maximumLength: 64
    )
    guard
      fingerprint.range(
        of: #"^[0-9a-f]{64}$"#,
        options: .regularExpression
      ) != nil
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    let t1Policy = try required(raw.t1Policy, field: "t1Policy", maximumLength: 120)
    guard
      let authorizationExpiresAt = PortfolioDateParser.parse(raw.authorizationExpiresAt),
      let challengeExpiresAt = PortfolioDateParser.parse(raw.challengeExpiresAt),
      authorizationExpiresAt > challengeExpiresAt,
      challengeExpiresAt > now,
      raw.position.totalVolume >= 0,
      raw.position.availableVolume >= 0,
      raw.position.frozenVolume >= 0,
      raw.position.yesterdayVolume >= 0,
      raw.position.t1UnavailableVolume >= 0,
      raw.position.availableVolume <= raw.position.totalVolume,
      raw.position.frozenVolume <= raw.position.totalVolume,
      raw.position.yesterdayVolume <= raw.position.totalVolume,
      raw.position.t1UnavailableVolume <= raw.position.totalVolume,
      raw.position.availableVolume + raw.position.frozenVolume
        + raw.position.t1UnavailableVolume <= raw.position.totalVolume
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    let conflicts = try raw.otherProtections.map {
      ExitPlanAuthorizationConflict(
        planID: try required($0.planID, field: "protection.planId", maximumLength: 128),
        sourceType: try required(
          $0.sourceType,
          field: "protection.sourceType",
          maximumLength: 80
        ),
        status: ExitPlanStatus(serverValue: $0.status),
        remainingVolume: $0.remainingVolume,
        configVersion: $0.configVersion,
        pending: $0.pending
      )
    }
    guard
      conflicts.allSatisfy({
        $0.planID != plan.id
          && $0.remainingVolume >= 0
          && $0.configVersion > 0
      }),
      Set(conflicts.map(\.planID)).count == conflicts.count,
      raw.warnings.count <= 100,
      raw.warnings.allSatisfy({ optional($0, maximumLength: 1_000) != nil })
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    let review = ExitPlanAuthorizationReview(
      id: challengeID,
      accountID: raw.accountID,
      planID: raw.planID,
      instrumentCode: raw.instrumentCode,
      bucket: raw.bucket,
      sourceType: raw.sourceType,
      executionMode: mode,
      configVersion: raw.configVersion,
      protectedVolume: raw.protectedVolume,
      exitedVolume: raw.exitedVolume,
      remainingVolume: raw.remainingVolume,
      rules: ExitPlanStructuredValue(graphQL: raw.rules),
      t1Policy: t1Policy,
      executionPolicy: ExitPlanStructuredValue(graphQL: raw.executionPolicy),
      position: ExitPlanAuthorizationPositionSnapshot(
        totalVolume: raw.position.totalVolume,
        availableVolume: raw.position.availableVolume,
        frozenVolume: raw.position.frozenVolume,
        yesterdayVolume: raw.position.yesterdayVolume,
        t1UnavailableVolume: raw.position.t1UnavailableVolume,
        updatedAt: try optionalDate(raw.position.updatedAt, field: "positionUpdatedAt")
      ),
      otherProtections: conflicts,
      readiness: ExitPlanStructuredValue(graphQL: raw.readiness),
      authorizationFingerprint: fingerprint,
      authorizationExpiresAt: authorizationExpiresAt,
      challengeExpiresAt: challengeExpiresAt,
      warnings: raw.warnings
    )
    return ExitPlanAuthorizationTicket(
      review: review,
      confirmationToken: confirmationToken,
      idempotencyKey: idempotencyKey,
      userID: context.userID,
      deviceSessionID: context.deviceSessionID,
      sessionContextID: context.sessionContextID
    )
  }

  static func validate(
    ticket: ExitPlanAuthorizationTicket,
    context: ExitPlanRepositoryContext
  ) throws {
    try validate(context)
    guard
      ticket.userID == context.userID,
      ticket.deviceSessionID == context.deviceSessionID,
      ticket.sessionContextID == context.sessionContextID,
      ticket.review.accountID == context.activeAccountID,
      !ticket.review.isChallengeExpired(),
      nonempty(ticket.confirmationToken, maximumLength: 512) != nil
    else {
      throw ExitPlanWorkspaceError.contextChanged
    }
  }

  static func validate(_ context: ExitPlanRepositoryContext) throws {
    guard
      nonempty(context.userID, maximumLength: 160) != nil,
      nonempty(context.deviceSessionID, maximumLength: 160) != nil,
      nonempty(context.activeAccountID, maximumLength: 80) != nil,
      context.authorizedAccountIDs == Set([context.activeAccountID])
    else {
      throw ExitPlanWorkspaceError.accountScopeMismatch
    }
  }

  static func validate(
    plan: ExitPlanItem,
    context: ExitPlanRepositoryContext
  ) throws {
    try validate(context)
    guard plan.accountID == context.activeAccountID else {
      throw ExitPlanWorkspaceError.accountScopeMismatch
    }
  }

  static func planSort(_ lhs: ExitPlanItem, _ rhs: ExitPlanItem) -> Bool {
    if lhs.status.isAuthorizable != rhs.status.isAuthorizable {
      return lhs.status.isAuthorizable
    }
    if lhs.updatedAt != rhs.updatedAt {
      return (lhs.updatedAt ?? .distantPast) > (rhs.updatedAt ?? .distantPast)
    }
    return lhs.instrumentCode.localizedStandardCompare(rhs.instrumentCode)
      == .orderedAscending
  }

  static func required(
    _ value: String,
    field: String,
    maximumLength: Int
  ) throws -> String {
    guard let value = nonempty(value, maximumLength: maximumLength), value.count <= maximumLength
    else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    return value
  }

  static func nonempty(_ value: String?, maximumLength: Int) -> String? {
    guard let value else { return nil }
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty, trimmed.count <= maximumLength else { return nil }
    return trimmed
  }

  static func optional(_ value: String?, maximumLength: Int) -> String? {
    nonempty(value, maximumLength: maximumLength)
  }

  static func optionalDate(_ value: String?, field: String) throws -> Date? {
    guard let value else { return nil }
    guard let parsed = PortfolioDateParser.parse(value) else {
      throw ExitPlanWorkspaceError.invalidResponse
    }
    return parsed
  }

  static func rejected(code: String, message: String) -> ExitPlanWorkspaceError {
    switch code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() {
    case "CONFIG_VERSION_CONFLICT", "EXIT_PLAN_CONFIG_VERSION_CONFLICT":
      .versionConflict
    case "CONFIRMATION_EXPIRED", "CHALLENGE_EXPIRED":
      .challengeExpired
    case let normalized:
      .rejected(
        code: normalized.isEmpty ? "EXIT_PLAN_AUTHORIZATION_REJECTED" : normalized,
        message: message.trimmingCharacters(in: .whitespacesAndNewlines)
      )
    }
  }

  static func map(_ error: Error) -> Error {
    if error is CancellationError { return CancellationError() }
    if let error = error as? ExitPlanWorkspaceError { return error }
    if let error = error as? ReadOnlyRepositoryError { return error }
    if error is ReadOnlyMappingError { return ExitPlanWorkspaceError.invalidResponse }
    if let error = error as? ResponseCodeInterceptor.ResponseCodeError {
      return ApolloReadOnlyResponseValidator.mapResponseCode(error)
    }
    return ReadOnlyRepositoryError.transport
  }
}
