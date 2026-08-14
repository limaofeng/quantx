// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewTTradeControlMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewTTradeControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewTTradeControl($input: TTradeControlPreviewInput!) { previewTTradeControl(input: $input) { __typename success code message preview { __typename challengeId confirmationToken tokenIssued accountId action policyVersion snapshotId targetStage reason currentStage readinessStatus readinessFingerprint challengeExpiresAt challengeStatus operationStatus checks { __typename code passed message scope } warnings } } }"#
      ))

    public var input: TTradeControlPreviewInput

    public init(input: TTradeControlPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewTTradeControl", PreviewTTradeControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewTTradeControlMutation.Data.self
      ] }

      var previewTTradeControl: PreviewTTradeControl { __data["previewTTradeControl"] }

      /// PreviewTTradeControl
      nonisolated struct PreviewTTradeControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeControlPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewTTradeControlMutation.Data.PreviewTTradeControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewTTradeControl.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeControlPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", QuantXAPI.ID.self),
            .field("confirmationToken", String?.self),
            .field("tokenIssued", Bool.self),
            .field("accountId", String.self),
            .field("action", GraphQLEnum<QuantXAPI.TTradeControlAction>.self),
            .field("policyVersion", Int.self),
            .field("snapshotId", String.self),
            .field("targetStage", GraphQLEnum<QuantXAPI.TTradeRolloutTarget>?.self),
            .field("reason", String.self),
            .field("currentStage", String.self),
            .field("readinessStatus", String.self),
            .field("readinessFingerprint", String.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("challengeStatus", String.self),
            .field("operationStatus", String.self),
            .field("checks", [Check].self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewTTradeControlMutation.Data.PreviewTTradeControl.Preview.self
          ] }

          var challengeId: QuantXAPI.ID { __data["challengeId"] }
          var confirmationToken: String? { __data["confirmationToken"] }
          var tokenIssued: Bool { __data["tokenIssued"] }
          var accountId: String { __data["accountId"] }
          var action: GraphQLEnum<QuantXAPI.TTradeControlAction> { __data["action"] }
          var policyVersion: Int { __data["policyVersion"] }
          var snapshotId: String { __data["snapshotId"] }
          var targetStage: GraphQLEnum<QuantXAPI.TTradeRolloutTarget>? { __data["targetStage"] }
          var reason: String { __data["reason"] }
          var currentStage: String { __data["currentStage"] }
          var readinessStatus: String { __data["readinessStatus"] }
          var readinessFingerprint: String { __data["readinessFingerprint"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var challengeStatus: String { __data["challengeStatus"] }
          var operationStatus: String { __data["operationStatus"] }
          var checks: [Check] { __data["checks"] }
          var warnings: [String] { __data["warnings"] }

          /// PreviewTTradeControl.Preview.Check
          nonisolated struct Check: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeReadinessCheck }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("code", String.self),
              .field("passed", Bool.self),
              .field("message", String.self),
              .field("scope", String.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewTTradeControlMutation.Data.PreviewTTradeControl.Preview.Check.self
            ] }

            var code: String { __data["code"] }
            var passed: Bool { __data["passed"] }
            var message: String { __data["message"] }
            var scope: String { __data["scope"] }
          }
        }
      }
    }
  }

}