// @generated
// This file was automatically generated and should not be edited.

@_exported import ApolloAPI
@_spi(Execution) @_spi(Unsafe) import ApolloAPI

extension QuantXAPI {
  nonisolated struct IOSTTradeControlStateQuery: GraphQLQuery {
    static let operationName: String = "IOSTTradeControlState"
    static let operationDocument: ApolloAPI.OperationDocument = .init(
      definition: .init(
        #"query IOSTTradeControlState($accountId: String!) { tTradeGlobalMonitor(accountId: $accountId) { __typename accountId enabled mode rolloutStage killSwitch pendingSignalCount activeBatchCount drainingCount positionSnapshotSource positionSnapshotReportedAt positionSnapshotReceivedAt positionSnapshotComplete positionSnapshotError projectionGeneratedAt } validateTTradeLiveReadiness(accountId: $accountId) { __typename accountId ready status preparationReady automationReady stage engineStatus agentStatus agentDeviceId agentMode protocolVersion reconcileStatus killSwitch policyVersion canApprove canActivateLive blockedReasons preparationBlockedReasons checks { __typename code passed message scope } snapshotId snapshotHash snapshotAt reconciliationAgeSeconds queuedCommandCount queueDelaySeconds deadLetterCount unresolvedCriticalAlertCount manualCoexistence externalOrderCount externalTradeCount controlledWindowActive controlledWindowSnapshotId controlledWindowStartedAt newExternalOrderCount newExternalTradeCount workingExternalOrderCount journalIntegrity journalSizeBytes journalPendingReports lastBackupAt checkedAt } }"#
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
        .field("validateTTradeLiveReadiness", ValidateTTradeLiveReadiness.self, arguments: ["accountId": .variable("accountId")]),
      ] }
      static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
        IOSTTradeControlStateQuery.Data.self
      ] }

      var tTradeGlobalMonitor: TTradeGlobalMonitor { __data["tTradeGlobalMonitor"] }
      var validateTTradeLiveReadiness: ValidateTTradeLiveReadiness { __data["validateTTradeLiveReadiness"] }

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
          .field("rolloutStage", String.self),
          .field("killSwitch", Bool.self),
          .field("pendingSignalCount", Int.self),
          .field("activeBatchCount", Int.self),
          .field("drainingCount", Int.self),
          .field("positionSnapshotSource", String?.self),
          .field("positionSnapshotReportedAt", QuantXAPI.DateTime?.self),
          .field("positionSnapshotReceivedAt", QuantXAPI.DateTime?.self),
          .field("positionSnapshotComplete", Bool.self),
          .field("positionSnapshotError", String?.self),
          .field("projectionGeneratedAt", QuantXAPI.DateTime?.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTTradeControlStateQuery.Data.TTradeGlobalMonitor.self
        ] }

        var accountId: String { __data["accountId"] }
        var enabled: Bool { __data["enabled"] }
        var mode: String { __data["mode"] }
        var rolloutStage: String { __data["rolloutStage"] }
        var killSwitch: Bool { __data["killSwitch"] }
        var pendingSignalCount: Int { __data["pendingSignalCount"] }
        var activeBatchCount: Int { __data["activeBatchCount"] }
        var drainingCount: Int { __data["drainingCount"] }
        var positionSnapshotSource: String? { __data["positionSnapshotSource"] }
        var positionSnapshotReportedAt: QuantXAPI.DateTime? { __data["positionSnapshotReportedAt"] }
        var positionSnapshotReceivedAt: QuantXAPI.DateTime? { __data["positionSnapshotReceivedAt"] }
        var positionSnapshotComplete: Bool { __data["positionSnapshotComplete"] }
        var positionSnapshotError: String? { __data["positionSnapshotError"] }
        var projectionGeneratedAt: QuantXAPI.DateTime? { __data["projectionGeneratedAt"] }
      }

      /// ValidateTTradeLiveReadiness
      nonisolated struct ValidateTTradeLiveReadiness: QuantXAPI.SelectionSet {
        let __data: DataDict
        init(_dataDict: DataDict) { __data = _dataDict }

        static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeLiveReadiness }
        static var __selections: [ApolloAPI.Selection] { [
          .field("__typename", String.self),
          .field("accountId", String.self),
          .field("ready", Bool.self),
          .field("status", String.self),
          .field("preparationReady", Bool.self),
          .field("automationReady", Bool.self),
          .field("stage", String.self),
          .field("engineStatus", String.self),
          .field("agentStatus", String.self),
          .field("agentDeviceId", String?.self),
          .field("agentMode", String.self),
          .field("protocolVersion", String.self),
          .field("reconcileStatus", String.self),
          .field("killSwitch", Bool.self),
          .field("policyVersion", Int.self),
          .field("canApprove", Bool.self),
          .field("canActivateLive", Bool.self),
          .field("blockedReasons", [String].self),
          .field("preparationBlockedReasons", [String].self),
          .field("checks", [Check].self),
          .field("snapshotId", String?.self),
          .field("snapshotHash", String?.self),
          .field("snapshotAt", QuantXAPI.DateTime?.self),
          .field("reconciliationAgeSeconds", Double?.self),
          .field("queuedCommandCount", Int.self),
          .field("queueDelaySeconds", Double.self),
          .field("deadLetterCount", Int.self),
          .field("unresolvedCriticalAlertCount", Int.self),
          .field("manualCoexistence", Bool.self),
          .field("externalOrderCount", Int.self),
          .field("externalTradeCount", Int.self),
          .field("controlledWindowActive", Bool.self),
          .field("controlledWindowSnapshotId", String?.self),
          .field("controlledWindowStartedAt", QuantXAPI.DateTime?.self),
          .field("newExternalOrderCount", Int.self),
          .field("newExternalTradeCount", Int.self),
          .field("workingExternalOrderCount", Int.self),
          .field("journalIntegrity", String.self),
          .field("journalSizeBytes", Int.self),
          .field("journalPendingReports", Int.self),
          .field("lastBackupAt", QuantXAPI.DateTime?.self),
          .field("checkedAt", QuantXAPI.DateTime.self),
        ] }
        static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
          IOSTTradeControlStateQuery.Data.ValidateTTradeLiveReadiness.self
        ] }

        var accountId: String { __data["accountId"] }
        var ready: Bool { __data["ready"] }
        var status: String { __data["status"] }
        var preparationReady: Bool { __data["preparationReady"] }
        var automationReady: Bool { __data["automationReady"] }
        var stage: String { __data["stage"] }
        var engineStatus: String { __data["engineStatus"] }
        var agentStatus: String { __data["agentStatus"] }
        var agentDeviceId: String? { __data["agentDeviceId"] }
        var agentMode: String { __data["agentMode"] }
        var protocolVersion: String { __data["protocolVersion"] }
        var reconcileStatus: String { __data["reconcileStatus"] }
        var killSwitch: Bool { __data["killSwitch"] }
        var policyVersion: Int { __data["policyVersion"] }
        var canApprove: Bool { __data["canApprove"] }
        var canActivateLive: Bool { __data["canActivateLive"] }
        var blockedReasons: [String] { __data["blockedReasons"] }
        var preparationBlockedReasons: [String] { __data["preparationBlockedReasons"] }
        var checks: [Check] { __data["checks"] }
        var snapshotId: String? { __data["snapshotId"] }
        var snapshotHash: String? { __data["snapshotHash"] }
        var snapshotAt: QuantXAPI.DateTime? { __data["snapshotAt"] }
        var reconciliationAgeSeconds: Double? { __data["reconciliationAgeSeconds"] }
        var queuedCommandCount: Int { __data["queuedCommandCount"] }
        var queueDelaySeconds: Double { __data["queueDelaySeconds"] }
        var deadLetterCount: Int { __data["deadLetterCount"] }
        var unresolvedCriticalAlertCount: Int { __data["unresolvedCriticalAlertCount"] }
        var manualCoexistence: Bool { __data["manualCoexistence"] }
        var externalOrderCount: Int { __data["externalOrderCount"] }
        var externalTradeCount: Int { __data["externalTradeCount"] }
        var controlledWindowActive: Bool { __data["controlledWindowActive"] }
        var controlledWindowSnapshotId: String? { __data["controlledWindowSnapshotId"] }
        var controlledWindowStartedAt: QuantXAPI.DateTime? { __data["controlledWindowStartedAt"] }
        var newExternalOrderCount: Int { __data["newExternalOrderCount"] }
        var newExternalTradeCount: Int { __data["newExternalTradeCount"] }
        var workingExternalOrderCount: Int { __data["workingExternalOrderCount"] }
        var journalIntegrity: String { __data["journalIntegrity"] }
        var journalSizeBytes: Int { __data["journalSizeBytes"] }
        var journalPendingReports: Int { __data["journalPendingReports"] }
        var lastBackupAt: QuantXAPI.DateTime? { __data["lastBackupAt"] }
        var checkedAt: QuantXAPI.DateTime { __data["checkedAt"] }

        /// ValidateTTradeLiveReadiness.Check
        nonisolated struct Check: QuantXAPI.SelectionSet {
          let __data: DataDict
          init(_dataDict: DataDict) { __data = _dataDict }

          static var __parentType: any ApolloAPI.ParentType { QuantXAPI.Objects.TTradeReadinessCheck }
          static var __selections: [ApolloAPI.Selection] { [
            .field("__typename", String.self),
            .field("code", String.self),
            .field("passed", Bool.self),
            .field("message", String.self),
            .field("scope", String.self),
          ] }
          static var __fulfilledFragments: [any ApolloAPI.SelectionSet.Type] { [
            IOSTTradeControlStateQuery.Data.ValidateTTradeLiveReadiness.Check.self
          ] }

          var code: String { __data["code"] }
          var passed: Bool { __data["passed"] }
          var message: String { __data["message"] }
          var scope: String { __data["scope"] }
        }
      }
    }
  }

}