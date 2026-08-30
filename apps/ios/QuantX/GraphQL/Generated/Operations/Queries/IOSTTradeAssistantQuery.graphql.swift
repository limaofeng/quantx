// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTTradeAssistantQuery: GraphQLQuery {
    static let operationName: String = "IOSTTradeAssistant"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTTradeAssistant($accountId: String!) { tTradeGlobalMonitor(accountId: $accountId) { __typename accountId enabled mode holdingCount eligibleCount ignoredCount monitoredCount pendingSignalCount activeBatchCount drainingCount lastReconciledAt lastError updatedAt positionSnapshotSource positionSnapshotReportedAt positionSnapshotReceivedAt positionSnapshotComplete positionSnapshotError rolloutStage engineStatus agentStatus reconcileStatus killSwitch canApprove canActivateLive blockedReasons projectionGeneratedAt readiness { __typename accountId ready stage engineStatus agentStatus agentDeviceId reconcileStatus killSwitch policyVersion canApprove canActivateLive blockedReasons checkedAt checks { __typename code passed message } } holdings { __typename stockCode instrumentName volume availableVolume ignored eligible status reason session { __typename runId runStatus status mode activeVolume lastPrice lastNetProfitPct peakNetProfitPct trailingFloorPct completedCycles pendingEntryIntentId pendingExitIntentId entryOrderStatus exitOrderStatus entryFilledVolume entryAvgPrice exitFilledVolume exitAvgPrice profitArmed lastExitReason canCancel errorMessage signalSnapshot { __typename instrumentCode evaluatedAt sourceAt dataHealth dominantPhase opportunityScore candidateThreshold features { __typename price pullbackPct reboundPct } topBlockers { __typename code label detail } candidateId candidateFingerprint candidateStatus candidateExpiresAt pendingEntryIntentId signalVersion candidateStateVersion stateSchemaVersion featureSchemaVersion policyVersion configVersion } } } } tTradeBatchesPage(accountId: $accountId, filter: {scope: CURRENT}, first: 20) { __typename items { __typename batchId accountId stockCode status targetVolume entryFilledVolume entryAvgPrice exitFilledVolume exitAvgPrice activeVolume lastPrice lastNetProfitPct peakNetProfitPct trailingFloorPct exitReason exceptionReason createdAt updatedAt } pageInfo { __typename hasNextPage endCursor } } }"#
      ))

    public var accountId: String

    public init(accountId: String) {
      self.accountId = accountId
    }

    @_spi(Unsafe) public var __variables: Variables? { ["accountId": accountId] }

    nonisolated struct Data: QuantXAPI.SelectionSet {
      let __data: DataDict
      init(_dataDict: DataDict) { __data = _dataDict }

      static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.Query }
      static var __selections: [ApolloAPI.Selection] { [
        .field("tTradeGlobalMonitor", TTradeGlobalMonitor.self, arguments: ["accountId": .variable("accountId")]),
        .field("tTradeBatchesPage", TTradeBatchesPage.self, arguments: [
          "accountId": .variable("accountId"),
          "filter": ["scope": "CURRENT"],
          "first": 20
        ]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTTradeAssistantQuery.Data.self
      ] }

      var tTradeGlobalMonitor: TTradeGlobalMonitor { __data["tTradeGlobalMonitor"] }
      var tTradeBatchesPage: TTradeBatchesPage { __data["tTradeBatchesPage"] }

      /// TTradeGlobalMonitor
      nonisolated struct TTradeGlobalMonitor: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeGlobalMonitor }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("enabled", Bool.self),
          .field("mode", String.self),
          .field("holdingCount", Int.self),
          .field("eligibleCount", Int.self),
          .field("ignoredCount", Int.self),
          .field("monitoredCount", Int.self),
          .field("pendingSignalCount", Int.self),
          .field("activeBatchCount", Int.self),
          .field("drainingCount", Int.self),
          .field("lastReconciledAt", QuantXAPI.DateTime?.self),
          .field("lastError", String?.self),
          .field("updatedAt", QuantXAPI.DateTime?.self),
          .field("positionSnapshotSource", String?.self),
          .field("positionSnapshotReportedAt", QuantXAPI.DateTime?.self),
          .field("positionSnapshotReceivedAt", QuantXAPI.DateTime?.self),
          .field("positionSnapshotComplete", Bool.self),
          .field("positionSnapshotError", String?.self),
          .field("rolloutStage", String.self),
          .field("engineStatus", String.self),
          .field("agentStatus", String.self),
          .field("reconcileStatus", String.self),
          .field("killSwitch", Bool.self),
          .field("canApprove", Bool.self),
          .field("canActivateLive", Bool.self),
          .field("blockedReasons", [String].self),
          .field("projectionGeneratedAt", QuantXAPI.DateTime?.self),
          .field("readiness", Readiness?.self),
          .field("holdings", [Holding].self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.self
        ] }

        var accountId: String { __data["accountId"] }
        var enabled: Bool { __data["enabled"] }
        var mode: String { __data["mode"] }
        var holdingCount: Int { __data["holdingCount"] }
        var eligibleCount: Int { __data["eligibleCount"] }
        var ignoredCount: Int { __data["ignoredCount"] }
        var monitoredCount: Int { __data["monitoredCount"] }
        var pendingSignalCount: Int { __data["pendingSignalCount"] }
        var activeBatchCount: Int { __data["activeBatchCount"] }
        var drainingCount: Int { __data["drainingCount"] }
        var lastReconciledAt: QuantXAPI.DateTime? { __data["lastReconciledAt"] }
        var lastError: String? { __data["lastError"] }
        var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
        var positionSnapshotSource: String? { __data["positionSnapshotSource"] }
        var positionSnapshotReportedAt: QuantXAPI.DateTime? { __data["positionSnapshotReportedAt"] }
        var positionSnapshotReceivedAt: QuantXAPI.DateTime? { __data["positionSnapshotReceivedAt"] }
        var positionSnapshotComplete: Bool { __data["positionSnapshotComplete"] }
        var positionSnapshotError: String? { __data["positionSnapshotError"] }
        var rolloutStage: String { __data["rolloutStage"] }
        var engineStatus: String { __data["engineStatus"] }
        var agentStatus: String { __data["agentStatus"] }
        var reconcileStatus: String { __data["reconcileStatus"] }
        var killSwitch: Bool { __data["killSwitch"] }
        var canApprove: Bool { __data["canApprove"] }
        var canActivateLive: Bool { __data["canActivateLive"] }
        var blockedReasons: [String] { __data["blockedReasons"] }
        var projectionGeneratedAt: QuantXAPI.DateTime? { __data["projectionGeneratedAt"] }
        var readiness: Readiness? { __data["readiness"] }
        var holdings: [Holding] { __data["holdings"] }

        /// TTradeGlobalMonitor.Readiness
        nonisolated struct Readiness: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeLiveReadiness }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("accountId", String.self),
            .field("ready", Bool.self),
            .field("stage", String.self),
            .field("engineStatus", String.self),
            .field("agentStatus", String.self),
            .field("agentDeviceId", String?.self),
            .field("reconcileStatus", String.self),
            .field("killSwitch", Bool.self),
            .field("policyVersion", Int.self),
            .field("canApprove", Bool.self),
            .field("canActivateLive", Bool.self),
            .field("blockedReasons", [String].self),
            .field("checkedAt", QuantXAPI.DateTime.self),
            .field("checks", [Check].self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Readiness.self
          ] }

          var accountId: String { __data["accountId"] }
          var ready: Bool { __data["ready"] }
          var stage: String { __data["stage"] }
          var engineStatus: String { __data["engineStatus"] }
          var agentStatus: String { __data["agentStatus"] }
          var agentDeviceId: String? { __data["agentDeviceId"] }
          var reconcileStatus: String { __data["reconcileStatus"] }
          var killSwitch: Bool { __data["killSwitch"] }
          var policyVersion: Int { __data["policyVersion"] }
          var canApprove: Bool { __data["canApprove"] }
          var canActivateLive: Bool { __data["canActivateLive"] }
          var blockedReasons: [String] { __data["blockedReasons"] }
          var checkedAt: QuantXAPI.DateTime { __data["checkedAt"] }
          var checks: [Check] { __data["checks"] }

          /// TTradeGlobalMonitor.Readiness.Check
          nonisolated struct Check: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeReadinessCheck }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("code", String.self),
              .field("passed", Bool.self),
              .field("message", String.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Readiness.Check.self
            ] }

            var code: String { __data["code"] }
            var passed: Bool { __data["passed"] }
            var message: String { __data["message"] }
          }
        }

        /// TTradeGlobalMonitor.Holding
        nonisolated struct Holding: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeGlobalHolding }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("stockCode", String.self),
            .field("instrumentName", String.self),
            .field("volume", Int.self),
            .field("availableVolume", Int.self),
            .field("ignored", Bool.self),
            .field("eligible", Bool.self),
            .field("status", String.self),
            .field("reason", String.self),
            .field("session", Session?.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding.self
          ] }

          var stockCode: String { __data["stockCode"] }
          var instrumentName: String { __data["instrumentName"] }
          var volume: Int { __data["volume"] }
          var availableVolume: Int { __data["availableVolume"] }
          var ignored: Bool { __data["ignored"] }
          var eligible: Bool { __data["eligible"] }
          var status: String { __data["status"] }
          var reason: String { __data["reason"] }
          var session: Session? { __data["session"] }

          /// TTradeGlobalMonitor.Holding.Session
          nonisolated struct Session: QuantXAPI.SelectionSet {
            let __data: DataDict
            init(_dataDict: DataDict) { __data = _dataDict }

            static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeSession }
            static var __selections: [ApolloAPI.Selection] { [
              .field("__typename", String.self),
              .field("runId", String.self),
              .field("runStatus", String.self),
              .field("status", String.self),
              .field("mode", String.self),
              .field("activeVolume", Int.self),
              .field("lastPrice", Double.self),
              .field("lastNetProfitPct", Double.self),
              .field("peakNetProfitPct", Double.self),
              .field("trailingFloorPct", Double?.self),
              .field("completedCycles", Int.self),
              .field("pendingEntryIntentId", String?.self),
              .field("pendingExitIntentId", String?.self),
              .field("entryOrderStatus", String.self),
              .field("exitOrderStatus", String.self),
              .field("entryFilledVolume", Int.self),
              .field("entryAvgPrice", Double.self),
              .field("exitFilledVolume", Int.self),
              .field("exitAvgPrice", Double.self),
              .field("profitArmed", Bool.self),
              .field("lastExitReason", String.self),
              .field("canCancel", Bool.self),
              .field("errorMessage", String?.self),
              .field("signalSnapshot", SignalSnapshot?.self),
            ] }
            static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
              IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding.Session.self
            ] }

            var runId: String { __data["runId"] }
            var runStatus: String { __data["runStatus"] }
            var status: String { __data["status"] }
            var mode: String { __data["mode"] }
            var activeVolume: Int { __data["activeVolume"] }
            var lastPrice: Double { __data["lastPrice"] }
            var lastNetProfitPct: Double { __data["lastNetProfitPct"] }
            var peakNetProfitPct: Double { __data["peakNetProfitPct"] }
            var trailingFloorPct: Double? { __data["trailingFloorPct"] }
            var completedCycles: Int { __data["completedCycles"] }
            var pendingEntryIntentId: String? { __data["pendingEntryIntentId"] }
            var pendingExitIntentId: String? { __data["pendingExitIntentId"] }
            var entryOrderStatus: String { __data["entryOrderStatus"] }
            var exitOrderStatus: String { __data["exitOrderStatus"] }
            var entryFilledVolume: Int { __data["entryFilledVolume"] }
            var entryAvgPrice: Double { __data["entryAvgPrice"] }
            var exitFilledVolume: Int { __data["exitFilledVolume"] }
            var exitAvgPrice: Double { __data["exitAvgPrice"] }
            var profitArmed: Bool { __data["profitArmed"] }
            var lastExitReason: String { __data["lastExitReason"] }
            var canCancel: Bool { __data["canCancel"] }
            var errorMessage: String? { __data["errorMessage"] }
            var signalSnapshot: SignalSnapshot? { __data["signalSnapshot"] }

            /// TTradeGlobalMonitor.Holding.Session.SignalSnapshot
            nonisolated struct SignalSnapshot: QuantXAPI.SelectionSet {
              let __data: DataDict
              init(_dataDict: DataDict) { __data = _dataDict }

              static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeSignalSnapshot }
              static var __selections: [ApolloAPI.Selection] { [
                .field("__typename", String.self),
                .field("instrumentCode", String.self),
                .field("evaluatedAt", QuantXAPI.DateTime.self),
                .field("sourceAt", QuantXAPI.DateTime.self),
                .field("dataHealth", GraphQLEnum<QuantXAPI.TTradeSignalDataHealth>.self),
                .field("dominantPhase", GraphQLEnum<QuantXAPI.TTradeDominantPhase>.self),
                .field("opportunityScore", Double?.self),
                .field("candidateThreshold", Double.self),
                .field("features", Features.self),
                .field("topBlockers", [TopBlocker].self),
                .field("candidateId", String?.self),
                .field("candidateFingerprint", String?.self),
                .field("candidateStatus", GraphQLEnum<QuantXAPI.TTradeCandidateStatus>.self),
                .field("candidateExpiresAt", QuantXAPI.DateTime?.self),
                .field("pendingEntryIntentId", String?.self),
                .field("signalVersion", Int.self),
                .field("candidateStateVersion", Int.self),
                .field("stateSchemaVersion", String.self),
                .field("featureSchemaVersion", String.self),
                .field("policyVersion", String.self),
                .field("configVersion", Int.self),
              ] }
              static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
                IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding.Session.SignalSnapshot.self
              ] }

              var instrumentCode: String { __data["instrumentCode"] }
              var evaluatedAt: QuantXAPI.DateTime { __data["evaluatedAt"] }
              var sourceAt: QuantXAPI.DateTime { __data["sourceAt"] }
              var dataHealth: GraphQLEnum<QuantXAPI.TTradeSignalDataHealth> { __data["dataHealth"] }
              var dominantPhase: GraphQLEnum<QuantXAPI.TTradeDominantPhase> { __data["dominantPhase"] }
              var opportunityScore: Double? { __data["opportunityScore"] }
              var candidateThreshold: Double { __data["candidateThreshold"] }
              var features: Features { __data["features"] }
              var topBlockers: [TopBlocker] { __data["topBlockers"] }
              var candidateId: String? { __data["candidateId"] }
              var candidateFingerprint: String? { __data["candidateFingerprint"] }
              var candidateStatus: GraphQLEnum<QuantXAPI.TTradeCandidateStatus> { __data["candidateStatus"] }
              var candidateExpiresAt: QuantXAPI.DateTime? { __data["candidateExpiresAt"] }
              var pendingEntryIntentId: String? { __data["pendingEntryIntentId"] }
              var signalVersion: Int { __data["signalVersion"] }
              var candidateStateVersion: Int { __data["candidateStateVersion"] }
              var stateSchemaVersion: String { __data["stateSchemaVersion"] }
              var featureSchemaVersion: String { __data["featureSchemaVersion"] }
              var policyVersion: String { __data["policyVersion"] }
              var configVersion: Int { __data["configVersion"] }

              /// TTradeGlobalMonitor.Holding.Session.SignalSnapshot.Features
              nonisolated struct Features: QuantXAPI.SelectionSet {
                let __data: DataDict
                init(_dataDict: DataDict) { __data = _dataDict }

                static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeSignalFeatures }
                static var __selections: [ApolloAPI.Selection] { [
                  .field("__typename", String.self),
                  .field("price", Double?.self),
                  .field("pullbackPct", Double?.self),
                  .field("reboundPct", Double?.self),
                ] }
                static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
                  IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding.Session.SignalSnapshot.Features.self
                ] }

                var price: Double? { __data["price"] }
                var pullbackPct: Double? { __data["pullbackPct"] }
                var reboundPct: Double? { __data["reboundPct"] }
              }

              /// TTradeGlobalMonitor.Holding.Session.SignalSnapshot.TopBlocker
              nonisolated struct TopBlocker: QuantXAPI.SelectionSet {
                let __data: DataDict
                init(_dataDict: DataDict) { __data = _dataDict }

                static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeSignalBlocker }
                static var __selections: [ApolloAPI.Selection] { [
                  .field("__typename", String.self),
                  .field("code", String.self),
                  .field("label", String.self),
                  .field("detail", String.self),
                ] }
                static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
                  IOSTTradeAssistantQuery.Data.TTradeGlobalMonitor.Holding.Session.SignalSnapshot.TopBlocker.self
                ] }

                var code: String { __data["code"] }
                var label: String { __data["label"] }
                var detail: String { __data["detail"] }
              }
            }
          }
        }
      }

      /// TTradeBatchesPage
      nonisolated struct TTradeBatchesPage: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeBatchPage }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("items", [Item].self),
          .field("pageInfo", PageInfo.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTTradeAssistantQuery.Data.TTradeBatchesPage.self
        ] }

        var items: [Item] { __data["items"] }
        var pageInfo: PageInfo { __data["pageInfo"] }

        /// TTradeBatchesPage.Item
        nonisolated struct Item: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeBatch }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("batchId", String.self),
            .field("accountId", String.self),
            .field("stockCode", String.self),
            .field("status", String.self),
            .field("targetVolume", Int.self),
            .field("entryFilledVolume", Int.self),
            .field("entryAvgPrice", Double.self),
            .field("exitFilledVolume", Int.self),
            .field("exitAvgPrice", Double.self),
            .field("activeVolume", Int.self),
            .field("lastPrice", Double.self),
            .field("lastNetProfitPct", Double.self),
            .field("peakNetProfitPct", Double.self),
            .field("trailingFloorPct", Double?.self),
            .field("exitReason", String?.self),
            .field("exceptionReason", String?.self),
            .field("createdAt", QuantXAPI.DateTime?.self),
            .field("updatedAt", QuantXAPI.DateTime?.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSTTradeAssistantQuery.Data.TTradeBatchesPage.Item.self
          ] }

          var batchId: String { __data["batchId"] }
          var accountId: String { __data["accountId"] }
          var stockCode: String { __data["stockCode"] }
          var status: String { __data["status"] }
          var targetVolume: Int { __data["targetVolume"] }
          var entryFilledVolume: Int { __data["entryFilledVolume"] }
          var entryAvgPrice: Double { __data["entryAvgPrice"] }
          var exitFilledVolume: Int { __data["exitFilledVolume"] }
          var exitAvgPrice: Double { __data["exitAvgPrice"] }
          var activeVolume: Int { __data["activeVolume"] }
          var lastPrice: Double { __data["lastPrice"] }
          var lastNetProfitPct: Double { __data["lastNetProfitPct"] }
          var peakNetProfitPct: Double { __data["peakNetProfitPct"] }
          var trailingFloorPct: Double? { __data["trailingFloorPct"] }
          var exitReason: String? { __data["exitReason"] }
          var exceptionReason: String? { __data["exceptionReason"] }
          var createdAt: QuantXAPI.DateTime? { __data["createdAt"] }
          var updatedAt: QuantXAPI.DateTime? { __data["updatedAt"] }
        }

        /// TTradeBatchesPage.PageInfo
        nonisolated struct PageInfo: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.PageInfo }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("hasNextPage", Bool.self),
            .field("endCursor", String?.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSTTradeAssistantQuery.Data.TTradeBatchesPage.PageInfo.self
          ] }

          var hasNextPage: Bool { __data["hasNextPage"] }
          var endCursor: String? { __data["endCursor"] }
        }
      }

    }
  }

}
