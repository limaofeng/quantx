import { gql } from '@/generated/gql';

export const AccountExecutionSafetyQuery = gql(`
  query TradingSafety_AccountExecutionSafety($accountId: String!) {
    accountExecutionSafety(accountId: $accountId) {
      accountId
      authorizationState
      stateVersion
      healthStatus
      executionMode
      canIncreaseRisk
      canReduceRisk
      canActivateAutomation
      summary
      engineStatus
      agentStatus
      agentMode
      protocolVersion
      reconcileStatus
      killSwitch
      blockedReasons
      executionWindowActive
      snapshotId
      snapshotHash
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

export const PreviewAccountExecutionControlMutation = gql(`
  mutation TradingSafety_PreviewAccountExecutionControl(
    $input: AccountExecutionControlPreviewInput!
  ) {
    previewAccountExecutionControl(input: $input) {
      success
      code
      message
      preview {
        challengeId
        confirmationToken
        tokenIssued
        accountId
        action
        stateVersion
        snapshotId
        reason
        challengeExpiresAt
        challengeStatus
        operationStatus
        safety {
          accountId
          authorizationState
          stateVersion
          healthStatus
          executionMode
          canIncreaseRisk
          canReduceRisk
          canActivateAutomation
          summary
          blockedReasons
        }
      }
    }
  }
`);

export const ConfirmAccountExecutionControlMutation = gql(`
  mutation TradingSafety_ConfirmAccountExecutionControl(
    $input: AccountExecutionControlConfirmationInput!
  ) {
    confirmAccountExecutionControl(input: $input) {
      success
      code
      message
      challengeId
      action
      operationStatus
      safety {
        accountId
        authorizationState
        stateVersion
        healthStatus
        executionMode
        canIncreaseRisk
        canReduceRisk
        canActivateAutomation
        summary
        blockedReasons
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
