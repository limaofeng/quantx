// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmManualOrderMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmManualOrder"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmManualOrder($input: ManualOrderConfirmationInput!) { confirmManualOrder(input: $input) { __typename success code message challengeId clientOrderId status } }"#
      ))

    public var input: ManualOrderConfirmationInput

    public init(input: ManualOrderConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmManualOrder", ConfirmManualOrder.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmManualOrderMutation.Data.self
      ] }

      var confirmManualOrder: ConfirmManualOrder { __data["confirmManualOrder"] }

      /// ConfirmManualOrder
      nonisolated struct ConfirmManualOrder: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ManualOrderConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", String?.self),
          .field("clientOrderId", String?.self),
          .field("status", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmManualOrderMutation.Data.ConfirmManualOrder.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
        var clientOrderId: String? { __data["clientOrderId"] }
        var status: String? { __data["status"] }
      }
    }
  }

}