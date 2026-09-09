// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewTAssistantLegacyDrainMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewTAssistantLegacyDrain"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewTAssistantLegacyDrain($request: TAssistantLegacyDrainRequest!) { previewTAssistantLegacyDrain(request: $request) { __typename success code message preview { __typename challengeId confirmationToken expiresAt request } } }"#
      ))

    public var request: TAssistantLegacyDrainRequest

    public init(request: TAssistantLegacyDrainRequest) {
      self.request = request
    }

    @_spi(Unsafe) public var __variables: Variables? { ["request": request] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewTAssistantLegacyDrain", PreviewTAssistantLegacyDrain.self, arguments: ["request": .variable("request")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewTAssistantLegacyDrainMutation.Data.self
      ] }

      var previewTAssistantLegacyDrain: PreviewTAssistantLegacyDrain { __data["previewTAssistantLegacyDrain"] }

      /// PreviewTAssistantLegacyDrain
      nonisolated struct PreviewTAssistantLegacyDrain: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyMaintenanceResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewTAssistantLegacyDrainMutation.Data.PreviewTAssistantLegacyDrain.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewTAssistantLegacyDrain.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyDrainPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("expiresAt", QuantXAPI.DateTime.self),
            .field("request", QuantXAPI.JSON.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewTAssistantLegacyDrainMutation.Data.PreviewTAssistantLegacyDrain.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var expiresAt: QuantXAPI.DateTime { __data["expiresAt"] }
          var request: QuantXAPI.JSON { __data["request"] }
        }
      }
    }
  }

}