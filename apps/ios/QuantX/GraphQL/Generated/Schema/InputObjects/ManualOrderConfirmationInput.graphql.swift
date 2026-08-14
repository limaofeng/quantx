// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct ManualOrderConfirmationInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      challengeId: String,
      confirmationToken: String
    ) {
      __data = InputDict([
        "challengeId": challengeId,
        "confirmationToken": confirmationToken
      ])
    }

    var challengeId: String {
      get { __data["challengeId"] }
      set { __data["challengeId"] = newValue }
    }

    var confirmationToken: String {
      get { __data["confirmationToken"] }
      set { __data["confirmationToken"] = newValue }
    }
  }

}