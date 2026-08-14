// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSResumeStrategyInstanceMutation: GraphQLMutation {
    static let operationName: String = "IOSResumeStrategyInstance"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSResumeStrategyInstance($instanceId: String!) { resumeStrategyInstance(instanceId: $instanceId) { __typename success message } }"#
      ))

    public var instanceId: String

    public init(instanceId: String) {
      self.instanceId = instanceId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["instanceId": instanceId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("resumeStrategyInstance", ResumeStrategyInstance.self, arguments: ["instanceId": .variable("instanceId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSResumeStrategyInstanceMutation.Data.self
      ] }

      var resumeStrategyInstance: ResumeStrategyInstance { __data["resumeStrategyInstance"] }

      /// ResumeStrategyInstance
      nonisolated struct ResumeStrategyInstance: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.OperationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("message", String.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSResumeStrategyInstanceMutation.Data.ResumeStrategyInstance.self
        ] }

        var success: Bool { __data["success"] }
        var message: String { __data["message"] }
      }
    }
  }

}