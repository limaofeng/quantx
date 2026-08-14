// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPauseTTradeEntriesMutation: GraphQLMutation {
    static let operationName: String = "IOSPauseTTradeEntries"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"mutation IOSPauseTTradeEntries($accountId: String!, $reason: String!) { pauseTTradeEntries(accountId: $accountId, reason: $reason) { __typename success code message readiness { __typename accountId stage policyVersion snapshotId killSwitch controlledWindowActive controlledWindowSnapshotId checkedAt } } }"#
      ))

    public var accountId: String
    public var reason: String

    public init(
      accountId: String,
      reason: String
    ) {
      self.accountId = accountId
      self.reason = reason
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "accountId": accountId,
      "reason": reason
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Mutation }
      static var __selections: [ApolloAPI.Selection] { [
        .field("pauseTTradeEntries", PauseTTradeEntries.self, arguments: [
          "accountId": .variable("accountId"),
          "reason": .variable("reason")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPauseTTradeEntriesMutation.Data.self
      ] }

      var pauseTTradeEntries: PauseTTradeEntries { __data["pauseTTradeEntries"] }

      /// PauseTTradeEntries
      nonisolated struct PauseTTradeEntries: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeOperationsMutationResult }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("success", Bool.self),
          .field("code", String.self),
          .field("message", String.self),
          .field("readiness", Readiness?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPauseTTradeEntriesMutation.Data.PauseTTradeEntries.self
        ] }

        var success: Bool { __data["success"] }
        var code: String { __data["code"] }
        var message: String { __data["message"] }
        var readiness: Readiness? { __data["readiness"] }

        /// PauseTTradeEntries.Readiness
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
            IOSPauseTTradeEntriesMutation.Data.PauseTTradeEntries.Readiness.self
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