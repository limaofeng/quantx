// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewExitPlanAuthorizationMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewExitPlanAuthorization"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewExitPlanAuthorization($input: ExitPlanAuthorizationPreviewInput!) { previewExitPlanAuthorization(input: $input) { __typename success code message preview { __typename challengeId confirmationToken accountId planId instrumentCode bucket sourceType executionMode configVersion protectedVolume exitedVolume remainingVolume rules t1Policy executionPolicy position { __typename totalVolume availableVolume frozenVolume yesterdayVolume t1UnavailableVolume positionUpdatedAt } otherProtections { __typename planId sourceType status remainingVolume configVersion pending } readiness authorizationFingerprint authorizationExpiresAt challengeExpiresAt warnings } } }"#
      ))

    public var input: ExitPlanAuthorizationPreviewInput

    public init(input: ExitPlanAuthorizationPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewExitPlanAuthorization", PreviewExitPlanAuthorization.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewExitPlanAuthorizationMutation.Data.self
      ] }

      var previewExitPlanAuthorization: PreviewExitPlanAuthorization { __data["previewExitPlanAuthorization"] }

      /// PreviewExitPlanAuthorization
      nonisolated struct PreviewExitPlanAuthorization: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanAuthorizationPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewExitPlanAuthorizationMutation.Data.PreviewExitPlanAuthorization.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewExitPlanAuthorization.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanAuthorizationPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("accountId", String.self),
            .field("planId", String.self),
            .field("instrumentCode", String.self),
            .field("bucket", String.self),
            .field("sourceType", String.self),
            .field("executionMode", String.self),
            .field("configVersion", Int.self),
            .field("protectedVolume", Int.self),
            .field("exitedVolume", Int.self),
            .field("remainingVolume", Int.self),
            .field("rules", QuantXAPI.JSON.self),
            .field("t1Policy", String.self),
            .field("executionPolicy", QuantXAPI.JSON.self),
            .field("position", Position.self),
            .field("otherProtections", [OtherProtection].self),
            .field("readiness", QuantXAPI.JSON.self),
            .field("authorizationFingerprint", String.self),
            .field("authorizationExpiresAt", QuantXAPI.DateTime.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewExitPlanAuthorizationMutation.Data.PreviewExitPlanAuthorization.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var accountId: String { __data["accountId"] }
          var planId: String { __data["planId"] }
          var instrumentCode: String { __data["instrumentCode"] }
          var bucket: String { __data["bucket"] }
          var sourceType: String { __data["sourceType"] }
          var executionMode: String { __data["executionMode"] }
          var configVersion: Int { __data["configVersion"] }
          var protectedVolume: Int { __data["protectedVolume"] }
          var exitedVolume: Int { __data["exitedVolume"] }
          var remainingVolume: Int { __data["remainingVolume"] }
          var rules: QuantXAPI.JSON { __data["rules"] }
          var t1Policy: String { __data["t1Policy"] }
          var executionPolicy: QuantXAPI.JSON { __data["executionPolicy"] }
          var position: Position { __data["position"] }
          var otherProtections: [OtherProtection] { __data["otherProtections"] }
          var readiness: QuantXAPI.JSON { __data["readiness"] }
          var authorizationFingerprint: String { __data["authorizationFingerprint"] }
          var authorizationExpiresAt: QuantXAPI.DateTime { __data["authorizationExpiresAt"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var warnings: [String] { __data["warnings"] }

          /// PreviewExitPlanAuthorization.Preview.Position
          nonisolated struct Position: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.ExitPlanAuthorizationPositionSnapshot }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("totalVolume", Int.self),
              .field("availableVolume", Int.self),
              .field("frozenVolume", Int.self),
              .field("yesterdayVolume", Int.self),
              .field("t1UnavailableVolume", Int.self),
              .field("positionUpdatedAt", QuantXAPI.DateTime?.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewExitPlanAuthorizationMutation.Data.PreviewExitPlanAuthorization.Preview.Position.self
            ] }

            var totalVolume: Int { __data["totalVolume"] }
            var availableVolume: Int { __data["availableVolume"] }
            var frozenVolume: Int { __data["frozenVolume"] }
            var yesterdayVolume: Int { __data["yesterdayVolume"] }
            var t1UnavailableVolume: Int { __data["t1UnavailableVolume"] }
            var positionUpdatedAt: QuantXAPI.DateTime? { __data["positionUpdatedAt"] }
          }

          /// PreviewExitPlanAuthorization.Preview.OtherProtection
          nonisolated struct OtherProtection: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationConflictPreview }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("planId", String.self),
              .field("sourceType", String.self),
              .field("status", String.self),
              .field("remainingVolume", Int.self),
              .field("configVersion", Int.self),
              .field("pending", Bool.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewExitPlanAuthorizationMutation.Data.PreviewExitPlanAuthorization.Preview.OtherProtection.self
            ] }

            var planId: String { __data["planId"] }
            var sourceType: String { __data["sourceType"] }
            var status: String { __data["status"] }
            var remainingVolume: Int { __data["remainingVolume"] }
            var configVersion: Int { __data["configVersion"] }
            var pending: Bool { __data["pending"] }
          }
        }
      }
    }
  }

}