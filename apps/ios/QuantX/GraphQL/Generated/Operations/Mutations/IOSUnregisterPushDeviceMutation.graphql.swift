// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSUnregisterPushDeviceMutation: GraphQLMutation {
    static let operationName: String = "IOSUnregisterPushDevice"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSUnregisterPushDevice($input: UnregisterPushDeviceInput!) { unregisterPushDevice(input: $input) { __typename success } }"#
      ))

    public var input: UnregisterPushDeviceInput

    public init(input: UnregisterPushDeviceInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("unregisterPushDevice", UnregisterPushDevice.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSUnregisterPushDeviceMutation.Data.self
      ] }

      var unregisterPushDevice: UnregisterPushDevice { __data["unregisterPushDevice"] }

      /// UnregisterPushDevice
      nonisolated struct UnregisterPushDevice: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.UnregisterPushDeviceResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSUnregisterPushDeviceMutation.Data.UnregisterPushDevice.self
        ] }

        var success: Bool { __data["success"] }
      }
    }
  }

}