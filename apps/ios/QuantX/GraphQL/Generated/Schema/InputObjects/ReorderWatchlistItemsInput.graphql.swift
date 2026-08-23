// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct ReorderWatchlistItemsInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      itemIds: [ID],
      accountId: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "itemIds": itemIds,
        "accountId": accountId
      ])
    }

    var itemIds: [ID] {
      get { __data["itemIds"] }
      set { __data["itemIds"] = newValue }
    }

    var accountId: GraphQLNullable<String> {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }
  }

}
