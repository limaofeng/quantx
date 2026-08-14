// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmLiquidationMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmLiquidation"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmLiquidation($input: LiquidationConfirmationInput!) { confirmLiquidation(input: $input) { __typename success code message challengeId groupId commandId status createdCount failedCount plans { __typename instrumentCode success planId protectedVolume conflictPlanIds error } } }"#
      ))

    public var input: LiquidationConfirmationInput

    public init(input: LiquidationConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmLiquidation", ConfirmLiquidation.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmLiquidationMutation.Data.self
      ] }

      var confirmLiquidation: ConfirmLiquidation { __data["confirmLiquidation"] }

      /// ConfirmLiquidation
      nonisolated struct ConfirmLiquidation: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", String?.self),
          .field("groupId", String?.self),
          .field("commandId", String?.self),
          .field("status", String?.self),
          .field("createdCount", Int.self),
          .field("failedCount", Int.self),
          .field("plans", [Plan].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmLiquidationMutation.Data.ConfirmLiquidation.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
        var groupId: String? { __data["groupId"] }
        var commandId: String? { __data["commandId"] }
        var status: String? { __data["status"] }
        var createdCount: Int { __data["createdCount"] }
        var failedCount: Int { __data["failedCount"] }
        var plans: [Plan] { __data["plans"] }

        /// ConfirmLiquidation.Plan
        nonisolated struct Plan: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationPlanResult }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("instrumentCode", String.self),
            .field("success", Bool.self),
            .field("planId", String?.self),
            .field("protectedVolume", Int?.self),
            .field("conflictPlanIds", [String].self),
            .field("error", String?.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSConfirmLiquidationMutation.Data.ConfirmLiquidation.Plan.self
          ] }

          var instrumentCode: String { __data["instrumentCode"] }
          var success: Bool { __data["success"] }
          var planId: String? { __data["planId"] }
          var protectedVolume: Int? { __data["protectedVolume"] }
          var conflictPlanIds: [String] { __data["conflictPlanIds"] }
          var error: String? { __data["error"] }
        }
      }
    }
  }

}