// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSMarketInstrumentDetailQuery: GraphQLQuery {
    static let operationName: String = "IOSMarketInstrumentDetail"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSMarketInstrumentDetail($stockCode: String!, $period: KLinePeriod!) { instrument(stockCode: $stockCode) { __typename id market instrumentId name abbreviation exchangeCode preClose upStopPrice downStopPrice priceTick isTrading quote { __typename stockCode time lastPrice open high low preClose change changePercent volume amount turnoverRate } } klines(stockCode: $stockCode, period: $period, limit: 120, order: "asc") { __typename stockCode period time open high low close preClose volume amount } }"#
      ))

    public var stockCode: String
    public var period: GraphQLEnum<KLinePeriod>

    public init(
      stockCode: String,
      period: GraphQLEnum<KLinePeriod>
    ) {
      self.stockCode = stockCode
      self.period = period
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "stockCode": stockCode,
      "period": period
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("instrument", Instrument?.self, arguments: ["stockCode": .variable("stockCode")]),
        .field("klines", [Kline].self, arguments: [
          "stockCode": .variable("stockCode"),
          "period": .variable("period"),
          "limit": 120,
          "order": "asc"
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSMarketInstrumentDetailQuery.Data.self
      ] }

      var instrument: Instrument? { __data["instrument"] }
      var klines: [Kline] { __data["klines"] }

      /// Instrument
      nonisolated struct Instrument: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Instrument }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("market", String?.self),
          .field("instrumentId", String.self),
          .field("name", String?.self),
          .field("abbreviation", String?.self),
          .field("exchangeCode", String?.self),
          .field("preClose", Double?.self),
          .field("upStopPrice", Double?.self),
          .field("downStopPrice", Double?.self),
          .field("priceTick", Double?.self),
          .field("isTrading", Bool?.self),
          .field("quote", Quote?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSMarketInstrumentDetailQuery.Data.Instrument.self
        ] }

        var id: String { __data["id"] }
        var market: String? { __data["market"] }
        var instrumentId: String { __data["instrumentId"] }
        var name: String? { __data["name"] }
        var abbreviation: String? { __data["abbreviation"] }
        var exchangeCode: String? { __data["exchangeCode"] }
        var preClose: Double? { __data["preClose"] }
        var upStopPrice: Double? { __data["upStopPrice"] }
        var downStopPrice: Double? { __data["downStopPrice"] }
        var priceTick: Double? { __data["priceTick"] }
        var isTrading: Bool? { __data["isTrading"] }
        var quote: Quote? { __data["quote"] }

        /// Instrument.Quote
        nonisolated struct Quote: QuantXAPI.SelectionSet {
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
            IOSMarketInstrumentDetailQuery.Data.Instrument.Quote.self
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

      /// Kline
      nonisolated struct Kline: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.KLineData }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("stockCode", String.self),
          .field("period", String.self),
          .field("time", QuantXAPI.DateTime.self),
          .field("open", Double.self),
          .field("high", Double.self),
          .field("low", Double.self),
          .field("close", Double.self),
          .field("preClose", Double.self),
          .field("volume", Int.self),
          .field("amount", Double.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSMarketInstrumentDetailQuery.Data.Kline.self
        ] }

        var stockCode: String { __data["stockCode"] }
        var period: String { __data["period"] }
        var time: QuantXAPI.DateTime { __data["time"] }
        var open: Double { __data["open"] }
        var high: Double { __data["high"] }
        var low: Double { __data["low"] }
        var close: Double { __data["close"] }
        var preClose: Double { __data["preClose"] }
        var volume: Int { __data["volume"] }
        var amount: Double { __data["amount"] }
      }
    }
  }

}