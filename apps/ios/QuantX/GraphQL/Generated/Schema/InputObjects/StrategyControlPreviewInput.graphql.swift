// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct StrategyControlPreviewInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      instanceId: String,
      action: GraphQLEnum<StrategyControlAction>,
      expectedConfigVersion: String,
      idempotencyKey: String
    ) {
      __data = InputDict([
        "accountId": accountId,
        "instanceId": instanceId,
        "action": action,
        "expectedConfigVersion": expectedConfigVersion,
        "idempotencyKey": idempotencyKey
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var instanceId: String {
      get { __data["instanceId"] }
      set { __data["instanceId"] = newValue }
    }

    var action: GraphQLEnum<StrategyControlAction> {
      get { __data["action"] }
      set { __data["action"] = newValue }
    }

    var expectedConfigVersion: String {
      get { __data["expectedConfigVersion"] }
      set { __data["expectedConfigVersion"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }
  }

}