// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSOrderEntryCapabilitiesQuery: GraphQLQuery {
    static let operationName: String = "IOSOrderEntryCapabilities"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSOrderEntryCapabilities($instrumentCode: String!, $accountId: String!) { orderEntryCapabilities(instrumentCode: $instrumentCode, accountId: $accountId) { __typename accountId instrumentCode canManualTrade defaultExecutionMode executionModes supportedSides supportedPriceTypes liveReady liveBlockedReasons warnings } }"#
      ))

    public var instrumentCode: String
    public var accountId: String

    public init(
      instrumentCode: String,
      accountId: String
    ) {
      self.instrumentCode = instrumentCode
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "instrumentCode": instrumentCode,
      "accountId": accountId
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("orderEntryCapabilities", OrderEntryCapabilities.self, arguments: [
          "instrumentCode": .variable("instrumentCode"),
          "accountId": .variable("accountId")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSOrderEntryCapabilitiesQuery.Data.self
      ] }

      var orderEntryCapabilities: OrderEntryCapabilities { __data["orderEntryCapabilities"] }

      /// OrderEntryCapabilities
      nonisolated struct OrderEntryCapabilities: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.OrderEntryCapabilities }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("instrumentCode", String.self),
          .field("canManualTrade", Bool.self),
          .field("defaultExecutionMode", GraphQLEnum<QuantXAPI.ManualOrderExecutionMode>.self),
          .field("executionModes", [GraphQLEnum<QuantXAPI.ManualOrderExecutionMode>].self),
          .field("supportedSides", [GraphQLEnum<QuantXAPI.ManualOrderSide>].self),
          .field("supportedPriceTypes", [GraphQLEnum<QuantXAPI.ManualOrderPriceType>].self),
          .field("liveReady", Bool.self),
          .field("liveBlockedReasons", [String].self),
          .field("warnings", [String].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSOrderEntryCapabilitiesQuery.Data.OrderEntryCapabilities.self
        ] }

        var accountId: String { __data["accountId"] }
        var instrumentCode: String { __data["instrumentCode"] }
        var canManualTrade: Bool { __data["canManualTrade"] }
        var defaultExecutionMode: GraphQLEnum<QuantXAPI.ManualOrderExecutionMode> { __data["defaultExecutionMode"] }
        var executionModes: [GraphQLEnum<QuantXAPI.ManualOrderExecutionMode>] { __data["executionModes"] }
        var supportedSides: [GraphQLEnum<QuantXAPI.ManualOrderSide>] { __data["supportedSides"] }
        var supportedPriceTypes: [GraphQLEnum<QuantXAPI.ManualOrderPriceType>] { __data["supportedPriceTypes"] }
        var liveReady: Bool { __data["liveReady"] }
        var liveBlockedReasons: [String] { __data["liveBlockedReasons"] }
        var warnings: [String] { __data["warnings"] }
      }
    }
  }

}