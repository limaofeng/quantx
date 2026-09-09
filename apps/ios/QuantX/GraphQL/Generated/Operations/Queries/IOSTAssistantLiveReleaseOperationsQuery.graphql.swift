// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTAssistantLiveReleaseOperationsQuery: GraphQLQuery {
    static let operationName: String = "IOSTAssistantLiveReleaseOperations"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTAssistantLiveReleaseOperations($accountId: String!, $limit: Int!) { tAssistantLiveReleaseOperations(accountId: $accountId, limit: $limit) { __typename challengeId accountId configVersionId createdAt } }"#
      ))

    public var accountId: String
    public var limit: Int32

    public init(
      accountId: String,
      limit: Int32
    ) {
      self.accountId = accountId
      self.limit = limit
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "accountId": accountId,
      "limit": limit
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("tAssistantLiveReleaseOperations", [TAssistantLiveReleaseOperation].self, arguments: [
          "accountId": .variable("accountId"),
          "limit": .variable("limit")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTAssistantLiveReleaseOperationsQuery.Data.self
      ] }

      var tAssistantLiveReleaseOperations: [TAssistantLiveReleaseOperation] { __data["tAssistantLiveReleaseOperations"] }

      /// TAssistantLiveReleaseOperation
      nonisolated struct TAssistantLiveReleaseOperation: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantReleaseOperation }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("challengeId", String.self),
          .field("accountId", String.self),
          .field("configVersionId", String.self),
          .field("createdAt", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTAssistantLiveReleaseOperationsQuery.Data.TAssistantLiveReleaseOperation.self
        ] }

        var challengeId: String { __data["challengeId"] }
        var accountId: String { __data["accountId"] }
        var configVersionId: String { __data["configVersionId"] }
        var createdAt: QuantXAPI.DateTime { __data["createdAt"] }
      }
    }
  }

}