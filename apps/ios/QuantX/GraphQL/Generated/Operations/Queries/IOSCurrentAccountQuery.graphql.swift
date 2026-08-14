// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSCurrentAccountQuery: GraphQLQuery {
    static let operationName: String = "IOSCurrentAccount"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSCurrentAccount { currentAccount { __typename id accountName accountType totalAsset cash frozenCash marketValue totalProfitLoss profitLossPercent updateTime } }"#
      ))

    public init() {}

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("currentAccount", CurrentAccount?.self),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSCurrentAccountQuery.Data.self
      ] }

      var currentAccount: CurrentAccount? { __data["currentAccount"] }

      /// CurrentAccount
      nonisolated struct CurrentAccount: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Account }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("accountName", String.self),
          .field("accountType", String.self),
          .field("totalAsset", Double.self),
          .field("cash", Double.self),
          .field("frozenCash", Double.self),
          .field("marketValue", Double.self),
          .field("totalProfitLoss", Double?.self),
          .field("profitLossPercent", Double?.self),
          .field("updateTime", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSCurrentAccountQuery.Data.CurrentAccount.self
        ] }

        var id: String { __data["id"] }
        var accountName: String { __data["accountName"] }
        var accountType: String { __data["accountType"] }
        var totalAsset: Double { __data["totalAsset"] }
        var cash: Double { __data["cash"] }
        var frozenCash: Double { __data["frozenCash"] }
        var marketValue: Double { __data["marketValue"] }
        var totalProfitLoss: Double? { __data["totalProfitLoss"] }
        var profitLossPercent: Double? { __data["profitLossPercent"] }
        var updateTime: QuantXAPI.DateTime { __data["updateTime"] }
      }
    }
  }

}