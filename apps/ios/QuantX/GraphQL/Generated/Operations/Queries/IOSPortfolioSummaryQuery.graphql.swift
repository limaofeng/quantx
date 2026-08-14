// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPortfolioSummaryQuery: GraphQLQuery {
    static let operationName: String = "IOSPortfolioSummary"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSPortfolioSummary($accountId: String) { portfolioSummary(accountId: $accountId) { __typename accountId accountName totalAsset cash totalMarketValue totalProfitLoss totalProfitLossPercent todayProfitLoss todayProfitLossPercent positionCount updateTime } }"#
      ))

    public var accountId: GraphQLNullable<String>

    public init(accountId: GraphQLNullable<String>) {
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["accountId": accountId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("portfolioSummary", PortfolioSummary.self, arguments: ["accountId": .variable("accountId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPortfolioSummaryQuery.Data.self
      ] }

      var portfolioSummary: PortfolioSummary { __data["portfolioSummary"] }

      /// PortfolioSummary
      nonisolated struct PortfolioSummary: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.PortfolioSummary }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("accountName", String.self),
          .field("totalAsset", Double.self),
          .field("cash", Double.self),
          .field("totalMarketValue", Double.self),
          .field("totalProfitLoss", Double.self),
          .field("totalProfitLossPercent", Double.self),
          .field("todayProfitLoss", Double?.self),
          .field("todayProfitLossPercent", Double?.self),
          .field("positionCount", Int.self),
          .field("updateTime", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPortfolioSummaryQuery.Data.PortfolioSummary.self
        ] }

        var accountId: String { __data["accountId"] }
        var accountName: String { __data["accountName"] }
        var totalAsset: Double { __data["totalAsset"] }
        var cash: Double { __data["cash"] }
        var totalMarketValue: Double { __data["totalMarketValue"] }
        var totalProfitLoss: Double { __data["totalProfitLoss"] }
        var totalProfitLossPercent: Double { __data["totalProfitLossPercent"] }
        var todayProfitLoss: Double? { __data["todayProfitLoss"] }
        var todayProfitLossPercent: Double? { __data["todayProfitLossPercent"] }
        var positionCount: Int { __data["positionCount"] }
        var updateTime: QuantXAPI.DateTime { __data["updateTime"] }
      }
    }
  }

}