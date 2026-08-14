// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmTTradeControlMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmTTradeControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmTTradeControl($input: TTradeControlConfirmationInput!) { confirmTTradeControl(input: $input) { __typename success code message challengeId accountId action challengeConsumed operationStatus readiness { __typename accountId stage policyVersion snapshotId killSwitch controlledWindowActive controlledWindowSnapshotId checkedAt } } }"#
      ))

    public var input: TTradeControlConfirmationInput

    public init(input: TTradeControlConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmTTradeControl", ConfirmTTradeControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmTTradeControlMutation.Data.self
      ] }

      var confirmTTradeControl: ConfirmTTradeControl { __data["confirmTTradeControl"] }

      /// ConfirmTTradeControl
      nonisolated struct ConfirmTTradeControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeControlConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", QuantXAPI.ID?.self),
          .field("accountId", String?.self),
          .field("action", GraphQLEnum<QuantXAPI.TTradeControlAction>?.self),
          .field("challengeConsumed", Bool.self),
          .field("operationStatus", String.self),
          .field("readiness", Readiness?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmTTradeControlMutation.Data.ConfirmTTradeControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: QuantXAPI.ID? { __data["challengeId"] }
        var accountId: String? { __data["accountId"] }
        var action: GraphQLEnum<QuantXAPI.TTradeControlAction>? { __data["action"] }
        var challengeConsumed: Bool { __data["challengeConsumed"] }
        var operationStatus: String { __data["operationStatus"] }
        var readiness: Readiness? { __data["readiness"] }

        /// ConfirmTTradeControl.Readiness
        nonisolated struct Readiness: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeLiveReadiness }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("accountId", String.self),
            .field("stage", String.self),
            .field("policyVersion", Int.self),
            .field("snapshotId", String?.self),
            .field("killSwitch", Bool.self),
            .field("controlledWindowActive", Bool.self),
            .field("controlledWindowSnapshotId", String?.self),
            .field("checkedAt", QuantXAPI.DateTime.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSConfirmTTradeControlMutation.Data.ConfirmTTradeControl.Readiness.self
          ] }

          var accountId: String { __data["accountId"] }
          var stage: String { __data["stage"] }
          var policyVersion: Int { __data["policyVersion"] }
          var snapshotId: String? { __data["snapshotId"] }
          var killSwitch: Bool { __data["killSwitch"] }
          var controlledWindowActive: Bool { __data["controlledWindowActive"] }
          var controlledWindowSnapshotId: String? { __data["controlledWindowSnapshotId"] }
          var checkedAt: QuantXAPI.DateTime { __data["checkedAt"] }
        }
      }
    }
  }

}