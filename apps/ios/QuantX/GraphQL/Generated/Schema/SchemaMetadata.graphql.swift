// @generated
// This file was automatically generated and should not be edited.

import ApolloAPI

nonisolated protocol QuantXAPI_SelectionSet: ApolloAPI.SelectionSet & ApolloAPI.RootSelectionSet
where Schema == QuantXAPI.SchemaMetadata {}

nonisolated protocol QuantXAPI_InlineFragment: ApolloAPI.SelectionSet & ApolloAPI.InlineFragment
where Schema == QuantXAPI.SchemaMetadata {}

nonisolated protocol QuantXAPI_MutableSelectionSet: ApolloAPI.MutableRootSelectionSet
where Schema == QuantXAPI.SchemaMetadata {}

nonisolated protocol QuantXAPI_MutableInlineFragment: ApolloAPI.MutableSelectionSet & ApolloAPI.InlineFragment
where Schema == QuantXAPI.SchemaMetadata {}

extension QuantXAPI {
  typealias SelectionSet = QuantXAPI_SelectionSet

  typealias InlineFragment = QuantXAPI_InlineFragment

  typealias MutableSelectionSet = QuantXAPI_MutableSelectionSet

  typealias MutableInlineFragment = QuantXAPI_MutableInlineFragment

  nonisolated enum SchemaMetadata: ApolloAPI.SchemaMetadata {
    static let configuration: any ApolloAPI.SchemaConfiguration.Type = SchemaConfiguration.self

    private static let objectTypeMap: [String: ApolloAPI.Object] = [
      "Account": QuantXAPI.Objects.Account,
      "Instrument": QuantXAPI.Objects.Instrument,
      "KLineData": QuantXAPI.Objects.KLineData,
      "MarketDepth": QuantXAPI.Objects.MarketDepth,
      "MarketDepthLevel": QuantXAPI.Objects.MarketDepthLevel,
      "Mutation": QuantXAPI.Objects.Mutation,
      "Order": QuantXAPI.Objects.Order,
      "PageInfo": QuantXAPI.Objects.PageInfo,
      "PortfolioSummary": QuantXAPI.Objects.PortfolioSummary,
      "Position": QuantXAPI.Objects.Position,
      "Query": QuantXAPI.Objects.Query,
      "RealTimePrice": QuantXAPI.Objects.RealTimePrice,
      "StockQuote": QuantXAPI.Objects.StockQuote,
      "StrategyApprovalIntent": QuantXAPI.Objects.StrategyApprovalIntent,
      "StrategyExitPlanView": QuantXAPI.Objects.StrategyExitPlanView,
      "StrategyInstance": QuantXAPI.Objects.StrategyInstance,
      "Subscription": QuantXAPI.Objects.Subscription,
      "TTradeBatch": QuantXAPI.Objects.TTradeBatch,
      "TTradeBatchPage": QuantXAPI.Objects.TTradeBatchPage,
      "TTradeGlobalHolding": QuantXAPI.Objects.TTradeGlobalHolding,
      "TTradeGlobalMonitor": QuantXAPI.Objects.TTradeGlobalMonitor,
      "TTradeLiveReadiness": QuantXAPI.Objects.TTradeLiveReadiness,
      "TTradeReadinessCheck": QuantXAPI.Objects.TTradeReadinessCheck,
      "TTradeSession": QuantXAPI.Objects.TTradeSession,
      "TTradeSignalHistoryEntry": QuantXAPI.Objects.TTradeSignalHistoryEntry,
      "TTradeSignalHistoryPage": QuantXAPI.Objects.TTradeSignalHistoryPage,
      "Trade": QuantXAPI.Objects.Trade,
      "TradeApprovalConfirmationResult": QuantXAPI.Objects.TradeApprovalConfirmationResult,
      "TradeApprovalPreview": QuantXAPI.Objects.TradeApprovalPreview,
      "TradeApprovalPreviewResult": QuantXAPI.Objects.TradeApprovalPreviewResult,
      "WatchlistItem": QuantXAPI.Objects.WatchlistItem
    ]

    static func objectType(forTypename typename: String) -> ApolloAPI.Object? {
      objectTypeMap[typename]
    }
  }

  nonisolated enum Objects {}
  nonisolated enum Interfaces {}
  nonisolated enum Unions {}

}