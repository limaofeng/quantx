// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct LiquidationPreviewInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      scope: GraphQLEnum<LiquidationScope>,
      completionStrategy: GraphQLEnum<LiquidationCompletionStrategy>,
      conflictStrategy: GraphQLEnum<LiquidationConflictStrategy>,
      idempotencyKey: String,
      instrumentCodes: GraphQLNullable<[String]> = nil,
      executionMode: GraphQLEnum<LiquidationExecutionMode>? = nil
    ) {
      __data = InputDict([
        "accountId": accountId,
        "scope": scope,
        "completionStrategy": completionStrategy,
        "conflictStrategy": conflictStrategy,
        "idempotencyKey": idempotencyKey,
        "instrumentCodes": instrumentCodes,
        "executionMode": executionMode ?? GraphQLNullable.none
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var scope: GraphQLEnum<LiquidationScope> {
      get { __data["scope"] }
      set { __data["scope"] = newValue }
    }

    var completionStrategy: GraphQLEnum<LiquidationCompletionStrategy> {
      get { __data["completionStrategy"] }
      set { __data["completionStrategy"] = newValue }
    }

    var conflictStrategy: GraphQLEnum<LiquidationConflictStrategy> {
      get { __data["conflictStrategy"] }
      set { __data["conflictStrategy"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }

    var instrumentCodes: GraphQLNullable<[String]> {
      get { __data["instrumentCodes"] }
      set { __data["instrumentCodes"] = newValue }
    }

    var executionMode: GraphQLEnum<LiquidationExecutionMode>? {
      get { __data["executionMode"] }
      set { __data["executionMode"] = newValue }
    }
  }

}