// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewAccountControlMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewAccountControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewAccountControl($input: AccountExecutionControlPreviewInput!) { previewAccountExecutionControl(input: $input) { __typename success code message preview { __typename challengeId confirmationToken tokenIssued accountId action stateVersion snapshotId reason challengeExpiresAt challengeStatus operationStatus safety { __typename accountId stateVersion summary blockedReasons killSwitch executionWindowActive } } } }"#
      ))

    public var input: AccountExecutionControlPreviewInput

    public init(input: AccountExecutionControlPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewAccountExecutionControl", PreviewAccountExecutionControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewAccountControlMutation.Data.self
      ] }

      var previewAccountExecutionControl: PreviewAccountExecutionControl { __data["previewAccountExecutionControl"] }

      /// PreviewAccountExecutionControl
      nonisolated struct PreviewAccountExecutionControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionControlPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewAccountControlMutation.Data.PreviewAccountExecutionControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewAccountExecutionControl.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionControlPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", QuantXAPI.ID.self),
            .field("confirmationToken", String?.self),
            .field("tokenIssued", Bool.self),
            .field("accountId", String.self),
            .field("action", GraphQLEnum<QuantXAPI.AccountExecutionControlAction>.self),
            .field("stateVersion", Int.self),
            .field("snapshotId", String.self),
            .field("reason", String.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("challengeStatus", String.self),
            .field("operationStatus", String.self),
            .field("safety", Safety.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewAccountControlMutation.Data.PreviewAccountExecutionControl.Preview.self
          ] }

          var challengeId: QuantXAPI.ID { __data["challengeId"] }
          var confirmationToken: String? { __data["confirmationToken"] }
          var tokenIssued: Bool { __data["tokenIssued"] }
          var accountId: String { __data["accountId"] }
          var action: GraphQLEnum<QuantXAPI.AccountExecutionControlAction> { __data["action"] }
          var stateVersion: Int { __data["stateVersion"] }
          var snapshotId: String { __data["snapshotId"] }
          var reason: String { __data["reason"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var challengeStatus: String { __data["challengeStatus"] }
          var operationStatus: String { __data["operationStatus"] }
          var safety: Safety { __data["safety"] }

          /// PreviewAccountExecutionControl.Preview.Safety
          nonisolated struct Safety: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionSafety }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("accountId", String.self),
              .field("stateVersion", Int.self),
              .field("summary", String.self),
              .field("blockedReasons", [String].self),
              .field("killSwitch", Bool.self),
              .field("executionWindowActive", Bool.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewAccountControlMutation.Data.PreviewAccountExecutionControl.Preview.Safety.self
            ] }

            var accountId: String { __data["accountId"] }
            var stateVersion: Int { __data["stateVersion"] }
            var summary: String { __data["summary"] }
            var blockedReasons: [String] { __data["blockedReasons"] }
            var killSwitch: Bool { __data["killSwitch"] }
            var executionWindowActive: Bool { __data["executionWindowActive"] }
          }
        }
      }
    }
  }

}