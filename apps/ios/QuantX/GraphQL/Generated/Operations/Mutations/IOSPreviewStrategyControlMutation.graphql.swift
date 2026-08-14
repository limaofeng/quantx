// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewStrategyControlMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewStrategyControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewStrategyControl($input: StrategyControlPreviewInput!) { previewStrategyControl(input: $input) { __typename success code message preview { __typename challengeId confirmationToken accountId instanceId targetInstanceId action currentMode currentStatus configVersion readinessStatus snapshotId snapshotAt challengeExpiresAt checks { __typename code passed message } warnings } } }"#
      ))

    public var input: StrategyControlPreviewInput

    public init(input: StrategyControlPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewStrategyControl", PreviewStrategyControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewStrategyControlMutation.Data.self
      ] }

      var previewStrategyControl: PreviewStrategyControl { __data["previewStrategyControl"] }

      /// PreviewStrategyControl
      nonisolated struct PreviewStrategyControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyControlPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewStrategyControlMutation.Data.PreviewStrategyControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewStrategyControl.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyControlPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("accountId", String.self),
            .field("instanceId", String.self),
            .field("targetInstanceId", String.self),
            .field("action", GraphQLEnum<QuantXAPI.StrategyControlAction>.self),
            .field("currentMode", String.self),
            .field("currentStatus", String.self),
            .field("configVersion", String.self),
            .field("readinessStatus", String.self),
            .field("snapshotId", String?.self),
            .field("snapshotAt", QuantXAPI.DateTime?.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("checks", [Check].self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewStrategyControlMutation.Data.PreviewStrategyControl.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var accountId: String { __data["accountId"] }
          var instanceId: String { __data["instanceId"] }
          var targetInstanceId: String { __data["targetInstanceId"] }
          var action: GraphQLEnum<QuantXAPI.StrategyControlAction> { __data["action"] }
          var currentMode: String { __data["currentMode"] }
          var currentStatus: String { __data["currentStatus"] }
          var configVersion: String { __data["configVersion"] }
          var readinessStatus: String { __data["readinessStatus"] }
          var snapshotId: String? { __data["snapshotId"] }
          var snapshotAt: QuantXAPI.DateTime? { __data["snapshotAt"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var checks: [Check] { __data["checks"] }
          var warnings: [String] { __data["warnings"] }

          /// PreviewStrategyControl.Preview.Check
          nonisolated struct Check: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyControlReadinessCheck }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("code", String.self),
              .field("passed", Bool.self),
              .field("message", String.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewStrategyControlMutation.Data.PreviewStrategyControl.Preview.Check.self
            ] }

            var code: String { __data["code"] }
            var passed: Bool { __data["passed"] }
            var message: String { __data["message"] }
          }
        }
      }
    }
  }

}