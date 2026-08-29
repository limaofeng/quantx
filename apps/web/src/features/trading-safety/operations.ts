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
        status
        message
        scope
      }
    }
  }
`);

export const AccountExecutionSafetyHistoryQuery = gql(`
  query TradingSafety_AccountExecutionSafetyHistory(
    $accountId: String!
    $range: AccountSafetyHistoryRange! = DAYS_30
  ) {
    accountExecutionSafetyHistory(accountId: $accountId, range: $range) {
      available
      range
      generatedAt
      firstObservedAt
      lastObservedAt
      observerFresh
      bucketSeconds
      incidentsTruncated
      checks {
        code
        currentStatus
        checkedAt
        reasonCode
        publicMessage
        coveragePct
        incidentCount
        points {
          start
          status
          coveragePct
          sampleCount
          passedCount
          standbyCount
          failedCount
          unknownCount
        }
      }
      incidents {
        id
        checkCode
        openedAt
        resolvedAt
        lastConfirmedFailedAt
        active
        observationFresh
        openedReasonCode
        lastReasonCode
        openedMessage
        lastMessage
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
