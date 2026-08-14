// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct ExitPlanAuthorizationConfirmationInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      planId: String,
      expectedConfigVersion: Int32,
      idempotencyKey: String,
      challengeId: String,
      confirmationToken: String
    ) {
      __data = InputDict([
        "accountId": accountId,
        "planId": planId,
        "expectedConfigVersion": expectedConfigVersion,
        "idempotencyKey": idempotencyKey,
        "challengeId": challengeId,
        "confirmationToken": confirmationToken
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var planId: String {
      get { __data["planId"] }
      set { __data["planId"] = newValue }
    }

    var expectedConfigVersion: Int32 {
      get { __data["expectedConfigVersion"] }
      set { __data["expectedConfigVersion"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
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