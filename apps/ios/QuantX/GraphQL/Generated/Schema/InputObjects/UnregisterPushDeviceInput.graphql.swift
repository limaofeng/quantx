// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct UnregisterPushDeviceInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      environment: GraphQLEnum<PushEnvironment>,
      appBundleId: String,
      deviceInstallId: String
    ) {
      __data = InputDict([
        "environment": environment,
        "appBundleId": appBundleId,
        "deviceInstallId": deviceInstallId
      ])
    }

    var environment: GraphQLEnum<PushEnvironment> {
      get { __data["environment"] }
      set { __data["environment"] = newValue }
    }

    var appBundleId: String {
      get { __data["appBundleId"] }
      set { __data["appBundleId"] = newValue }
    }

    var deviceInstallId: String {
      get { __data["deviceInstallId"] }
      set { __data["deviceInstallId"] = newValue }
    }
  }

}