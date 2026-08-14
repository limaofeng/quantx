import { gql } from '@/generated/gql';

export const LiveSafetyStatusQuery = gql(`
  query TradingSafety_LiveSafetyStatus($accountId: String!) {
    liveSafetyStatus(accountId: $accountId) {
      accountId
      ready
      status
      preparationReady
      automationReady
      stage
      engineStatus
      agentStatus
      agentDeviceId
      agentMode
      protocolVersion
      reconcileStatus
      killSwitch
      policyVersion
      canApprove
      canActivateLive
      blockedReasons
      preparationBlockedReasons
      snapshotId
      snapshotHash
      snapshotAt
      reconciliationAgeSeconds
      queuedCommandCount
      queueDelaySeconds
      deadLetterCount
      unresolvedCriticalAlertCount
      manualCoexistence
      externalOrderCount
      externalTradeCount
      controlledWindowActive
      controlledWindowSnapshotId
      controlledWindowStartedAt
      newExternalOrderCount
      newExternalTradeCount
      workingExternalOrderCount
      journalIntegrity
      journalSizeBytes
      journalPendingReports
      lastBackupAt
      checkedAt
      checks {
        code
        passed
        message
        scope
      }
    }
  }
`);

export const AcknowledgeOperationalAlertMutation = gql(`
  mutation TradingSafety_AcknowledgeOperationalAlert($id: ID!) {
    acknowledgeOperationalAlert(id: $id) {
      id
      status
      acknowledgedAt
      acknowledgedBy
    }
  }
`);

export const ResolveOperationalAlertMutation = gql(`
  mutation TradingSafety_ResolveOperationalAlert(
    $id: ID!
    $resolution: String!
  ) {
    resolveOperationalAlert(id: $id, resolution: $resolution) {
      id
      status
      resolvedAt
      resolvedBy
      resolution
    }
  }
`);
