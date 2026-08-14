// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSUpdateStrategyInstanceParametersMutation: GraphQLMutation {
    static let operationName: String = "IOSUpdateStrategyInstanceParameters"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSUpdateStrategyInstanceParameters($instanceId: String!, $input: StrategyInstanceParameterUpdateInput!) { updateStrategyInstanceParameters(instanceId: $instanceId, input: $input) { __typename id mode status parameterVersion updatedAt } }"#
      ))

    public var instanceId: String
    public var input: StrategyInstanceParameterUpdateInput

    public init(
      instanceId: String,
      input: StrategyInstanceParameterUpdateInput
    ) {
      self.instanceId = instanceId
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "instanceId": instanceId,
      "input": input
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("updateStrategyInstanceParameters", UpdateStrategyInstanceParameters?.self, arguments: [
          "instanceId": .variable("instanceId"),
          "input": .variable("input")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSUpdateStrategyInstanceParametersMutation.Data.self
      ] }

      var updateStrategyInstanceParameters: UpdateStrategyInstanceParameters? { __data["updateStrategyInstanceParameters"] }

      /// UpdateStrategyInstanceParameters
      nonisolated struct UpdateStrategyInstanceParameters: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyInstance }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("mode", GraphQLEnum<QuantXAPI.StrategyRunMode>.self),
          .field("status", GraphQLEnum<QuantXAPI.StrategyRunStatus>.self),
          .field("parameterVersion", String.self),
          .field("updatedAt", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSUpdateStrategyInstanceParametersMutation.Data.UpdateStrategyInstanceParameters.self
        ] }

        var id: String { __data["id"] }
        var mode: GraphQLEnum<QuantXAPI.StrategyRunMode> { __data["mode"] }
        var status: GraphQLEnum<QuantXAPI.StrategyRunStatus> { __data["status"] }
        var parameterVersion: String { __data["parameterVersion"] }
        var updatedAt: QuantXAPI.DateTime { __data["updatedAt"] }
      }
    }
  }

}