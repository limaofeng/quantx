// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPauseStrategyInstanceMutation: GraphQLMutation {
    static let operationName: String = "IOSPauseStrategyInstance"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPauseStrategyInstance($instanceId: String!) { pauseStrategyInstance(instanceId: $instanceId) { __typename success message } }"#
      ))

    public var instanceId: String

    public init(instanceId: String) {
      self.instanceId = instanceId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["instanceId": instanceId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("pauseStrategyInstance", PauseStrategyInstance.self, arguments: ["instanceId": .variable("instanceId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPauseStrategyInstanceMutation.Data.self
      ] }

      var pauseStrategyInstance: PauseStrategyInstance { __data["pauseStrategyInstance"] }

      /// PauseStrategyInstance
      nonisolated struct PauseStrategyInstance: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.OperationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPauseStrategyInstanceMutation.Data.PauseStrategyInstance.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
      }
    }
  }

}