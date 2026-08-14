// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSExitPlanFields: QuantXAPI.SelectionSet, Fragment {
    static var fragmentDefinition: StaticString {
      #"fragment IOSExitPlanFields on ExitPlanView { __typename planId groupId accountId instrumentCode bucket sourceType sourceId strategyRunId enabled status executionMode autoExitAuthorized autoExitAuthorizationConfigVersion autoExitAuthorizationExpiresAt configVersion completionStrategy completionNote protectedVolume exitedVolume remainingVolume entryAvgPrice rules metadata canEditRules editRoute phase dataQuality lastDecision peakPrice peakDrawdownPct trailingFloorPct pendingClientOrderId pendingIntentId lastEvaluatedAt lastError createdAt updatedAt }"#
    }

    let __data: DataDict
    init(_dataDict: DataDict) { __data = _dataDict }

    static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanView }
    static var __selections: [ApolloAPI.Selection] { [
      .field("__typename", String.self),
      .field("planId", String.self),
      .field("groupId", String?.self),
      .field("accountId", String.self),
      .field("instrumentCode", String.self),
      .field("bucket", String.self),
      .field("sourceType", String.self),
      .field("sourceId", String.self),
      .field("strategyRunId", String?.self),
      .field("enabled", Bool.self),
      .field("status", String.self),
      .field("executionMode", String.self),
      .field("autoExitAuthorized", Bool.self),
      .field("autoExitAuthorizationConfigVersion", Int?.self),
      .field("autoExitAuthorizationExpiresAt", QuantXAPI.DateTime?.self),
      .field("configVersion", Int.self),
      .field("completionStrategy", String?.self),
      .field("completionNote", String?.self),
      .field("protectedVolume", Int.self),
      .field("exitedVolume", Int.self),
      .field("remainingVolume", Int.self),
      .field("entryAvgPrice", Double.self),
      .field("rules", QuantXAPI.JSON.self),
      .field("metadata", QuantXAPI.JSON.self),
      .field("canEditRules", Bool.self),
      .field("editRoute", String?.self),
      .field("phase", String.self),
      .field("dataQuality", String.self),
      .field("lastDecision", String?.self),
      .field("peakPrice", Double.self),
      .field("peakDrawdownPct", Double.self),
      .field("trailingFloorPct", Double?.self),
      .field("pendingClientOrderId", String?.self),
      .field("pendingIntentId", String?.self),
      .field("lastEvaluatedAt", QuantXAPI.DateTime?.self),
      .field("lastError", String?.self),
      .field("createdAt", QuantXAPI.DateTime?.self),
      .field("updatedAt", QuantXAPI.DateTime?.self),
    ] }
    static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
      IOSExitPlanFields.self
    ] }

    var planId: String { __data["planId"] }
    var groupId: String? { __data["groupId"] }
    var accountId: String { __data["accountId"] }
    var instrumentCode: String { __data["instrumentCode"] }
    var bucket: String { __data["bucket"] }
    var sourceType: String { __data["sourceType"] }
    var sourceId: String { __data["sourceId"] }
    var strategyRunId: String? { __data["strategyRunId"] }
    var enabled: Bool { __data["enabled"] }
    var status: String { __data["status"] }
    var executionMode: String { __data["executionMode"] }
    var autoExitAuthorized: Bool { __data["autoExitAuthorized"] }
    var autoExitAuthorizationConfigVersion: Int? { __data["autoExitAuthorizationConfigVersion"] }
    var autoExitAuthorizationExpiresAt: QuantXAPI.DateTime? { __data["autoExitAuthorizationExpiresAt"] }
    var configVersion: Int { __data["configVersion"] }
    var completionStrategy: String? { __data["completionStrategy"] }
    var completionNote: String? { __data["completionNote"] }
    var protectedVolume: Int { __data["protectedVolume"] }
    var exitedVolume: Int { __data["exitedVolume"] }
    var remainingVolume: Int { __data["remainingVolume"] }
    var entryAvgPrice: Double { __data["entryAvgPrice"] }
    var rules: QuantXAPI.JSON { __data["rules"] }
    var metadata: QuantXAPI.JSON { __data["metadata"] }
    var canEditRules: Bool { __data["canEditRules"] }
    var editRoute: String? { __data["editRoute"] }
    var phase: String { __data["phase"] }
    var dataQuality: String { __data["dataQuality"] }
    var lastDecision: String? { __data["lastDecision"] }
    var peakPrice: Double { __data["peakPrice"] }
    var peakDrawdownPct: Double { __data["peakDrawdownPct"] }
    var trailingFloorPct: Double? { __data["trailingFloorPct"] }
    var pendingClientOrderId: String? { __data["pendingClientOrderId"] }
    var pendingIntentId: String? { __data["pendingIntentId"] }
    var lastEvaluatedAt: QuantXAPI.DateTime? { __data["lastEvaluatedAt"] }
    var lastError: String? { __data["lastError"] }
    var createdAt: QuantXAPI.DateTime? { __data["createdAt"] }
    var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
  }

}