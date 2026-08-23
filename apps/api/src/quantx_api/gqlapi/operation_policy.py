"""Explicit public policy metadata for every GraphQL root operation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

Audience = str
Risk = str
Stability = str

ALL_CLIENTS: Final = ("web", "native", "third-party")
WEB_ONLY: Final = ("web",)
WEB_AND_NATIVE: Final = ("web", "native")


@dataclass(frozen=True)
class GraphQLOperationPolicy:
  required_permissions: tuple[str, ...]
  audiences: tuple[Audience, ...]
  stability: Stability
  risk: Risk


def normalize_field_name(value: str) -> str:
  return re.sub(r"[^a-z0-9]", "", value.lower())


_POLICIES: dict[tuple[str, str], GraphQLOperationPolicy] = {}


def _register(
  operation: str,
  permission: str,
  fields: set[str],
  *,
  audiences: tuple[Audience, ...] = ALL_CLIENTS,
  stability: Stability = "supported",
  risk: Risk = "READ",
) -> None:
  for field in fields:
    key = (operation, normalize_field_name(field))
    if key in _POLICIES:
      raise RuntimeError(f"duplicate GraphQL operation policy: {operation}.{field}")
    _POLICIES[key] = GraphQLOperationPolicy(
      required_permissions=(permission,),
      audiences=audiences,
      stability=stability,
      risk=risk,
    )


_register(
  "Query",
  "portfolio:read",
  {
    "account",
    "closedPositionCycles",
    "currentAccount",
    "dailyAssetSnapshots",
    "dailyAssetSnapshotsPage",
    "portfolioOverview",
    "portfolioSummary",
    "position",
    "positions",
    "watchlist",
  },
)
_register(
  "Query",
  "orders:read",
  {
    "conditionalLiquidationOrders",
    "exitPlan",
    "exitPlanCapabilities",
    "exitPlanCostBasisCandidates",
    "exitPlanEvents",
    "exitPlanHoldingCapacity",
    "exitPlans",
    "historyOrders",
    "historyTrades",
    "liquidationOrder",
    "liquidationOrders",
    "liquidationSummary",
    "order",
    "redemptionRecords",
    "todayOrders",
    "todayTrades",
    "trade",
  },
)
_register(
  "Query",
  "strategy:read",
  {
    "backtestHistory",
    "entryAutomationStatus",
    "entryPlan",
    "entryPlanCapabilities",
    "entryPlanEvents",
    "entryPlans",
    "firstBoardPromotionDesk",
    "limitUpBoardAssistant",
    "limitUpBoardReplay",
    "limitUpBoardReplayCurve",
    "limitUpBoardReplayHistory",
    "limitUpBoardReplayPreparation",
    "limitUpBoardReplayTrades",
    "accountExecutionSafety",
    "operationalAlerts",
    "strategies",
    "strategy",
    "strategyBucketLedger",
    "strategyDecisionHistory",
    "strategyDefinitions",
    "strategyExecutionLogs",
    "strategyExecutionTrace",
    "strategyExitPlans",
    "strategyGridBook",
    "strategyInstance",
    "strategyInstanceMobileParameters",
    "strategyInstances",
    "strategyPendingTradeIntents",
    "strategyPerformance",
    "strategyRun",
    "strategyRuns",
    "pendingEntryIntents",
    "tTradeBatchEvents",
    "tTradeBatchEventsPage",
    "tTradeBatches",
    "tTradeBatchesPage",
    "tTradeCandidateTrace",
    "tTradeGlobalMonitor",
    "tTradeImportedEntries",
    "tTradeReplay",
    "tTradeReplayCycles",
    "tTradeReplayHistory",
    "tTradeReplayPreparation",
    "tTradeSession",
    "tTradeSessions",
    "tTradeSignalDiagnostics",
    "tTradeSignalEvaluations",
    "validateTTradeLiveReadiness",
  },
)
_register(
  "Query",
  "market:read",
  {
    "dividFactors",
    "financialOverview",
    "financialReports",
    "financialStatements",
    "financialSummary",
    "holidays",
    "instrument",
    "instruments",
    "instrumentsConnection",
    "intradayVolumeScreen",
    "klines",
    "klinesPage",
    "latestMarketQuotes",
    "limitUpLifecycle",
    "limitUpRadar",
    "orderEntryCapabilities",
    "researchRun",
    "researchRuns",
    "rootSectors",
    "sector",
    "sectorStats",
    "sectors",
    "stockDisclosureSummary",
    "stockScreen",
    "stockScreenSnapshotStatus",
    "stockSectors",
    "stockSignalSnapshotMeta",
    "ticks",
    "tradingCalendar",
  },
)
_register(
  "Query",
  "system-status:read",
  {
    "qmtAgentConnection",
    "aiRuntimeSettings",
    "flowRun",
    "flowRuns",
    "getDeploymentById",
    "getDeploymentByName",
    "intradayWarmCacheStatus",
    "listDeployments",
  },
  audiences=WEB_ONLY,
  stability="web-internal",
)
_register(
  "Query",
  "assistant:read",
  {
    "aiAssistantCapabilities",
    "aiAssistantMessages",
    "aiAssistantThread",
    "aiAssistantThreads",
  },
  audiences=WEB_AND_NATIVE,
  stability="experimental",
)

_register(
  "Query",
  "notification:manage",
  {"notificationEventRoute"},
  audiences=WEB_AND_NATIVE,
)

_register(
  "Mutation",
  "watchlist:write",
  {
    "addWatchlistItem",
    "removeWatchlistItem",
    "reorderWatchlist",
    "replaceWatchlist",
  },
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "strategy:read",
  {"recordTTradeClientTelemetry"},
  audiences=WEB_AND_NATIVE,
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "market:write",
  {"addHoliday", "bulkSaveHolidays", "deleteHoliday", "refreshStockDisclosures"},
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "orders:write",
  {
    "cancelConditionalLiquidationOrder",
    "cancelExitPlan",
    "cancelLiquidationOrder",
    "confirmExitIntent",
    "createManualExitPlan",
    "evaluateConditionalLiquidationOrders",
    "evaluateExitPlanNow",
    "liquidateAllPositions",
    "liquidatePosition",
    "liquidatePositions",
    "placeOrder",
    "previewExitIntent",
    "reconcileExitPlanCapacity",
    "redeemClearedPosition",
    "rejectExitIntent",
    "setConditionalLiquidationOrderEnabled",
    "setExitPlanEnabled",
    "updateManualExitPlan",
    "upsertConditionalLiquidationOrder",
  },
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "strategy:write",
  {
    "activateTTradeLive",
    "approveStrategyTradeIntent",
    "approveTTradeEntry",
    "archiveStrategyInstance",
    "beginTTradeControlledWindow",
    "cancelLimitUpBoardReplay",
    "cloneStrategy",
    "cloneStrategyInstance",
    "createStrategyInstance",
    "createStrategyRun",
    "deleteBacktestVersion",
    "deleteStrategyRun",
    "importTTradeExternalEntry",
    "pauseStrategy",
    "pauseStrategyRun",
    "rerunBacktestVersion",
    "restartStrategy",
    "resumeStrategy",
    "resumeStrategyRun",
    "runStrategy",
    "startStrategy",
    "startStrategyRun",
    "startLimitUpBoardReplay",
    "stopStrategy",
    "stopStrategyRun",
    "syncTTradeSourceOrders",
    "triggerTTradeKillSwitch",
    "updateStrategyGridBook",
    "updateStrategyRun",
  },
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "strategy:write",
  {"cancelTTradeReplay", "startTTradeReplay"},
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "trade:manual",
  {"cancelOrder", "confirmManualOrder", "previewManualOrder"},
  audiences=WEB_AND_NATIVE,
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "strategy:control",
  {
    "confirmStrategyControl",
    "cancelEntryPlan",
    "confirmEntryIntent",
    "confirmEntryPlanAuthorization",
    "createEntryPlan",
    "evaluateEntryPlanNow",
    "confirmStrategyTradeIntentApproval",
    "pauseStrategyInstance",
    "previewStrategyControl",
    "previewEntryIntent",
    "previewEntryPlanAuthorization",
    "rejectEntryIntent",
    "previewStrategyTradeIntentApproval",
    "rejectStrategyTradeIntent",
    "resumeStrategyInstance",
    "setEntryAutomationPaused",
    "setEntryPlanEnabled",
    "triggerEntryPlanManualRule",
    "updateEntryPlan",
    "updateStrategyInstanceParameters",
  },
  audiences=WEB_AND_NATIVE,
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "t-trade:control",
  {
    "acknowledgeOperationalAlert",
    "cancelTTradeOrder",
    "confirmTTradeControl",
    "confirmTTradeEntryApproval",
    "pauseTTradeEntries",
    "previewTTradeControl",
    "previewTTradeEntryApproval",
    "previewTTradeSignalPolicy",
    "reconcileTTradeGlobalMonitor",
    "rejectTTradeEntry",
    "resolveOperationalAlert",
    "saveTTradeGlobalMonitor",
    "stopTTradeSession",
  },
  audiences=WEB_AND_NATIVE,
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "limit-up:control",
  {
    "armLimitUpBoardCandidate",
    "disarmLimitUpBoardCandidate",
    "reconcileLimitUpBoardAssistant",
    "saveFirstBoardAssistant",
    "saveLimitUpBoardAssistant",
    "setFirstBoardCandidatePreference",
  },
  audiences=WEB_AND_NATIVE,
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "liquidation:control",
  {
    "confirmExitPlanAuthorization",
    "confirmLiquidation",
    "previewExitPlanAuthorization",
    "previewLiquidation",
  },
  audiences=WEB_AND_NATIVE,
  risk="TRADING_WRITE",
)
_register(
  "Mutation",
  "notification:manage",
  {"registerPushDevice", "unregisterPushDevice", "updatePushPreferences"},
  audiences=WEB_AND_NATIVE,
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "operations:write",
  {
    "cancelFlowRun",
    "retryFlowRun",
    "runDeployment",
    "setDeploymentScheduleActive",
  },
  audiences=WEB_ONLY,
  stability="web-internal",
  risk="ADMIN",
)
_register(
  "Mutation",
  "agent:manage",
  {
    "cancelAgentHandover",
    "createAgentEnrollment",
    "revokeAgentDevice",
  },
  audiences=WEB_ONLY,
  stability="web-internal",
  risk="ADMIN",
)
_register(
  "Mutation",
  "assistant:write",
  {
    "cancelAiAssistantRun",
    "createAiAssistantThread",
    "deleteAiAssistantThread",
    "resolveAiAssistantApproval",
    "retryAiAssistantRun",
    "sendAiAssistantMessage",
    "updateAiAssistantThread",
  },
  audiences=WEB_AND_NATIVE,
  stability="experimental",
  risk="NON_TRADING_WRITE",
)
_register(
  "Mutation",
  "system-config:write",
  {"updateAiRuntimeSettings"},
  audiences=WEB_ONLY,
  stability="web-internal",
  risk="ADMIN",
)

_TRADE_APPROVAL_FIELDS = {
  "activateTTradeLive",
  "approveStrategyTradeIntent",
  "approveTTradeEntry",
  "beginTTradeControlledWindow",
  "confirmExitIntent",
  "confirmExitPlanAuthorization",
  "confirmEntryIntent",
  "confirmEntryPlanAuthorization",
  "confirmLiquidation",
  "confirmStrategyControl",
  "confirmStrategyTradeIntentApproval",
  "confirmTTradeControl",
  "confirmTTradeEntryApproval",
  "previewExitIntent",
  "previewEntryIntent",
  "previewStrategyControl",
  "previewStrategyTradeIntentApproval",
  "previewTTradeControl",
  "previewTTradeEntryApproval",
  "triggerTTradeKillSwitch",
  "triggerEntryPlanManualRule",
}
for _field in _TRADE_APPROVAL_FIELDS:
  _key = ("Mutation", normalize_field_name(_field))
  _policy = _POLICIES[_key]
  _POLICIES[_key] = replace(
    _policy,
    required_permissions=(*_policy.required_permissions, "trade:approve"),
  )

_register(
  "Subscription",
  "market:read",
  {"marketDepth", "marketKlines", "marketQuotes", "marketTicks"},
)
_register("Subscription", "orders:read", {"exitPlanUpdates", "tradingEvents"})
_register(
  "Subscription",
  "strategy:read",
  {
    "firstBoardPromotionUpdates",
    "entryIntentUpdated",
    "entryPlanUpdated",
    "limitUpBoardAssistantUpdates",
    "limitUpBoardReplayUpdates",
    "strategyInstanceEvents",
    "strategyKlines",
    "strategyLogs",
    "strategyTicks",
    "tTradeReplayUpdates",
    "tTradeUpdates",
  },
)
_register(
  "Subscription",
  "system-status:read",
  {"deploymentStatus", "flowRunLogs", "systemAlerts", "systemStrategies"},
  audiences=WEB_ONLY,
  stability="web-internal",
)
_register(
  "Subscription",
  "assistant:read",
  {"aiAssistantEvents"},
  audiences=WEB_AND_NATIVE,
  stability="experimental",
)


def operation_policy(operation_name: str, field_name: str) -> GraphQLOperationPolicy:
  key = (operation_name, normalize_field_name(field_name))
  try:
    return _POLICIES[key]
  except KeyError:
    raise RuntimeError(
      f"GraphQL root field has no explicit policy: {operation_name}.{field_name}"
    ) from None


def operation_policy_keys() -> frozenset[tuple[str, str]]:
  """Return normalized policy keys for schema completeness tests."""
  return frozenset(_POLICIES)
