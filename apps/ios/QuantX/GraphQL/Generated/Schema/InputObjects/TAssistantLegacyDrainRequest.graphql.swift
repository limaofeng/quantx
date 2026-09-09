// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct TAssistantLegacyDrainRequest: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      configId: String,
      runId: String,
      expectedHeadVersion: Int32,
      inventoryOperationId: String,
      expectedInventoryHash: String,
      windowStart: DateTime,
      windowEnd: DateTime
    ) {
      __data = InputDict([
        "accountId": accountId,
        "configId": configId,
        "runId": runId,
        "expectedHeadVersion": expectedHeadVersion,
        "inventoryOperationId": inventoryOperationId,
        "expectedInventoryHash": expectedInventoryHash,
        "windowStart": windowStart,
        "windowEnd": windowEnd
      ])
    }

    var accountId: String {
      get { __data["accountId"] }
      set { __data["accountId"] = newValue }
    }

    var configId: String {
      get { __data["configId"] }
      set { __data["configId"] = newValue }
    }

    var runId: String {
      get { __data["runId"] }
      set { __data["runId"] = newValue }
    }

    var expectedHeadVersion: Int32 {
      get { __data["expectedHeadVersion"] }
      set { __data["expectedHeadVersion"] = newValue }
    }

    var inventoryOperationId: String {
      get { __data["inventoryOperationId"] }
      set { __data["inventoryOperationId"] = newValue }
    }

    var expectedInventoryHash: String {
      get { __data["expectedInventoryHash"] }
      set { __data["expectedInventoryHash"] = newValue }
    }

    var windowStart: DateTime {
      get { __data["windowStart"] }
      set { __data["windowStart"] = newValue }
    }

    var windowEnd: DateTime {
      get { __data["windowEnd"] }
      set { __data["windowEnd"] = newValue }
    }
  }

}