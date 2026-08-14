// @generated
// This file was automatically generated and should not be edited.

@_spi(Internal) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct RegisterPushDeviceInput: InputObject {
    private(set) var __data: InputDict

    init(_ data: InputDict) {
      __data = data
    }

    init(
      deviceToken: String,
      environment: GraphQLEnum<PushEnvironment>,
      appBundleId: String,
      appVersion: String,
      deviceInstallId: String
    ) {
      __data = InputDict([
        "deviceToken": deviceToken,
        "environment": environment,
        "appBundleId": appBundleId,
        "appVersion": appVersion,
        "deviceInstallId": deviceInstallId
      ])
    }

    var deviceToken: String {
      get { __data["deviceToken"] }
      set { __data["deviceToken"] = newValue }
    }

    var environment: GraphQLEnum<PushEnvironment> {
      get { __data["environment"] }
      set { __data["environment"] = newValue }
    }

    var appBundleId: String {
      get { __data["appBundleId"] }
      set { __data["appBundleId"] = newValue }
    }

    var appVersion: String {
      get { __data["appVersion"] }
      set { __data["appVersion"] = newValue }
    }

    var deviceInstallId: String {
      get { __data["deviceInstallId"] }
      set { __data["deviceInstallId"] = newValue }
    }
  }

}