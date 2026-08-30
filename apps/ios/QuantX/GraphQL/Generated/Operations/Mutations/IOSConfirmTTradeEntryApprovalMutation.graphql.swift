// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmTTradeEntryApprovalMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmTTradeEntryApproval"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmTTradeEntryApproval($runId: String!, $intentId: String!, $confirmationToken: String!, $expectation: TTradeCandidateApprovalExpectationInput!) { confirmTTradeEntryApproval( runId: $runId intentId: $intentId confirmationToken: $confirmationToken expectation: $expectation ) { __typename success code message challengeId } }"#
      ))

    public var runId: String
    public var intentId: String
    public var confirmationToken: String
    public var expectation: TTradeCandidateApprovalExpectationInput

    public init(
      runId: String,
      intentId: String,
      confirmationToken: String,
      expectation: TTradeCandidateApprovalExpectationInput
    ) {
      self.runId = runId
      self.intentId = intentId
      self.confirmationToken = confirmationToken
      self.expectation = expectation
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "runId": runId,
      "intentId": intentId,
      "confirmationToken": confirmationToken,
      "expectation": expectation
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmTTradeEntryApproval", ConfirmTTradeEntryApproval.self, arguments: [
          "runId": .variable("runId"),
          "intentId": .variable("intentId"),
          "confirmationToken": .variable("confirmationToken"),
          "expectation": .variable("expectation")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmTTradeEntryApprovalMutation.Data.self
      ] }

      var confirmTTradeEntryApproval: ConfirmTTradeEntryApproval { __data["confirmTTradeEntryApproval"] }

      /// ConfirmTTradeEntryApproval
      nonisolated struct ConfirmTTradeEntryApproval: QuantXAPI.SelectionSet {
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
          IOSConfirmTTradeEntryApprovalMutation.Data.ConfirmTTradeEntryApproval.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
      }
    }
  }

}
