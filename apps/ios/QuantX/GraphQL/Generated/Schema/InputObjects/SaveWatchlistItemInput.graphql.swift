// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct SaveWatchlistItemInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      stockCode: String,
      groupIds: [ID],
      accountId: GraphQLNullable<String> = nil,
      instrumentName: GraphQLNullable<String> = nil,
      note: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "stockCode": stockCode,
        "groupIds": groupIds,
        "accountId": accountId,
        "instrumentName": instrumentName,
        "note": note
      ])
    }

    var stockCode: String {
      get { __data["stockCode"] }
      set { __data["stockCode"] = newValue }
    }

    var groupIds: [ID] {
      get { __data["groupIds"] }
      set { __data["groupIds"] = newValue }
    }

    var accountId: GraphQLNullable<String> {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var instrumentName: GraphQLNullable<String> {
      get { __data["instrumentName"] }
      set { __data["instrumentName"] = newValue }
    }

    var note: GraphQLNullable<String> {
      get { __data["note"] }
      set { __data["note"] = newValue }
    }
  }

}
