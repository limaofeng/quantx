// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct AccountExecutionControlPreviewInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      action: GraphQLEnum<AccountExecutionControlAction>,
      stateVersion: Int32,
      idempotencyKey: String,
      snapshotId: String? = nil,
      reason: String? = nil,
      clientOrderId: String? = nil,
      quarantineReason: String? = nil
    ) {
      __data = InputDict([
        "accountId": accountId,
        "action": action,
        "stateVersion": stateVersion,
        "idempotencyKey": idempotencyKey,
        "snapshotId": snapshotId ?? GraphQLNullable.none,
        "reason": reason ?? GraphQLNullable.none,
        "clientOrderId": clientOrderId ?? GraphQLNullable.none,
        "quarantineReason": quarantineReason ?? GraphQLNullable.none
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var action: GraphQLEnum<AccountExecutionControlAction> {
      get { __data["action"] }
      set { __data["action"] = newValue }
    }

    var stateVersion: Int32 {
      get { __data["stateVersion"] }
      set { __data["stateVersion"] = newValue }
    }

    var idempotencyKey: String {
      get { __data["idempotencyKey"] }
      set { __data["idempotencyKey"] = newValue }
    }

    var snapshotId: String? {
      get { __data["snapshotId"] }
      set { __data["snapshotId"] = newValue }
    }

    var reason: String? {
      get { __data["reason"] }
      set { __data["reason"] = newValue }
    }

    var clientOrderId: String? {
      get { __data["clientOrderId"] }
      set { __data["clientOrderId"] = newValue }
    }

    var quarantineReason: String? {
      get { __data["quarantineReason"] }
      set { __data["quarantineReason"] = newValue }
    }
  }

}