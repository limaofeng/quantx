// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSHistoryTradesQuery: GraphQLQuery {
    static let operationName: String = "IOSHistoryTrades"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSHistoryTrades($accountId: String!, $startDate: String!, $endDate: String!) { historyTrades(accountId: $accountId, startDate: $startDate, endDate: $endDate) { __typename accountId tradedId orderId orderSysid stockCode stockName orderType direction tradedPrice tradedVolume tradedAmount tradedTime strategyName orderRemark } }"#
      ))

    public var accountId: String
    public var startDate: String
    public var endDate: String

    public init(
      accountId: String,
      startDate: String,
      endDate: String
    ) {
      self.accountId = accountId
      self.startDate = startDate
      self.endDate = endDate
    }

    @_spi(Unsafe) public var __variables: Variables? { [
      "accountId": accountId,
      "startDate": startDate,
      "endDate": endDate
    ] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("historyTrades", [HistoryTrade].self, arguments: [
          "accountId": .variable("accountId"),
          "startDate": .variable("startDate"),
          "endDate": .variable("endDate")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSHistoryTradesQuery.Data.self
      ] }

      var historyTrades: [HistoryTrade] { __data["historyTrades"] }

      /// HistoryTrade
      nonisolated struct HistoryTrade: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Trade }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("tradedId", String.self),
          .field("orderId", Int.self),
          .field("orderSysid", String.self),
          .field("stockCode", String.self),
          .field("stockName", String.self),
          .field("orderType", Int.self),
          .field("direction", Int?.self),
          .field("tradedPrice", Double.self),
          .field("tradedVolume", Int.self),
          .field("tradedAmount", Double.self),
          .field("tradedTime", Int.self),
          .field("strategyName", String?.self),
          .field("orderRemark", String?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSHistoryTradesQuery.Data.HistoryTrade.self
        ] }

        var accountId: String { __data["accountId"] }
        var tradedId: String { __data["tradedId"] }
        var orderId: Int { __data["orderId"] }
        var orderSysid: String { __data["orderSysid"] }
        var stockCode: String { __data["stockCode"] }
        var stockName: String { __data["stockName"] }
        var orderType: Int { __data["orderType"] }
        var direction: Int? { __data["direction"] }
        var tradedPrice: Double { __data["tradedPrice"] }
        var tradedVolume: Int { __data["tradedVolume"] }
        var tradedAmount: Double { __data["tradedAmount"] }
        var tradedTime: Int { __data["tradedTime"] }
        var strategyName: String? { __data["strategyName"] }
        var orderRemark: String? { __data["orderRemark"] }
      }
    }
  }

}