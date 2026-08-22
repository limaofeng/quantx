import { gql } from '@/generated/gql';

export const AccountExecutionSafetyQuery = gql(`
  query TradingSafety_AccountExecutionSafety($accountId: String!) {
    accountExecutionSafety(accountId: $accountId) {
      accountId
      healthStatus
      executionMode
      canIncreaseRisk
      canReduceRisk
      summary
      engineStatus
      agentStatus
      agentMode
      protocolVersion
      reconcileStatus
      killSwitch
      blockedReasons
      executionWindowActive
      snapshotAt
      reconciliationAgeSeconds
      queuedCommandCount
      queueDelaySeconds
      deadLetterCount
      unresolvedCriticalAlertCount
      externalOrderCount
      externalTradeCount
      newExternalOrderCount
      newExternalTradeCount
      workingExternalOrderCount
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
