// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct ReorderWatchlistInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      symbols: [String],
      accountId: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "symbols": symbols,
        "accountId": accountId
      ])
    }

    var symbols: [String] {
      get { __data["symbols"] }
      set { __data["symbols"] = newValue }
    }

    var accountId: GraphQLNullable<String> {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }
  }

}