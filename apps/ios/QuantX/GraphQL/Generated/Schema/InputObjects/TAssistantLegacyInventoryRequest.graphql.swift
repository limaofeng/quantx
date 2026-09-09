// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct TAssistantLegacyInventoryRequest: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      accountId: String,
      configId: String,
      runId: String,
      expectedHeadVersion: Int32
    ) {
      __data = InputDict([
        "accountId": accountId,
        "configId": configId,
        "runId": runId,
        "expectedHeadVersion": expectedHeadVersion
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
  }

}