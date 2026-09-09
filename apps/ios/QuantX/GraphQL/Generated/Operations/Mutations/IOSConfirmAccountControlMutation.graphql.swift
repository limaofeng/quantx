// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmAccountControlMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmAccountControl"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmAccountControl($input: AccountExecutionControlConfirmationInput!) { confirmAccountExecutionControl(input: $input) { __typename success code message challengeId action operationStatus safety { __typename accountId stateVersion killSwitch executionWindowActive } } }"#
      ))

    public var input: AccountExecutionControlConfirmationInput

    public init(input: AccountExecutionControlConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmAccountExecutionControl", ConfirmAccountExecutionControl.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmAccountControlMutation.Data.self
      ] }

      var confirmAccountExecutionControl: ConfirmAccountExecutionControl { __data["confirmAccountExecutionControl"] }

      /// ConfirmAccountExecutionControl
      nonisolated struct ConfirmAccountExecutionControl: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionControlConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", QuantXAPI.ID?.self),
          .field("action", GraphQLEnum<QuantXAPI.AccountExecutionControlAction>?.self),
          .field("operationStatus", String.self),
          .field("safety", Safety?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmAccountControlMutation.Data.ConfirmAccountExecutionControl.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: QuantXAPI.ID? { __data["challengeId"] }
        var action: GraphQLEnum<QuantXAPI.AccountExecutionControlAction>? { __data["action"] }
        var operationStatus: String { __data["operationStatus"] }
        var safety: Safety? { __data["safety"] }

        /// ConfirmAccountExecutionControl.Safety
        nonisolated struct Safety: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionSafety }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("accountId", String.self),
            .field("stateVersion", Int.self),
            .field("killSwitch", Bool.self),
            .field("executionWindowActive", Bool.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSConfirmAccountControlMutation.Data.ConfirmAccountExecutionControl.Safety.self
          ] }

          var accountId: String { __data["accountId"] }
          var stateVersion: Int { __data["stateVersion"] }
          var killSwitch: Bool { __data["killSwitch"] }
          var executionWindowActive: Bool { __data["executionWindowActive"] }
        }
      }
    }
  }

}