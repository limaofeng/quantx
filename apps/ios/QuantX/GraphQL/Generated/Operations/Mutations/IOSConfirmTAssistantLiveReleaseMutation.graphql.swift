// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmTAssistantLiveReleaseMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmTAssistantLiveRelease"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmTAssistantLiveRelease($challengeId: String!, $confirmationToken: String!) { confirmTAssistantLiveRelease( challengeId: $challengeId confirmationToken: $confirmationToken ) { __typename success code message engineCommandId } }"#
      ))

    public var challengeId: String
    public var confirmationToken: String

    public init(
      challengeId: String,
      confirmationToken: String
    ) {
      self.challengeId = challengeId
      self.confirmationToken = confirmationToken
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "challengeId": challengeId,
      "confirmationToken": confirmationToken
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmTAssistantLiveRelease", ConfirmTAssistantLiveRelease.self, arguments: [
          "challengeId": .variable("challengeId"),
          "confirmationToken": .variable("confirmationToken")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmTAssistantLiveReleaseMutation.Data.self
      ] }

      var confirmTAssistantLiveRelease: ConfirmTAssistantLiveRelease { __data["confirmTAssistantLiveRelease"] }

      /// ConfirmTAssistantLiveRelease
      nonisolated struct ConfirmTAssistantLiveRelease: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantReleaseResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("engineCommandId", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmTAssistantLiveReleaseMutation.Data.ConfirmTAssistantLiveRelease.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var engineCommandId: String? { __data["engineCommandId"] }
      }
    }
  }

}