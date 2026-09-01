import { gql } from '@/generated/gql';

export const ManualOrderCapabilitiesQuery = gql(`
  query Trading_ManualOrderCapabilities(
    $instrumentCode: String!
    $accountId: String!
  ) {
    orderEntryCapabilities(
      instrumentCode: $instrumentCode
      accountId: $accountId
    ) {
      accountId
      instrumentCode
      canManualTrade
      defaultExecutionMode
      executionModes
      supportedSides
      supportedPriceTypes
      canLiveBuy
      canLiveSell
      liveReady
      liveBlockedReasons
      warnings
    }
  }
`);

export const PreviewManualOrderMutation = gql(`
  mutation Trading_PreviewManualOrder($input: ManualOrderPreviewInput!) {
    previewManualOrder(input: $input) {
      success
      code
      message
      preview {
        challengeId
        confirmationToken
        accountId
        instrumentCode
        side
        priceType
        requestedVolume
        finalVolume
        limitPrice
        referencePrice
        estimatedAmount
        estimatedFees
        availableCash
        availableVolume
        idempotencyKey
        executionMode
        quoteTimestamp
        challengeExpiresAt
        riskDecisionId
        riskAction
        riskReasonCode
        riskReasonDetail
        warnings
      }
    }
  }
`);

export const ConfirmManualOrderMutation = gql(`
  mutation Trading_ConfirmManualOrder(
    $input: ManualOrderConfirmationInput!
  ) {
    confirmManualOrder(input: $input) {
      success
      code
      message
      challengeId
      clientOrderId
      status
    }
  }
`);

export const ManualOrderAttemptsQuery = gql(`
  query Trading_ManualOrderAttempts($accountId: String, $limit: Int = 50) {
    manualOrderAttempts(accountId: $accountId, limit: $limit) {
      items {
        accountId
        clientOrderId
        brokerOrderId
        instrumentCode
        side
        orderType
        limitPrice
        volume
        executionMode
        phase
        active
        requiresAttention
        status
        deliveryStatus
        statusReason
        message
        createdAt
        deliveredAt
        acknowledgedAt
        expiresAt
        updatedAt
      }
      totalCount
      activeCount
      requiresAttentionCount
      truncated
      asOf
    }
  }
`);
