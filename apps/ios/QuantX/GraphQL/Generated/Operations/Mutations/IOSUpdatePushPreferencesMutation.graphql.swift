// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSUpdatePushPreferencesMutation: GraphQLMutation {
    static let operationName: String = "IOSUpdatePushPreferences"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSUpdatePushPreferences($input: UpdatePushPreferencesInput!) { updatePushPreferences(input: $input) { __typename id deviceInstallId appBundleId appVersion environment registeredAt updatedAt preferences { __typename category enabled } } }"#
      ))

    public var input: UpdatePushPreferencesInput

    public init(input: UpdatePushPreferencesInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("updatePushPreferences", UpdatePushPreferences.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSUpdatePushPreferencesMutation.Data.self
      ] }

      var updatePushPreferences: UpdatePushPreferences { __data["updatePushPreferences"] }

      /// UpdatePushPreferences
      nonisolated struct UpdatePushPreferences: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.PushDeviceRegistration }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", QuantXAPI.ID.self),
          .field("deviceInstallId", String.self),
          .field("appBundleId", String.self),
          .field("appVersion", String.self),
          .field("environment", GraphQLEnum<QuantXAPI.PushEnvironment>.self),
          .field("registeredAt", QuantXAPI.DateTime.self),
          .field("updatedAt", QuantXAPI.DateTime.self),
          .field("preferences", [Preference].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSUpdatePushPreferencesMutation.Data.UpdatePushPreferences.self
        ] }

        var id: QuantXAPI.ID { __data["id"] }
        var deviceInstallId: String { __data["deviceInstallId"] }
        var appBundleId: String { __data["appBundleId"] }
        var appVersion: String { __data["appVersion"] }
        var environment: GraphQLEnum<QuantXAPI.PushEnvironment> { __data["environment"] }
        var registeredAt: QuantXAPI.DateTime { __data["registeredAt"] }
        var updatedAt: QuantXAPI.DateTime { __data["updatedAt"] }
        var preferences: [Preference] { __data["preferences"] }

        /// UpdatePushPreferences.Preference
        nonisolated struct Preference: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.PushCategoryPreference }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("category", GraphQLEnum<QuantXAPI.PushCategory>.self),
            .field("enabled", Bool.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSUpdatePushPreferencesMutation.Data.UpdatePushPreferences.Preference.self
          ] }

          var category: GraphQLEnum<QuantXAPI.PushCategory> { __data["category"] }
          var enabled: Bool { __data["enabled"] }
        }
      }
    }
  }

}