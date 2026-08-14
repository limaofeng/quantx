// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmStrategyTradeIntentApprovalMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmStrategyTradeIntentApproval"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmStrategyTradeIntentApproval($runId: String!, $intentId: String!, $confirmationToken: String!) { confirmStrategyTradeIntentApproval( runId: $runId intentId: $intentId confirmationToken: $confirmationToken ) { __typename success code message challengeId } }"#
      ))

    public var runId: String
    public var intentId: String
    public var confirmationToken: String

    public init(
      runId: String,
      intentId: String,
      confirmationToken: String
    ) {
      self.runId = runId
      self.intentId = intentId
      self.confirmationToken = confirmationToken
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "runId": runId,
      "intentId": intentId,
      "confirmationToken": confirmationToken
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmStrategyTradeIntentApproval", ConfirmStrategyTradeIntentApproval.self, arguments: [
          "runId": .variable("runId"),
          "intentId": .variable("intentId"),
          "confirmationToken": .variable("confirmationToken")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmStrategyTradeIntentApprovalMutation.Data.self
      ] }

      var confirmStrategyTradeIntentApproval: ConfirmStrategyTradeIntentApproval { __data["confirmStrategyTradeIntentApproval"] }

      /// ConfirmStrategyTradeIntentApproval
      nonisolated struct ConfirmStrategyTradeIntentApproval: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TradeApprovalConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmStrategyTradeIntentApprovalMutation.Data.ConfirmStrategyTradeIntentApproval.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
      }
    }
  }

}