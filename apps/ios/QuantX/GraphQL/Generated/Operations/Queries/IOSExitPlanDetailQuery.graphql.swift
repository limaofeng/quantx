// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSExitPlanDetailQuery: GraphQLQuery {
    static let operationName: String = "IOSExitPlanDetail"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSExitPlanDetail($planId: String!, $accountId: String!, $instrumentCode: String!) { exitPlan(planId: $planId) { __typename ...IOSExitPlanFields } exitPlanHoldingCapacity(instrumentCode: $instrumentCode, accountId: $accountId) { __typename accountId instrumentCode totalVolume availableVolume frozenVolume protectedVolume pendingVolume unallocatedVolume conflicts { __typename planId sourceType status remainingVolume pending } } exitPlanEvents(planId: $planId, limit: 100) { __typename eventId planId eventType payload createdAt } }"#,
        fragments: [IOSExitPlanFields.self]
      ))

    public var planId: String
    public var accountId: String
    public var instrumentCode: String

    public init(
      planId: String,
      accountId: String,
      instrumentCode: String
    ) {
      self.planId = planId
      self.accountId = accountId
      self.instrumentCode = instrumentCode
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "planId": planId,
      "accountId": accountId,
      "instrumentCode": instrumentCode
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("exitPlan", ExitPlan?.self, arguments: ["planId": .variable("planId")]),
        .field("exitPlanHoldingCapacity", ExitPlanHoldingCapacity.self, arguments: [
          "instrumentCode": .variable("instrumentCode"),
          "accountId": .variable("accountId")
        ]),
        .field("exitPlanEvents", [ExitPlanEvent].self, arguments: [
          "planId": .variable("planId"),
          "limit": 100
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSExitPlanDetailQuery.Data.self
      ] }

      var exitPlan: ExitPlan? { __data["exitPlan"] }
      var exitPlanHoldingCapacity: ExitPlanHoldingCapacity { __data["exitPlanHoldingCapacity"] }
      var exitPlanEvents: [ExitPlanEvent] { __data["exitPlanEvents"] }

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
          IOSExitPlanDetailQuery.Data.ExitPlan.self,
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

      /// ExitPlanHoldingCapacity
      nonisolated struct ExitPlanHoldingCapacity: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanHoldingCapacity }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("instrumentCode", String.self),
          .field("totalVolume", Int.self),
          .field("availableVolume", Int.self),
          .field("frozenVolume", Int.self),
          .field("protectedVolume", Int.self),
          .field("pendingVolume", Int.self),
          .field("unallocatedVolume", Int.self),
          .field("conflicts", [Conflict].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSExitPlanDetailQuery.Data.ExitPlanHoldingCapacity.self
        ] }

        var accountId: String { __data["accountId"] }
        var instrumentCode: String { __data["instrumentCode"] }
        var totalVolume: Int { __data["totalVolume"] }
        var availableVolume: Int { __data["availableVolume"] }
        var frozenVolume: Int { __data["frozenVolume"] }
        var protectedVolume: Int { __data["protectedVolume"] }
        var pendingVolume: Int { __data["pendingVolume"] }
        var unallocatedVolume: Int { __data["unallocatedVolume"] }
        var conflicts: [Conflict] { __data["conflicts"] }

        /// ExitPlanHoldingCapacity.Conflict
        nonisolated struct Conflict: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanCapacityConflict }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("planId", String.self),
            .field("sourceType", String.self),
            .field("status", String.self),
            .field("remainingVolume", Int.self),
            .field("pending", Bool.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSExitPlanDetailQuery.Data.ExitPlanHoldingCapacity.Conflict.self
          ] }

          var planId: String { __data["planId"] }
          var sourceType: String { __data["sourceType"] }
          var status: String { __data["status"] }
          var remainingVolume: Int { __data["remainingVolume"] }
          var pending: Bool { __data["pending"] }
        }
      }

      /// ExitPlanEvent
      nonisolated struct ExitPlanEvent: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanEventView }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("eventId", String.self),
          .field("planId", String.self),
          .field("eventType", String.self),
          .field("payload", QuantXAPI.JSON.self),
          .field("createdAt", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSExitPlanDetailQuery.Data.ExitPlanEvent.self
        ] }

        var eventId: String { __data["eventId"] }
        var planId: String { __data["planId"] }
        var eventType: String { __data["eventType"] }
        var payload: QuantXAPI.JSON { __data["payload"] }
        var createdAt: QuantXAPI.DateTime { __data["createdAt"] }
      }
    }
  }

}