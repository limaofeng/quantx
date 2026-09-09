// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSAccountControlSafetyQuery: GraphQLQuery {
    static let operationName: String = "IOSAccountControlSafety"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSAccountControlSafety($accountId: String!) { accountExecutionSafety(accountId: $accountId) { __typename accountId stateVersion snapshotId } }"#
      ))

    public var accountId: String

    public init(accountId: String) {
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["accountId": accountId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("accountExecutionSafety", AccountExecutionSafety.self, arguments: ["accountId": .variable("accountId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSAccountControlSafetyQuery.Data.self
      ] }

      var accountExecutionSafety: AccountExecutionSafety { __data["accountExecutionSafety"] }

      /// AccountExecutionSafety
      nonisolated struct AccountExecutionSafety: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.AccountExecutionSafety }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("stateVersion", Int.self),
          .field("snapshotId", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSAccountControlSafetyQuery.Data.AccountExecutionSafety.self
        ] }

        var accountId: String { __data["accountId"] }
        var stateVersion: Int { __data["stateVersion"] }
        var snapshotId: String? { __data["snapshotId"] }
      }
    }
  }

}