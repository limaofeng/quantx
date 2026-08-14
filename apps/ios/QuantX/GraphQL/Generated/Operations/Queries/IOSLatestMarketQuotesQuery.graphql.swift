// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSLatestMarketQuotesQuery: GraphQLQuery {
    static let operationName: String = "IOSLatestMarketQuotes"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSLatestMarketQuotes($stockList: [String!]!) { latestMarketQuotes(stockList: $stockList) { __typename stockCode time lastPrice open high low preClose change changePercent volume amount turnoverRate } }"#
      ))

    public var stockList: [String]

    public init(stockList: [String]) {
      self.stockList = stockList
    }

    @_spi(Unsafe) public var __variables: Variables? { ["stockList": stockList] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("latestMarketQuotes", [LatestMarketQuote].self, arguments: ["stockList": .variable("stockList")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSLatestMarketQuotesQuery.Data.self
      ] }

      var latestMarketQuotes: [LatestMarketQuote] { __data["latestMarketQuotes"] }

      /// LatestMarketQuote
      nonisolated struct LatestMarketQuote: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StockQuote }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("stockCode", String.self),
          .field("time", QuantXAPI.DateTime.self),
          .field("lastPrice", Double.self),
          .field("open", Double.self),
          .field("high", Double.self),
          .field("low", Double.self),
          .field("preClose", Double.self),
          .field("change", Double?.self),
          .field("changePercent", Double?.self),
          .field("volume", Double.self),
          .field("amount", Double.self),
          .field("turnoverRate", Double?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSLatestMarketQuotesQuery.Data.LatestMarketQuote.self
        ] }

        var stockCode: String { __data["stockCode"] }
        var time: QuantXAPI.DateTime { __data["time"] }
        var lastPrice: Double { __data["lastPrice"] }
        var open: Double { __data["open"] }
        var high: Double { __data["high"] }
        var low: Double { __data["low"] }
        var preClose: Double { __data["preClose"] }
        var change: Double? { __data["change"] }
        var changePercent: Double? { __data["changePercent"] }
        var volume: Double { __data["volume"] }
        var amount: Double { __data["amount"] }
        var turnoverRate: Double? { __data["turnoverRate"] }
      }
    }
  }

}