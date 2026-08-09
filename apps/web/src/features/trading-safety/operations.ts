import { gql } from '@/generated/gql';

export const LiveSafetyStatusQuery = gql(`
  query TradingSafety_LiveSafetyStatus($accountId: String!) {
    liveSafetyStatus(accountId: $accountId) {
      accountId
      ready
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
      snapshotId
      snapshotHash
      snapshotAt
      reconciliationAgeSeconds
      queuedCommandCount
      queueDelaySeconds
      deadLetterCount
      unresolvedCriticalAlertCount
      journalIntegrity
      journalSizeBytes
      journalPendingReports
      lastBackupAt
      checkedAt
      checks {
        code
        passed
        message
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
