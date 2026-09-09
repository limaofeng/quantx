// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTAssistantLegacyConfirmationStatusQuery: GraphQLQuery {
    static let operationName: String = "IOSTAssistantLegacyConfirmationStatus"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTAssistantLegacyConfirmationStatus($challengeId: String!) { tAssistantLegacyConfirmationStatus(challengeId: $challengeId) { __typename challengeId status engineCommandId request } }"#
      ))

    public var challengeId: String

    public init(challengeId: String) {
      self.challengeId = challengeId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["challengeId": challengeId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("tAssistantLegacyConfirmationStatus", TAssistantLegacyConfirmationStatus.self, arguments: ["challengeId": .variable("challengeId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTAssistantLegacyConfirmationStatusQuery.Data.self
      ] }

      var tAssistantLegacyConfirmationStatus: TAssistantLegacyConfirmationStatus { __data["tAssistantLegacyConfirmationStatus"] }

      /// TAssistantLegacyConfirmationStatus
      nonisolated struct TAssistantLegacyConfirmationStatus: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyConfirmationStatus }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("challengeId", String.self),
          .field("status", String.self),
          .field("engineCommandId", String?.self),
          .field("request", QuantXAPI.JSON.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTAssistantLegacyConfirmationStatusQuery.Data.TAssistantLegacyConfirmationStatus.self
        ] }

        var challengeId: String { __data["challengeId"] }
        var status: String { __data["status"] }
        var engineCommandId: String? { __data["engineCommandId"] }
        var request: QuantXAPI.JSON { __data["request"] }
      }
    }
  }

}