// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmStrategyControlMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmStrategyControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmStrategyControl($input: StrategyControlConfirmationInput!) { confirmStrategyControl(input: $input) { __typename success code message challengeId instanceId status } }"#
      ))

    public var input: StrategyControlConfirmationInput

    public init(input: StrategyControlConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmStrategyControl", ConfirmStrategyControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmStrategyControlMutation.Data.self
      ] }

      var confirmStrategyControl: ConfirmStrategyControl { __data["confirmStrategyControl"] }

      /// ConfirmStrategyControl
      nonisolated struct ConfirmStrategyControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyControlConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", String?.self),
          .field("instanceId", String?.self),
          .field("status", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmStrategyControlMutation.Data.ConfirmStrategyControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
        var instanceId: String? { __data["instanceId"] }
        var status: String? { __data["status"] }
      }
    }
  }

}