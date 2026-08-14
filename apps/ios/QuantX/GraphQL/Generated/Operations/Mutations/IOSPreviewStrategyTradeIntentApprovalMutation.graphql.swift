// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewStrategyTradeIntentApprovalMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewStrategyTradeIntentApproval"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewStrategyTradeIntentApproval($runId: String!, $intentId: String!) { previewStrategyTradeIntentApproval(runId: $runId, intentId: $intentId) { __typename success code message preview { __typename challengeId confirmationToken action accountId runId intentId instrumentCode side bucket reason targetVolume referencePrice estimatedAmount signalExpiresAt challengeExpiresAt warnings } } }"#
      ))

    public var runId: String
    public var intentId: String

    public init(
      runId: String,
      intentId: String
    ) {
      self.runId = runId
      self.intentId = intentId
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "runId": runId,
      "intentId": intentId
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewStrategyTradeIntentApproval", PreviewStrategyTradeIntentApproval.self, arguments: [
          "runId": .variable("runId"),
          "intentId": .variable("intentId")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewStrategyTradeIntentApprovalMutation.Data.self
      ] }

      var previewStrategyTradeIntentApproval: PreviewStrategyTradeIntentApproval { __data["previewStrategyTradeIntentApproval"] }

      /// PreviewStrategyTradeIntentApproval
      nonisolated struct PreviewStrategyTradeIntentApproval: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TradeApprovalPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewStrategyTradeIntentApprovalMutation.Data.PreviewStrategyTradeIntentApproval.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewStrategyTradeIntentApproval.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TradeApprovalPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("action", String.self),
            .field("accountId", String.self),
            .field("runId", String.self),
            .field("intentId", String.self),
            .field("instrumentCode", String.self),
            .field("side", String.self),
            .field("bucket", String.self),
            .field("reason", String.self),
            .field("targetVolume", Int?.self),
            .field("referencePrice", Double?.self),
            .field("estimatedAmount", Double?.self),
            .field("signalExpiresAt", QuantXAPI.DateTime?.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewStrategyTradeIntentApprovalMutation.Data.PreviewStrategyTradeIntentApproval.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var action: String { __data["action"] }
          var accountId: String { __data["accountId"] }
          var runId: String { __data["runId"] }
          var intentId: String { __data["intentId"] }
          var instrumentCode: String { __data["instrumentCode"] }
          var side: String { __data["side"] }
          var bucket: String { __data["bucket"] }
          var reason: String { __data["reason"] }
          var targetVolume: Int? { __data["targetVolume"] }
          var referencePrice: Double? { __data["referencePrice"] }
          var estimatedAmount: Double? { __data["estimatedAmount"] }
          var signalExpiresAt: QuantXAPI.DateTime? { __data["signalExpiresAt"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var warnings: [String] { __data["warnings"] }
        }
      }
    }
  }

}