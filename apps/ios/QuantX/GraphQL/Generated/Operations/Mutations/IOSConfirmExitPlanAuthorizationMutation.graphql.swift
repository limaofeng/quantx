// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSConfirmExitPlanAuthorizationMutation: GraphQLMutation {
    static let operationName: String = "IOSConfirmExitPlanAuthorization"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSConfirmExitPlanAuthorization($input: ExitPlanAuthorizationConfirmationInput!) { confirmExitPlanAuthorization(input: $input) { __typename success code message challengeId planId configVersion authorized authorizationExpiresAt auditEventId } }"#
      ))

    public var input: ExitPlanAuthorizationConfirmationInput

    public init(input: ExitPlanAuthorizationConfirmationInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("confirmExitPlanAuthorization", ConfirmExitPlanAuthorization.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSConfirmExitPlanAuthorizationMutation.Data.self
      ] }

      var confirmExitPlanAuthorization: ConfirmExitPlanAuthorization { __data["confirmExitPlanAuthorization"] }

      /// ConfirmExitPlanAuthorization
      nonisolated struct ConfirmExitPlanAuthorization: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanAuthorizationConfirmationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("challengeId", String?.self),
          .field("planId", String?.self),
          .field("configVersion", Int?.self),
          .field("authorized", Bool.self),
          .field("authorizationExpiresAt", QuantXAPI.DateTime?.self),
          .field("auditEventId", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSConfirmExitPlanAuthorizationMutation.Data.ConfirmExitPlanAuthorization.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var challengeId: String? { __data["challengeId"] }
        var planId: String? { __data["planId"] }
        var configVersion: Int? { __data["configVersion"] }
        var authorized: Bool { __data["authorized"] }
        var authorizationExpiresAt: QuantXAPI.DateTime? { __data["authorizationExpiresAt"] }
        var auditEventId: String? { __data["auditEventId"] }
      }
    }
  }

}