// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSHistoryOrdersQuery: GraphQLQuery {
    static let operationName: String = "IOSHistoryOrders"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSHistoryOrders($accountId: String!, $startDate: String!, $endDate: String!) { historyOrders(accountId: $accountId, startDate: $startDate, endDate: $endDate) { __typename id sysid stockCode stockName type status statusMsg price volume tradedVolume tradedPrice strategyName orderRemark time } }"#
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
        .field("historyOrders", [HistoryOrder].self, arguments: [
          "accountId": .variable("accountId"),
          "startDate": .variable("startDate"),
          "endDate": .variable("endDate")
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSHistoryOrdersQuery.Data.self
      ] }

      var historyOrders: [HistoryOrder] { __data["historyOrders"] }

      /// HistoryOrder
      nonisolated struct HistoryOrder: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Order }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("sysid", String.self),
          .field("stockCode", String.self),
          .field("stockName", String.self),
          .field("type", GraphQLEnum<QuantXAPI.OrderType>.self),
          .field("status", GraphQLEnum<QuantXAPI.OrderStatus>.self),
          .field("statusMsg", String?.self),
          .field("price", Double.self),
          .field("volume", Int.self),
          .field("tradedVolume", Int.self),
          .field("tradedPrice", Double.self),
          .field("strategyName", String?.self),
          .field("orderRemark", String?.self),
          .field("time", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSHistoryOrdersQuery.Data.HistoryOrder.self
        ] }

        var id: String { __data["id"] }
        var sysid: String { __data["sysid"] }
        var stockCode: String { __data["stockCode"] }
        var stockName: String { __data["stockName"] }
        var type: GraphQLEnum<QuantXAPI.OrderType> { __data["type"] }
        var status: GraphQLEnum<QuantXAPI.OrderStatus> { __data["status"] }
        var statusMsg: String? { __data["statusMsg"] }
        var price: Double { __data["price"] }
        var volume: Int { __data["volume"] }
        var tradedVolume: Int { __data["tradedVolume"] }
        var tradedPrice: Double { __data["tradedPrice"] }
        var strategyName: String? { __data["strategyName"] }
        var orderRemark: String? { __data["orderRemark"] }
        var time: QuantXAPI.DateTime { __data["time"] }
      }
    }
  }

}