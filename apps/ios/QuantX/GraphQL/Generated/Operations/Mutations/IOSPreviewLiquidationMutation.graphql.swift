// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPreviewLiquidationMutation: GraphQLMutation {
    static let operationName: String = "IOSPreviewLiquidation"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPreviewLiquidation($input: LiquidationPreviewInput!) { previewLiquidation(input: $input) { __typename success code message preview { __typename challengeId confirmationToken groupId accountId scope instrumentCodes completionStrategy conflictStrategy executionMode idempotencyKey snapshotVersion accountUpdatedAt rolloutSnapshotId rolloutSnapshotHash challengeExpiresAt includedCount skippedCount items { __typename instrumentCode instrumentName totalVolume availableVolume frozenVolume t1UnavailableVolume protectedVolume pendingSellVolume maxProtectedVolume included reasonCode reasonDetail positionUpdatedAt conflicts { __typename planId sourceType status remainingVolume configVersion pending } } warnings } } }"#
      ))

    public var input: LiquidationPreviewInput

    public init(input: LiquidationPreviewInput) {
      self.input = input
    }

    @_spi(Unsafe) public var __variables: Variables? { ["input": input] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("previewLiquidation", PreviewLiquidation.self, arguments: ["input": .variable("input")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPreviewLiquidationMutation.Data.self
      ] }

      var previewLiquidation: PreviewLiquidation { __data["previewLiquidation"] }

      /// PreviewLiquidation
      nonisolated struct PreviewLiquidation: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationPreviewResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("preview", Preview?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPreviewLiquidationMutation.Data.PreviewLiquidation.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var preview: Preview? { __data["preview"] }

        /// PreviewLiquidation.Preview
        nonisolated struct Preview: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationPreview }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("challengeId", String.self),
            .field("confirmationToken", String.self),
            .field("groupId", String.self),
            .field("accountId", String.self),
            .field("scope", GraphQLEnum<QuantXAPI.LiquidationScope>.self),
            .field("instrumentCodes", [String].self),
            .field("completionStrategy", GraphQLEnum<QuantXAPI.LiquidationCompletionStrategy>.self),
            .field("conflictStrategy", GraphQLEnum<QuantXAPI.LiquidationConflictStrategy>.self),
            .field("executionMode", GraphQLEnum<QuantXAPI.LiquidationExecutionMode>.self),
            .field("idempotencyKey", String.self),
            .field("snapshotVersion", String.self),
            .field("accountUpdatedAt", QuantXAPI.DateTime.self),
            .field("rolloutSnapshotId", String?.self),
            .field("rolloutSnapshotHash", String?.self),
            .field("challengeExpiresAt", QuantXAPI.DateTime.self),
            .field("includedCount", Int.self),
            .field("skippedCount", Int.self),
            .field("items", [Item].self),
            .field("warnings", [String].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSPreviewLiquidationMutation.Data.PreviewLiquidation.Preview.self
          ] }

          var challengeId: String { __data["challengeId"] }
          var confirmationToken: String { __data["confirmationToken"] }
          var groupId: String { __data["groupId"] }
          var accountId: String { __data["accountId"] }
          var scope: GraphQLEnum<QuantXAPI.LiquidationScope> { __data["scope"] }
          var instrumentCodes: [String] { __data["instrumentCodes"] }
          var completionStrategy: GraphQLEnum<QuantXAPI.LiquidationCompletionStrategy> { __data["completionStrategy"] }
          var conflictStrategy: GraphQLEnum<QuantXAPI.LiquidationConflictStrategy> { __data["conflictStrategy"] }
          var executionMode: GraphQLEnum<QuantXAPI.LiquidationExecutionMode> { __data["executionMode"] }
          var idempotencyKey: String { __data["idempotencyKey"] }
          var snapshotVersion: String { __data["snapshotVersion"] }
          var accountUpdatedAt: QuantXAPI.DateTime { __data["accountUpdatedAt"] }
          var rolloutSnapshotId: String? { __data["rolloutSnapshotId"] }
          var rolloutSnapshotHash: String? { __data["rolloutSnapshotHash"] }
          var challengeExpiresAt: QuantXAPI.DateTime { __data["challengeExpiresAt"] }
          var includedCount: Int { __data["includedCount"] }
          var skippedCount: Int { __data["skippedCount"] }
          var items: [Item] { __data["items"] }
          var warnings: [String] { __data["warnings"] }

          /// PreviewLiquidation.Preview.Item
          nonisolated struct Item: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.LiquidationItemPreview }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("instrumentCode", String.self),
              .field("instrumentName", String?.self),
              .field("totalVolume", Int.self),
              .field("availableVolume", Int.self),
              .field("frozenVolume", Int.self),
              .field("t1UnavailableVolume", Int.self),
              .field("protectedVolume", Int.self),
              .field("pendingSellVolume", Int.self),
              .field("maxProtectedVolume", Int.self),
              .field("included", Bool.self),
              .field("reasonCode", String.self),
              .field("reasonDetail", String.self),
              .field("positionUpdatedAt", QuantXAPI.DateTime?.self),
              .field("conflicts", [Conflict].self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSPreviewLiquidationMutation.Data.PreviewLiquidation.Preview.Item.self
            ] }

            var instrumentCode: String { __data["instrumentCode"] }
            var instrumentName: String? { __data["instrumentName"] }
            var totalVolume: Int { __data["totalVolume"] }
            var availableVolume: Int { __data["availableVolume"] }
            var frozenVolume: Int { __data["frozenVolume"] }
            var t1UnavailableVolume: Int { __data["t1UnavailableVolume"] }
            var protectedVolume: Int { __data["protectedVolume"] }
            var pendingSellVolume: Int { __data["pendingSellVolume"] }
            var maxProtectedVolume: Int { __data["maxProtectedVolume"] }
            var included: Bool { __data["included"] }
            var reasonCode: String { __data["reasonCode"] }
            var reasonDetail: String { __data["reasonDetail"] }
            var positionUpdatedAt: QuantXAPI.DateTime? { __data["positionUpdatedAt"] }
            var conflicts: [Conflict] { __data["conflicts"] }

            /// PreviewLiquidation.Preview.Item.Conflict
            nonisolated struct Conflict: QuantXAPI.SelectionSet {
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
                IOSPreviewLiquidationMutation.Data.PreviewLiquidation.Preview.Item.Conflict.self
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

}