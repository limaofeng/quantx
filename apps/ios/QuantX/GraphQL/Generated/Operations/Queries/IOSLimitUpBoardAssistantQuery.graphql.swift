// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSLimitUpBoardAssistantQuery: GraphQLQuery {
    static let operationName: String = "IOSLimitUpBoardAssistant"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSLimitUpBoardAssistant($runId: String!) { strategyPendingTradeIntents(runId: $runId) { __typename id runId instrumentCode side bucket reason status executionMode confidence limitPriceHint targetPositionPct targetAmount targetVolume signalPrice limitUpPrice distanceToLimitTicks approvalExpiresAt createdAt } strategyExitPlans(runId: $runId) { __typename id instrumentCode sourceType bucket status entryFilledVolume entryAvgPrice exitedVolume exitAvgPrice remainingVolume peakPrice lastPrice lastNetProfitPct peakNetProfitPct holdingTradingDays pendingIntentId pendingOrderId lastExitReason t1Policy executionMode autoExitAuthorized ruleTypes } }"#
      ))

    public var runId: String

    public init(runId: String) {
      self.runId = runId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["runId": runId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("strategyPendingTradeIntents", [StrategyPendingTradeIntent].self, arguments: ["runId": .variable("runId")]),
        .field("strategyExitPlans", [StrategyExitPlan].self, arguments: ["runId": .variable("runId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSLimitUpBoardAssistantQuery.Data.self
      ] }

      var strategyPendingTradeIntents: [StrategyPendingTradeIntent] { __data["strategyPendingTradeIntents"] }
      var strategyExitPlans: [StrategyExitPlan] { __data["strategyExitPlans"] }

      /// StrategyPendingTradeIntent
      nonisolated struct StrategyPendingTradeIntent: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyApprovalIntent }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("runId", String.self),
          .field("instrumentCode", String.self),
          .field("side", String.self),
          .field("bucket", String.self),
          .field("reason", String.self),
          .field("status", String.self),
          .field("executionMode", String.self),
          .field("confidence", Double.self),
          .field("limitPriceHint", Double?.self),
          .field("targetPositionPct", Double?.self),
          .field("targetAmount", Double?.self),
          .field("targetVolume", Int?.self),
          .field("signalPrice", Double?.self),
          .field("limitUpPrice", Double?.self),
          .field("distanceToLimitTicks", Double?.self),
          .field("approvalExpiresAt", QuantXAPI.DateTime?.self),
          .field("createdAt", QuantXAPI.DateTime?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSLimitUpBoardAssistantQuery.Data.StrategyPendingTradeIntent.self
        ] }

        var id: String { __data["id"] }
        var runId: String { __data["runId"] }
        var instrumentCode: String { __data["instrumentCode"] }
        var side: String { __data["side"] }
        var bucket: String { __data["bucket"] }
        var reason: String { __data["reason"] }
        var status: String { __data["status"] }
        var executionMode: String { __data["executionMode"] }
        var confidence: Double { __data["confidence"] }
        var limitPriceHint: Double? { __data["limitPriceHint"] }
        var targetPositionPct: Double? { __data["targetPositionPct"] }
        var targetAmount: Double? { __data["targetAmount"] }
        var targetVolume: Int? { __data["targetVolume"] }
        var signalPrice: Double? { __data["signalPrice"] }
        var limitUpPrice: Double? { __data["limitUpPrice"] }
        var distanceToLimitTicks: Double? { __data["distanceToLimitTicks"] }
        var approvalExpiresAt: QuantXAPI.DateTime? { __data["approvalExpiresAt"] }
        var createdAt: QuantXAPI.DateTime? { __data["createdAt"] }
      }

      /// StrategyExitPlan
      nonisolated struct StrategyExitPlan: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.StrategyExitPlanView }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("id", String.self),
          .field("instrumentCode", String.self),
          .field("sourceType", String.self),
          .field("bucket", String.self),
          .field("status", String.self),
          .field("entryFilledVolume", Int.self),
          .field("entryAvgPrice", Double.self),
          .field("exitedVolume", Int.self),
          .field("exitAvgPrice", Double.self),
          .field("remainingVolume", Int.self),
          .field("peakPrice", Double.self),
          .field("lastPrice", Double.self),
          .field("lastNetProfitPct", Double.self),
          .field("peakNetProfitPct", Double.self),
          .field("holdingTradingDays", Int.self),
          .field("pendingIntentId", String?.self),
          .field("pendingOrderId", String?.self),
          .field("lastExitReason", String?.self),
          .field("t1Policy", String.self),
          .field("executionMode", String.self),
          .field("autoExitAuthorized", Bool.self),
          .field("ruleTypes", [String].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSLimitUpBoardAssistantQuery.Data.StrategyExitPlan.self
        ] }

        var id: String { __data["id"] }
        var instrumentCode: String { __data["instrumentCode"] }
        var sourceType: String { __data["sourceType"] }
        var bucket: String { __data["bucket"] }
        var status: String { __data["status"] }
        var entryFilledVolume: Int { __data["entryFilledVolume"] }
        var entryAvgPrice: Double { __data["entryAvgPrice"] }
        var exitedVolume: Int { __data["exitedVolume"] }
        var exitAvgPrice: Double { __data["exitAvgPrice"] }
        var remainingVolume: Int { __data["remainingVolume"] }
        var peakPrice: Double { __data["peakPrice"] }
        var lastPrice: Double { __data["lastPrice"] }
        var lastNetProfitPct: Double { __data["lastNetProfitPct"] }
        var peakNetProfitPct: Double { __data["peakNetProfitPct"] }
        var holdingTradingDays: Int { __data["holdingTradingDays"] }
        var pendingIntentId: String? { __data["pendingIntentId"] }
        var pendingOrderId: String? { __data["pendingOrderId"] }
        var lastExitReason: String? { __data["lastExitReason"] }
        var t1Policy: String { __data["t1Policy"] }
        var executionMode: String { __data["executionMode"] }
        var autoExitAuthorized: Bool { __data["autoExitAuthorized"] }
        var ruleTypes: [String] { __data["ruleTypes"] }
      }
    }
  }

}