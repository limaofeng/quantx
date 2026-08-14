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
      "CancelOrderResult": QuantXAPI.Objects.CancelOrderResult,
      "ExitPlanAuthorizationConfirmationResult": QuantXAPI.Objects.ExitPlanAuthorizationConfirmationResult,
      "ExitPlanAuthorizationPositionSnapshot": QuantXAPI.Objects.ExitPlanAuthorizationPositionSnapshot,
      "ExitPlanAuthorizationPreview": QuantXAPI.Objects.ExitPlanAuthorizationPreview,
      "ExitPlanAuthorizationPreviewResult": QuantXAPI.Objects.ExitPlanAuthorizationPreviewResult,
      "ExitPlanCapabilities": QuantXAPI.Objects.ExitPlanCapabilities,
      "ExitPlanCapacityConflict": QuantXAPI.Objects.ExitPlanCapacityConflict,
      "ExitPlanEventView": QuantXAPI.Objects.ExitPlanEventView,
      "ExitPlanHoldingCapacity": QuantXAPI.Objects.ExitPlanHoldingCapacity,
      "ExitPlanRuleCapability": QuantXAPI.Objects.ExitPlanRuleCapability,
      "ExitPlanView": QuantXAPI.Objects.ExitPlanView,
      "Instrument": QuantXAPI.Objects.Instrument,
      "KLineData": QuantXAPI.Objects.KLineData,
      "LiquidationConfirmationResult": QuantXAPI.Objects.LiquidationConfirmationResult,
      "LiquidationConflictPreview": QuantXAPI.Objects.LiquidationConflictPreview,
      "LiquidationItemPreview": QuantXAPI.Objects.LiquidationItemPreview,
      "LiquidationPlanResult": QuantXAPI.Objects.LiquidationPlanResult,
      "LiquidationPreview": QuantXAPI.Objects.LiquidationPreview,
      "LiquidationPreviewResult": QuantXAPI.Objects.LiquidationPreviewResult,
      "ManualOrderConfirmationResult": QuantXAPI.Objects.ManualOrderConfirmationResult,
      "ManualOrderPreview": QuantXAPI.Objects.ManualOrderPreview,
      "ManualOrderPreviewResult": QuantXAPI.Objects.ManualOrderPreviewResult,
      "MarketDepth": QuantXAPI.Objects.MarketDepth,
      "MarketDepthLevel": QuantXAPI.Objects.MarketDepthLevel,
      "Mutation": QuantXAPI.Objects.Mutation,
      "NotificationEventRoute": QuantXAPI.Objects.NotificationEventRoute,
      "OperationResult": QuantXAPI.Objects.OperationResult,
      "Order": QuantXAPI.Objects.Order,
      "OrderEntryCapabilities": QuantXAPI.Objects.OrderEntryCapabilities,
      "PageInfo": QuantXAPI.Objects.PageInfo,
      "PortfolioSummary": QuantXAPI.Objects.PortfolioSummary,
      "Position": QuantXAPI.Objects.Position,
      "PushCategoryPreference": QuantXAPI.Objects.PushCategoryPreference,
      "PushDeviceRegistration": QuantXAPI.Objects.PushDeviceRegistration,
      "Query": QuantXAPI.Objects.Query,
      "RealTimePrice": QuantXAPI.Objects.RealTimePrice,
      "StockQuote": QuantXAPI.Objects.StockQuote,
      "StrategyApprovalIntent": QuantXAPI.Objects.StrategyApprovalIntent,
      "StrategyControlConfirmationResult": QuantXAPI.Objects.StrategyControlConfirmationResult,
      "StrategyControlPreview": QuantXAPI.Objects.StrategyControlPreview,
      "StrategyControlPreviewResult": QuantXAPI.Objects.StrategyControlPreviewResult,
      "StrategyControlReadinessCheck": QuantXAPI.Objects.StrategyControlReadinessCheck,
      "StrategyExitPlanView": QuantXAPI.Objects.StrategyExitPlanView,
      "StrategyInstance": QuantXAPI.Objects.StrategyInstance,
      "StrategyInstanceMobileParameters": QuantXAPI.Objects.StrategyInstanceMobileParameters,
      "StrategyMobileParameter": QuantXAPI.Objects.StrategyMobileParameter,
      "Subscription": QuantXAPI.Objects.Subscription,
      "TTradeBatch": QuantXAPI.Objects.TTradeBatch,
      "TTradeBatchPage": QuantXAPI.Objects.TTradeBatchPage,
      "TTradeControlConfirmationResult": QuantXAPI.Objects.TTradeControlConfirmationResult,
      "TTradeControlPreview": QuantXAPI.Objects.TTradeControlPreview,
      "TTradeControlPreviewResult": QuantXAPI.Objects.TTradeControlPreviewResult,
      "TTradeGlobalHolding": QuantXAPI.Objects.TTradeGlobalHolding,
      "TTradeGlobalMonitor": QuantXAPI.Objects.TTradeGlobalMonitor,
      "TTradeLiveReadiness": QuantXAPI.Objects.TTradeLiveReadiness,
      "TTradeOperationsMutationResult": QuantXAPI.Objects.TTradeOperationsMutationResult,
      "TTradeReadinessCheck": QuantXAPI.Objects.TTradeReadinessCheck,
      "TTradeSession": QuantXAPI.Objects.TTradeSession,
      "TTradeSignalHistoryEntry": QuantXAPI.Objects.TTradeSignalHistoryEntry,
      "TTradeSignalHistoryPage": QuantXAPI.Objects.TTradeSignalHistoryPage,
      "Trade": QuantXAPI.Objects.Trade,
      "TradeApprovalConfirmationResult": QuantXAPI.Objects.TradeApprovalConfirmationResult,
      "TradeApprovalPreview": QuantXAPI.Objects.TradeApprovalPreview,
      "TradeApprovalPreviewResult": QuantXAPI.Objects.TradeApprovalPreviewResult,
      "UnregisterPushDeviceResult": QuantXAPI.Objects.UnregisterPushDeviceResult,
      "WatchlistItem": QuantXAPI.Objects.WatchlistItem,
      "WatchlistMutationResult": QuantXAPI.Objects.WatchlistMutationResult
    ]

    static func objectType(forTypename typename: String) -> ApolloAPI.Object? {
      objectTypeMap[typename]
    }
  }

  nonisolated enum Objects {}
  nonisolated enum Interfaces {}
  nonisolated enum Unions {}

}