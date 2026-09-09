// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPrepareTAssistantLegacyInventoryMutation: GraphQLMutation {
    static let operationName: String = "IOSPrepareTAssistantLegacyInventory"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPrepareTAssistantLegacyInventory($requestId: String!, $request: TAssistantLegacyInventoryRequest!) { prepareTAssistantLegacyInventory(requestId: $requestId, request: $request) { __typename success code message engineCommandId } }"#
      ))

    public var requestId: String
    public var request: TAssistantLegacyInventoryRequest

    public init(
      requestId: String,
      request: TAssistantLegacyInventoryRequest
    ) {
      self.requestId = requestId
      self.request = request
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "requestId": requestId,
      "request": request
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("prepareTAssistantLegacyInventory", PrepareTAssistantLegacyInventory.self, arguments: [
          "requestId": .variable("requestId"),
          "request": .variable("request")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPrepareTAssistantLegacyInventoryMutation.Data.self
      ] }

      var prepareTAssistantLegacyInventory: PrepareTAssistantLegacyInventory { __data["prepareTAssistantLegacyInventory"] }

      /// PrepareTAssistantLegacyInventory
      nonisolated struct PrepareTAssistantLegacyInventory: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyMaintenanceResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("engineCommandId", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPrepareTAssistantLegacyInventoryMutation.Data.PrepareTAssistantLegacyInventory.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var engineCommandId: String? { __data["engineCommandId"] }
      }
    }
  }

}