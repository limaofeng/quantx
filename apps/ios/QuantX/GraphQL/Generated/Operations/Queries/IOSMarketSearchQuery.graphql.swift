// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSMarketSearchQuery: GraphQLQuery {
    static let operationName: String = "IOSMarketSearch"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSMarketSearch($term: String!) { byCode: instruments( limit: 20 where: { type: STOCK, stockCode_contains: $term } orderBy: { field: CODE, direction: ASC } ) { __typename id market instrumentId name abbreviation exchangeCode preClose upStopPrice downStopPrice priceTick isTrading quote { __typename stockCode time lastPrice open high low preClose change changePercent volume amount turnoverRate } } byName: instruments( limit: 20 where: { type: STOCK, name_contains: $term } orderBy: { field: CODE, direction: ASC } ) { __typename id market instrumentId name abbreviation exchangeCode preClose upStopPrice downStopPrice priceTick isTrading quote { __typename stockCode time lastPrice open high low preClose change changePercent volume amount turnoverRate } } }"#
      ))

    public var term: String

    public init(term: String) {
      self.term = term
    }

    @_spi(Unsafe) public var __variables: Variables? { ["term": term] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("instruments", alias: "byCode", [ByCode].self, arguments: [
          "limit": 20,
          "where": [
            "type": "STOCK",
            "stockCode_contains": .variable("term")
          ],
          "orderBy": [
            "field": "CODE",
            "direction": "ASC"
          ]
        ]),
        .field("instruments", alias: "byName", [ByName].self, arguments: [
          "limit": 20,
          "where": [
            "type": "STOCK",
            "name_contains": .variable("term")
          ],
          "orderBy": [
            "field": "CODE",
            "direction": "ASC"
          ]
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSMarketSearchQuery.Data.self
      ] }

      var byCode: [ByCode] { __data["byCode"] }
      var byName: [ByName] { __data["byName"] }

      /// ByCode
      nonisolated struct ByCode: QuantXAPI.SelectionSet {
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
          IOSMarketSearchQuery.Data.ByCode.self
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

        /// ByCode.Quote
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
            IOSMarketSearchQuery.Data.ByCode.Quote.self
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

      /// ByName
      nonisolated struct ByName: QuantXAPI.SelectionSet {
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
          IOSMarketSearchQuery.Data.ByName.self
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

        /// ByName.Quote
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
            IOSMarketSearchQuery.Data.ByName.Quote.self
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

}