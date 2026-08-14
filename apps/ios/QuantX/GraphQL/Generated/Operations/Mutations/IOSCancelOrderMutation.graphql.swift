// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSCancelOrderMutation: GraphQLMutation {
    static let operationName: String = "IOSCancelOrder"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSCancelOrder($input: CancelOrderInput!) { cancelOrder(input: $input) { __typename success message orderId clientOrderId status } }"#
      ))

    public var input: CancelOrderInput

    public init(input: CancelOrderInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("cancelOrder", CancelOrder.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSCancelOrderMutation.Data.self
      ] }

      var cancelOrder: CancelOrder { __data["cancelOrder"] }

      /// CancelOrder
      nonisolated struct CancelOrder: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.CancelOrderResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
          .field("orderId", Int?.self),
          .field("clientOrderId", String?.self),
          .field("status", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSCancelOrderMutation.Data.CancelOrder.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
        var orderId: Int? { __data["orderId"] }
        var clientOrderId: String? { __data["clientOrderId"] }
        var status: String? { __data["status"] }
      }
    }
  }

}