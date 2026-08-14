// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct ManualOrderPreviewInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      instrumentCode: String,
      side: GraphQLEnum<ManualOrderSide>,
      priceType: GraphQLEnum<ManualOrderPriceType>,
      volume: Int32,
      idempotencyKey: String,
      limitPrice: GraphQLNullable<Double> = nil
    ) {
      __data = InputDict([
        "accountId": accountId,
        "instrumentCode": instrumentCode,
        "side": side,
        "priceType": priceType,
        "volume": volume,
        "idempotencyKey": idempotencyKey,
        "limitPrice": limitPrice
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var instrumentCode: String {
      get { __data["instrumentCode"] }
      set { __data["instrumentCode"] = newValue }
    }

    var side: GraphQLEnum<ManualOrderSide> {
      get { __data["side"] }
      set { __data["side"] = newValue }
    }

    var priceType: GraphQLEnum<ManualOrderPriceType> {
      get { __data["priceType"] }
      set { __data["priceType"] = newValue }
    }

    var volume: Int32 {
      get { __data["volume"] }
      set { __data["volume"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }

    var limitPrice: GraphQLNullable<Double> {
      get { __data["limitPrice"] }
      set { __data["limitPrice"] = newValue }
    }
  }

}