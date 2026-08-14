// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSPositionsQuery: GraphQLQuery {
    static let operationName: String = "IOSPositions"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSPositions { positions { __typename id accountId stockCode instrumentName volume canUseVolume avgPrice lastPrice marketValue marketValuePercent profitLoss profitRate updatedAt } }"#
      ))

    public init() {}

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("positions", [Position].self),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSPositionsQuery.Data.self
      ] }

      var positions: [Position] { __data["positions"] }

      /// Position
      nonisolated struct Position: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Position }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("accountId", String.self),
          .field("stockCode", String.self),
          .field("instrumentName", String?.self),
          .field("volume", Int.self),
          .field("canUseVolume", Int.self),
          .field("avgPrice", Double?.self),
          .field("lastPrice", Double?.self),
          .field("marketValue", Double?.self),
          .field("marketValuePercent", Double?.self),
          .field("profitLoss", Double?.self),
          .field("profitRate", Double?.self),
          .field("updatedAt", QuantXAPI.DateTime?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSPositionsQuery.Data.Position.self
        ] }

        var id: String { __data["id"] }
        var accountId: String { __data["accountId"] }
        var stockCode: String { __data["stockCode"] }
        var instrumentName: String? { __data["instrumentName"] }
        var volume: Int { __data["volume"] }
        var canUseVolume: Int { __data["canUseVolume"] }
        var avgPrice: Double? { __data["avgPrice"] }
        var lastPrice: Double? { __data["lastPrice"] }
        var marketValue: Double? { __data["marketValue"] }
        var marketValuePercent: Double? { __data["marketValuePercent"] }
        var profitLoss: Double? { __data["profitLoss"] }
        var profitRate: Double? { __data["profitRate"] }
        var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
      }
    }
  }

}