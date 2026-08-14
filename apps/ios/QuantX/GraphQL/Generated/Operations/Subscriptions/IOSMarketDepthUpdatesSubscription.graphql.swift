// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSMarketDepthUpdatesSubscription: GraphQLSubscription {
    static let operationName: String = "IOSMarketDepthUpdates"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"subscription IOSMarketDepthUpdates($stockList: [String!]!, $levels: Int = 5) { marketDepth(stockList: $stockList, levels: $levels) { __typename stockCode time bids { __typename price volume } asks { __typename price volume } } }"#
      ))

    public var stockList: [String]
    public var levels: GraphQLNullable<Int32>

    public init(
      stockList: [String],
      levels: GraphQLNullable<Int32> = 5
    ) {
      self.stockList = stockList
      self.levels = levels
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "stockList": stockList,
      "levels": levels
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Subscription }
      static var __selections: [ApolloAPI.Selection] { [
        .field("marketDepth", MarketDepth.self, arguments: [
          "stockList": .variable("stockList"),
          "levels": .variable("levels")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSMarketDepthUpdatesSubscription.Data.self
      ] }

      var marketDepth: MarketDepth { __data["marketDepth"] }

      /// MarketDepth
      nonisolated struct MarketDepth: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.MarketDepth }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("stockCode", String.self),
          .field("time", QuantXAPI.DateTime.self),
          .field("bids", [Bid].self),
          .field("asks", [Ask].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSMarketDepthUpdatesSubscription.Data.MarketDepth.self
        ] }

        var stockCode: String { __data["stockCode"] }
        var time: QuantXAPI.DateTime { __data["time"] }
        var bids: [Bid] { __data["bids"] }
        var asks: [Ask] { __data["asks"] }

        /// MarketDepth.Bid
        nonisolated struct Bid: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.MarketDepthLevel }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("price", Double.self),
            .field("volume", Int.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSMarketDepthUpdatesSubscription.Data.MarketDepth.Bid.self
          ] }

          var price: Double { __data["price"] }
          var volume: Int { __data["volume"] }
        }

        /// MarketDepth.Ask
        nonisolated struct Ask: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.MarketDepthLevel }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("price", Double.self),
            .field("volume", Int.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSMarketDepthUpdatesSubscription.Data.MarketDepth.Ask.self
          ] }

          var price: Double { __data["price"] }
          var volume: Int { __data["volume"] }
        }
      }
    }
  }

}