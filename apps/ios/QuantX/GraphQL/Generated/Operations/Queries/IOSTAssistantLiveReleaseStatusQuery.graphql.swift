// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTAssistantLiveReleaseStatusQuery: GraphQLQuery {
    static let operationName: String = "IOSTAssistantLiveReleaseStatus"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTAssistantLiveReleaseStatus($challengeId: String!) { tAssistantLiveReleaseStatus(challengeId: $challengeId) { __typename challengeId status engineCommandId executionId executionStatus reasonCode } }"#
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
        .field("tAssistantLiveReleaseStatus", TAssistantLiveReleaseStatus.self, arguments: ["challengeId": .variable("challengeId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTAssistantLiveReleaseStatusQuery.Data.self
      ] }

      var tAssistantLiveReleaseStatus: TAssistantLiveReleaseStatus { __data["tAssistantLiveReleaseStatus"] }

      /// TAssistantLiveReleaseStatus
      nonisolated struct TAssistantLiveReleaseStatus: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantReleaseStatus }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("challengeId", String.self),
          .field("status", String.self),
          .field("engineCommandId", String?.self),
          .field("executionId", String?.self),
          .field("executionStatus", String?.self),
          .field("reasonCode", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTAssistantLiveReleaseStatusQuery.Data.TAssistantLiveReleaseStatus.self
        ] }

        var challengeId: String { __data["challengeId"] }
        var status: String { __data["status"] }
        var engineCommandId: String? { __data["engineCommandId"] }
        var executionId: String? { __data["executionId"] }
        var executionStatus: String? { __data["executionStatus"] }
        var reasonCode: String? { __data["reasonCode"] }
      }
    }
  }

}