// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSMarketQuoteUpdatesSubscription: GraphQLSubscription {
    static let operationName: String = "IOSMarketQuoteUpdates"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"subscription IOSMarketQuoteUpdates($stockList: [String!]!) { marketQuotes(stockList: $stockList) { __typename stockCode currentPrice change changePercent volume amount time bidPrice askPrice bidVolume askVolume high low open preClose } }"#
      ))

    public var stockList: [String]

    public init(stockList: [String]) {
      self.stockList = stockList
    }

    @_spi(Unsafe) public var __variables: Variables? { ["stockList": stockList] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Subscription }
      static var __selections: [ApolloAPI.Selection] { [
        .field("marketQuotes", MarketQuotes.self, arguments: ["stockList": .variable("stockList")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSMarketQuoteUpdatesSubscription.Data.self
      ] }

      var marketQuotes: MarketQuotes { __data["marketQuotes"] }

      /// MarketQuotes
      nonisolated struct MarketQuotes: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.RealTimePrice }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("stockCode", String.self),
          .field("currentPrice", Double.self),
          .field("change", Double?.self),
          .field("changePercent", Double?.self),
          .field("volume", Double.self),
          .field("amount", Double.self),
          .field("time", QuantXAPI.DateTime.self),
          .field("bidPrice", Double.self),
          .field("askPrice", Double.self),
          .field("bidVolume", Int.self),
          .field("askVolume", Int.self),
          .field("high", Double.self),
          .field("low", Double.self),
          .field("open", Double.self),
          .field("preClose", Double?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSMarketQuoteUpdatesSubscription.Data.MarketQuotes.self
        ] }

        var stockCode: String { __data["stockCode"] }
        var currentPrice: Double { __data["currentPrice"] }
        var change: Double? { __data["change"] }
        var changePercent: Double? { __data["changePercent"] }
        var volume: Double { __data["volume"] }
        var amount: Double { __data["amount"] }
        var time: QuantXAPI.DateTime { __data["time"] }
        var bidPrice: Double { __data["bidPrice"] }
        var askPrice: Double { __data["askPrice"] }
        var bidVolume: Int { __data["bidVolume"] }
        var askVolume: Int { __data["askVolume"] }
        var high: Double { __data["high"] }
        var low: Double { __data["low"] }
        var open: Double { __data["open"] }
        var preClose: Double? { __data["preClose"] }
      }
    }
  }

}