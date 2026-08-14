// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSAddWatchlistItemMutation: GraphQLMutation {
    static let operationName: String = "IOSAddWatchlistItem"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSAddWatchlistItem($input: AddWatchlistItemInput!) { addWatchlistItem(input: $input) { __typename success message item { __typename id accountId stockCode instrumentName displayOrder groupName note updatedAt } } }"#
      ))

    public var input: AddWatchlistItemInput

    public init(input: AddWatchlistItemInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("addWatchlistItem", AddWatchlistItem.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSAddWatchlistItemMutation.Data.self
      ] }

      var addWatchlistItem: AddWatchlistItem { __data["addWatchlistItem"] }

      /// AddWatchlistItem
      nonisolated struct AddWatchlistItem: QuantXAPI.SelectionSet {
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
          IOSAddWatchlistItemMutation.Data.AddWatchlistItem.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
        var item: Item? { __data["item"] }

        /// AddWatchlistItem.Item
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
            IOSAddWatchlistItemMutation.Data.AddWatchlistItem.Item.self
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