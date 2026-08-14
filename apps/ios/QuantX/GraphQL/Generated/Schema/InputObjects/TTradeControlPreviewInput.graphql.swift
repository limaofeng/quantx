// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct TTradeControlPreviewInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      action: GraphQLEnum<TTradeControlAction>,
      policyVersion: Int32,
      idempotencyKey: String,
      snapshotId: String? = nil,
      targetStage: GraphQLNullable<GraphQLEnum<TTradeRolloutTarget>> = nil,
      reason: String? = nil
    ) {
      __data = InputDict([
        "accountId": accountId,
        "action": action,
        "policyVersion": policyVersion,
        "idempotencyKey": idempotencyKey,
        "snapshotId": snapshotId ?? GraphQLNullable.none,
        "targetStage": targetStage,
        "reason": reason ?? GraphQLNullable.none
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var action: GraphQLEnum<TTradeControlAction> {
      get { __data["action"] }
      set { __data["action"] = newValue }
    }

    var policyVersion: Int32 {
      get { __data["policyVersion"] }
      set { __data["policyVersion"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }

    var snapshotId: String? {
      get { __data["snapshotId"] }
      set { __data["snapshotId"] = newValue }
    }

    var targetStage: GraphQLNullable<GraphQLEnum<TTradeRolloutTarget>> {
      get { __data["targetStage"] }
      set { __data["targetStage"] = newValue }
    }

    var reason: String? {
      get { __data["reason"] }
      set { __data["reason"] = newValue }
    }
  }

}