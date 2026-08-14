// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSStrategyInstancesQuery: GraphQLQuery {
    static let operationName: String = "IOSStrategyInstances"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSStrategyInstances { strategyInstances { __typename id strategyKey strategyId strategyName instrumentCode displayName status mode parameterVersion createdAt updatedAt lastDecisionAt latestExecutionStatus } }"#
      ))

    public init() {}

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("strategyInstances", [StrategyInstance].self),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSStrategyInstancesQuery.Data.self
      ] }

      var strategyInstances: [StrategyInstance] { __data["strategyInstances"] }

      /// StrategyInstance
      nonisolated struct StrategyInstance: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyInstance }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("strategyKey", String.self),
          .field("strategyId", Int?.self),
          .field("strategyName", String?.self),
          .field("instrumentCode", String.self),
          .field("displayName", String.self),
          .field("status", GraphQLEnum<QuantXAPI.StrategyRunStatus>.self),
          .field("mode", GraphQLEnum<QuantXAPI.StrategyRunMode>.self),
          .field("parameterVersion", String.self),
          .field("createdAt", QuantXAPI.DateTime.self),
          .field("updatedAt", QuantXAPI.DateTime.self),
          .field("lastDecisionAt", QuantXAPI.DateTime?.self),
          .field("latestExecutionStatus", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSStrategyInstancesQuery.Data.StrategyInstance.self
        ] }

        var id: String { __data["id"] }
        var strategyKey: String { __data["strategyKey"] }
        var strategyId: Int? { __data["strategyId"] }
        var strategyName: String? { __data["strategyName"] }
        var instrumentCode: String { __data["instrumentCode"] }
        var displayName: String { __data["displayName"] }
        var status: GraphQLEnum<QuantXAPI.StrategyRunStatus> { __data["status"] }
        var mode: GraphQLEnum<QuantXAPI.StrategyRunMode> { __data["mode"] }
        var parameterVersion: String { __data["parameterVersion"] }
        var createdAt: QuantXAPI.DateTime { __data["createdAt"] }
        var updatedAt: QuantXAPI.DateTime { __data["updatedAt"] }
        var lastDecisionAt: QuantXAPI.DateTime? { __data["lastDecisionAt"] }
        var latestExecutionStatus: String? { __data["latestExecutionStatus"] }
      }
    }
  }

}