// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct CancelOrderInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: GraphQLNullable<String> = nil,
      orderId: Int32,
      idempotencyKey: GraphQLNullable<String> = nil
    ) {
      __data = InputDict([
        "accountId": accountId,
        "orderId": orderId,
        "idempotencyKey": idempotencyKey
      ])
    }

    var accountId: GraphQLNullable<String> {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var orderId: Int32 {
      get { __data["orderId"] }
      set { __data["orderId"] = newValue }
    }

    var idempotencyKey: GraphQLNullable<String> {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }
  }

}