"""Hard assistant safety policy independent from any model provider."""

from __future__ import annotations

from .contracts import (
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolRisk,
)

FORBIDDEN_TOOL_RISKS = frozenset({AssistantToolRisk.TRADING, AssistantToolRisk.ADMIN})


def tool_requires_approval(metadata: AssistantToolMetadata) -> bool:
  return metadata.risk_level is AssistantToolRisk.NON_TRADING_WRITE


def authorize_tool(
  context: AssistantExecutionContext,
  metadata: AssistantToolMetadata,
) -> None:
  if metadata.risk_level in FORBIDDEN_TOOL_RISKS:
    raise PermissionError(f"tool risk is not available: {metadata.risk_level}")
  for permission in metadata.required_permissions:
    context.require_permission(permission)
  if metadata.account_scoped:
    context.require_account()
