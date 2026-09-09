// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTAssistantLegacyMaintenanceOperationQuery: GraphQLQuery {
    static let operationName: String = "IOSTAssistantLegacyMaintenanceOperation"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTAssistantLegacyMaintenanceOperation($accountId: String!, $commandId: String!) { tAssistantLegacyMaintenanceOperation( accountId: $accountId commandId: $commandId ) { __typename commandId status evidence } }"#
      ))

    public var accountId: String
    public var commandId: String

    public init(
      accountId: String,
      commandId: String
    ) {
      self.accountId = accountId
      self.commandId = commandId
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "accountId": accountId,
      "commandId": commandId
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("tAssistantLegacyMaintenanceOperation", TAssistantLegacyMaintenanceOperation.self, arguments: [
          "accountId": .variable("accountId"),
          "commandId": .variable("commandId")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTAssistantLegacyMaintenanceOperationQuery.Data.self
      ] }

      var tAssistantLegacyMaintenanceOperation: TAssistantLegacyMaintenanceOperation { __data["tAssistantLegacyMaintenanceOperation"] }

      /// TAssistantLegacyMaintenanceOperation
      nonisolated struct TAssistantLegacyMaintenanceOperation: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyMaintenanceOperation }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("commandId", String.self),
          .field("status", String.self),
          .field("evidence", QuantXAPI.JSON?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTAssistantLegacyMaintenanceOperationQuery.Data.TAssistantLegacyMaintenanceOperation.self
        ] }

        var commandId: String { __data["commandId"] }
        var status: String { __data["status"] }
        var evidence: QuantXAPI.JSON? { __data["evidence"] }
      }
    }
  }

}