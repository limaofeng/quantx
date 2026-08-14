// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSReorderWatchlistMutation: GraphQLMutation {
    static let operationName: String = "IOSReorderWatchlist"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSReorderWatchlist($input: ReorderWatchlistInput!) { reorderWatchlist(input: $input) { __typename success message items { __typename id accountId stockCode instrumentName displayOrder groupName note updatedAt } } }"#
      ))

    public var input: ReorderWatchlistInput

    public init(input: ReorderWatchlistInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("reorderWatchlist", ReorderWatchlist.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSReorderWatchlistMutation.Data.self
      ] }

      var reorderWatchlist: ReorderWatchlist { __data["reorderWatchlist"] }

      /// ReorderWatchlist
      nonisolated struct ReorderWatchlist: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistMutationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
          .field("items", [Item].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSReorderWatchlistMutation.Data.ReorderWatchlist.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
        var items: [Item] { __data["items"] }

        /// ReorderWatchlist.Item
        nonisolated struct Item: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistItem }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("id", String.self),
            .field("accountId", String.self),
            .field("stockCode", String.self),
            .field("instrumentName", String?.self),
            .field("displayOrder", Int.self),
            .field("groupName", String?.self),
            .field("note", String?.self),
            .field("updatedAt", QuantXAPI.DateTime?.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSReorderWatchlistMutation.Data.ReorderWatchlist.Item.self
          ] }

          var id: String { __data["id"] }
          var accountId: String { __data["accountId"] }
          var stockCode: String { __data["stockCode"] }
          var instrumentName: String? { __data["instrumentName"] }
          var displayOrder: Int { __data["displayOrder"] }
          var groupName: String? { __data["groupName"] }
          var note: String? { __data["note"] }
          var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
        }
      }
    }
  }

}