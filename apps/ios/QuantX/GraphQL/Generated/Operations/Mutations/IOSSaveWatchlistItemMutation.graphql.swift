// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSSaveWatchlistItemMutation: GraphQLMutation {
    static let operationName: String = "IOSSaveWatchlistItem"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSSaveWatchlistItem($input: SaveWatchlistItemInput!) { saveWatchlistItem(input: $input) { __typename success message item { __typename id accountId stockCode instrumentName displayOrder note updatedAt groups { __typename id name displayOrder itemCount } } } }"#
      ))

    public var input: SaveWatchlistItemInput

    public init(input: SaveWatchlistItemInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("saveWatchlistItem", SaveWatchlistItem.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSSaveWatchlistItemMutation.Data.self
      ] }

      var saveWatchlistItem: SaveWatchlistItem { __data["saveWatchlistItem"] }

      /// SaveWatchlistItem
      nonisolated struct SaveWatchlistItem: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.WatchlistMutationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
          .field("item", Item?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSSaveWatchlistItemMutation.Data.SaveWatchlistItem.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
        var item: Item? { __data["item"] }

        /// SaveWatchlistItem.Item
        nonisolated struct Item: QuantXAPI.SelectionSet {
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
            IOSSaveWatchlistItemMutation.Data.SaveWatchlistItem.Item.self
          ] }

          var id: QuantXAPI.ID { __data["id"] }
          var accountId: String { __data["accountId"] }
          var stockCode: String { __data["stockCode"] }
          var instrumentName: String? { __data["instrumentName"] }
          var displayOrder: Int { __data["displayOrder"] }
          var note: String? { __data["note"] }
          var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
          var groups: [Group] { __data["groups"] }

          /// SaveWatchlistItem.Item.Group
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
              IOSSaveWatchlistItemMutation.Data.SaveWatchlistItem.Item.Group.self
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

}
