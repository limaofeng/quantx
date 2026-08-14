// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSRemoveWatchlistItemMutation: GraphQLMutation {
    static let operationName: String = "IOSRemoveWatchlistItem"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSRemoveWatchlistItem($stockCode: String!, $accountId: String!) { removeWatchlistItem(stockCode: $stockCode, accountId: $accountId) { __typename success message } }"#
      ))

    public var stockCode: String
    public var accountId: String

    public init(
      stockCode: String,
      accountId: String
    ) {
      self.stockCode = stockCode
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "stockCode": stockCode,
      "accountId": accountId
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("removeWatchlistItem", RemoveWatchlistItem.self, arguments: [
          "stockCode": .variable("stockCode"),
          "accountId": .variable("accountId")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSRemoveWatchlistItemMutation.Data.self
      ] }

      var removeWatchlistItem: RemoveWatchlistItem { __data["removeWatchlistItem"] }

      /// RemoveWatchlistItem
      nonisolated struct RemoveWatchlistItem: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistMutationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSRemoveWatchlistItemMutation.Data.RemoveWatchlistItem.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
      }
    }
  }

}