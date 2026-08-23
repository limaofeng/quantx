// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSMarketWatchlistQuery: GraphQLQuery {
    static let operationName: String = "IOSMarketWatchlist"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSMarketWatchlist($accountId: String) { watchlist(accountId: $accountId) { __typename id accountId stockCode instrumentName displayOrder note updatedAt groups { __typename id name displayOrder itemCount } } }"#
      ))

    public var accountId: GraphQLNullable<String>

    public init(accountId: GraphQLNullable<String>) {
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["accountId": accountId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("watchlist", [Watchlist].self, arguments: ["accountId": .variable("accountId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSMarketWatchlistQuery.Data.self
      ] }

      var watchlist: [Watchlist] { __data["watchlist"] }

      /// Watchlist
      nonisolated struct Watchlist: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistItem }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", QuantXAPI.ID.self),
          .field("accountId", String.self),
          .field("stockCode", String.self),
          .field("instrumentName", String?.self),
          .field("displayOrder", Int.self),
          .field("note", String?.self),
          .field("updatedAt", QuantXAPI.DateTime?.self),
          .field("groups", [Group].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSMarketWatchlistQuery.Data.Watchlist.self
        ] }

        var id: QuantXAPI.ID { __data["id"] }
        var accountId: String { __data["accountId"] }
        var stockCode: String { __data["stockCode"] }
        var instrumentName: String? { __data["instrumentName"] }
        var displayOrder: Int { __data["displayOrder"] }
        var note: String? { __data["note"] }
        var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
        var groups: [Group] { __data["groups"] }

        /// Watchlist.Group
        nonisolated struct Group: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistGroup }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("id", QuantXAPI.ID.self),
            .field("name", String.self),
            .field("displayOrder", Int.self),
            .field("itemCount", Int.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSMarketWatchlistQuery.Data.Watchlist.Group.self
          ] }

          var id: QuantXAPI.ID { __data["id"] }
          var name: String { __data["name"] }
          var displayOrder: Int { __data["displayOrder"] }
          var itemCount: Int { __data["itemCount"] }
        }
      }
    }
  }

}
