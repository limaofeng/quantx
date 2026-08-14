// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewManualOrderMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewManualOrder"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewManualOrder($input: ManualOrderPreviewInput!) { previewManualOrder(input: $input) { __typename success code message preview { __typename challengeId confirmationToken accountId instrumentCode side priceType volume requestedVolume finalVolume limitPrice referencePrice estimatedAmount estimatedFees availableCash availableVolume idempotencyKey executionMode quoteTimestamp challengeExpiresAt riskDecisionId riskAction riskReasonCode riskReasonDetail warnings } } }"#
      ))

    public var input: ManualOrderPreviewInput

    public init(input: ManualOrderPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewManualOrder", PreviewManualOrder.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewManualOrderMutation.Data.self
      ] }

      var previewManualOrder: PreviewManualOrder { __data["previewManualOrder"] }

      /// PreviewManualOrder
      nonisolated struct PreviewManualOrder: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ManualOrderPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewManualOrderMutation.Data.PreviewManualOrder.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewManualOrder.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ManualOrderPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("accountId", String.self),
            .field("instrumentCode", String.self),
            .field("side", GraphQLEnum<QuantXAPI.ManualOrderSide>.self),
            .field("priceType", GraphQLEnum<QuantXAPI.ManualOrderPriceType>.self),
            .field("volume", Int.self),
            .field("requestedVolume", Int.self),
            .field("finalVolume", Int.self),
            .field("limitPrice", Double?.self),
            .field("referencePrice", Double.self),
            .field("estimatedAmount", Double.self),
            .field("estimatedFees", Double?.self),
            .field("availableCash", Double.self),
            .field("availableVolume", Int?.self),
            .field("idempotencyKey", String.self),
            .field("executionMode", String.self),
            .field("quoteTimestamp", QuantXAPI.DateTime.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("riskDecisionId", String.self),
            .field("riskAction", String.self),
            .field("riskReasonCode", String.self),
            .field("riskReasonDetail", String.self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewManualOrderMutation.Data.PreviewManualOrder.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var accountId: String { __data["accountId"] }
          var instrumentCode: String { __data["instrumentCode"] }
          var side: GraphQLEnum<QuantXAPI.ManualOrderSide> { __data["side"] }
          var priceType: GraphQLEnum<QuantXAPI.ManualOrderPriceType> { __data["priceType"] }
          var volume: Int { __data["volume"] }
          var requestedVolume: Int { __data["requestedVolume"] }
          var finalVolume: Int { __data["finalVolume"] }
          var limitPrice: Double? { __data["limitPrice"] }
          var referencePrice: Double { __data["referencePrice"] }
          var estimatedAmount: Double { __data["estimatedAmount"] }
          var estimatedFees: Double? { __data["estimatedFees"] }
          var availableCash: Double { __data["availableCash"] }
          var availableVolume: Int? { __data["availableVolume"] }
          var idempotencyKey: String { __data["idempotencyKey"] }
          var executionMode: String { __data["executionMode"] }
          var quoteTimestamp: QuantXAPI.DateTime { __data["quoteTimestamp"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var riskDecisionId: String { __data["riskDecisionId"] }
          var riskAction: String { __data["riskAction"] }
          var riskReasonCode: String { __data["riskReasonCode"] }
          var riskReasonDetail: String { __data["riskReasonDetail"] }
          var warnings: [String] { __data["warnings"] }
        }
      }
    }
  }

}