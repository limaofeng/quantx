// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTAssistantLegacyMaintenanceSourceQuery: GraphQLQuery {
    static let operationName: String = "IOSTAssistantLegacyMaintenanceSource"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTAssistantLegacyMaintenanceSource($accountId: String!) { tAssistantLegacyMaintenanceSource(accountId: $accountId) { __typename accountId configId runId headVersion draining } }"#
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
        .field("tAssistantLegacyMaintenanceSource", TAssistantLegacyMaintenanceSource?.self, arguments: ["accountId": .variable("accountId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTAssistantLegacyMaintenanceSourceQuery.Data.self
      ] }

      var tAssistantLegacyMaintenanceSource: TAssistantLegacyMaintenanceSource? { __data["tAssistantLegacyMaintenanceSource"] }

      /// TAssistantLegacyMaintenanceSource
      nonisolated struct TAssistantLegacyMaintenanceSource: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TAssistantLegacyMaintenanceSource }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("configId", String.self),
          .field("runId", String.self),
          .field("headVersion", Int.self),
          .field("draining", Bool.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTAssistantLegacyMaintenanceSourceQuery.Data.TAssistantLegacyMaintenanceSource.self
        ] }

        var accountId: String { __data["accountId"] }
        var configId: String { __data["configId"] }
        var runId: String { __data["runId"] }
        var headVersion: Int { __data["headVersion"] }
        var draining: Bool { __data["draining"] }
      }
    }
  }

}