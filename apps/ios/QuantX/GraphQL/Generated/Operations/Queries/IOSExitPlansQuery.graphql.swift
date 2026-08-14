// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSExitPlansQuery: GraphQLQuery {
    static let operationName: String = "IOSExitPlans"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSExitPlans($accountId: String!) { exitPlans(accountId: $accountId, limit: 200) { __typename ...IOSExitPlanFields } exitPlanCapabilities { __typename ruleTypes { __typename ruleType label category parameters } completionStrategies conflictStrategies executionModes ruleSemantics } }"#,
        fragments: [IOSExitPlanFields.self]
      ))

    public var accountId: String

    public init(accountId: String) {
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["accountId": accountId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("exitPlans", [ExitPlan].self, arguments: [
          "accountId": .variable("accountId"),
          "limit": 200
        ]),
        .field("exitPlanCapabilities", ExitPlanCapabilities.self),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSExitPlansQuery.Data.self
      ] }

      var exitPlans: [ExitPlan] { __data["exitPlans"] }
      var exitPlanCapabilities: ExitPlanCapabilities { __data["exitPlanCapabilities"] }

      /// ExitPlan
      nonisolated struct ExitPlan: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanView }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .fragment(IOSExitPlanFields.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSExitPlansQuery.Data.ExitPlan.self,
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

        struct Fragments: FragmentContainer {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          var iOSExitPlanFields: IOSExitPlanFields { _toFragment() }
        }
      }

      /// ExitPlanCapabilities
      nonisolated struct ExitPlanCapabilities: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanCapabilities }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("ruleTypes", [RuleType].self),
          .field("completionStrategies", [String].self),
          .field("conflictStrategies", [String].self),
          .field("executionModes", [String].self),
          .field("ruleSemantics", String.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSExitPlansQuery.Data.ExitPlanCapabilities.self
        ] }

        var ruleTypes: [RuleType] { __data["ruleTypes"] }
        var completionStrategies: [String] { __data["completionStrategies"] }
        var conflictStrategies: [String] { __data["conflictStrategies"] }
        var executionModes: [String] { __data["executionModes"] }
        var ruleSemantics: String { __data["ruleSemantics"] }

        /// ExitPlanCapabilities.RuleType
        nonisolated struct RuleType: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanRuleCapability }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("ruleType", String.self),
            .field("label", String.self),
            .field("category", String.self),
            .field("parameters", QuantXAPI.JSON.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSExitPlansQuery.Data.ExitPlanCapabilities.RuleType.self
          ] }

          var ruleType: String { __data["ruleType"] }
          var label: String { __data["label"] }
          var category: String { __data["category"] }
          var parameters: QuantXAPI.JSON { __data["parameters"] }
        }
      }
    }
  }

}