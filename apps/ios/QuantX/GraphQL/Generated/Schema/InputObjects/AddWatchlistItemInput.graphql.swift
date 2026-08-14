// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct AddWatchlistItemInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      stockCode: String,
      accountId: GraphQLNullable<String> = nil,
      instrumentName: GraphQLNullable<String> = nil,
      displayOrder: GraphQLNullable<Int32> = nil,
      groupName: GraphQLNullable<String> = nil,
      note: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "stockCode": stockCode,
        "accountId": accountId,
        "instrumentName": instrumentName,
        "displayOrder": displayOrder,
        "groupName": groupName,
        "note": note
      ])
    }

    var stockCode: String {
      get { __data["stockCode"] }
      set { __data["stockCode"] = newValue }
    }

    var accountId: GraphQLNullable<String> {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var instrumentName: GraphQLNullable<String> {
      get { __data["instrumentName"] }
      set { __data["instrumentName"] = newValue }
    }

    var displayOrder: GraphQLNullable<Int32> {
      get { __data["displayOrder"] }
      set { __data["displayOrder"] = newValue }
    }

    var groupName: GraphQLNullable<String> {
      get { __data["groupName"] }
      set { __data["groupName"] = newValue }
    }

    var note: GraphQLNullable<String> {
      get { __data["note"] }
      set { __data["note"] = newValue }
    }
  }

}