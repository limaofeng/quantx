from __future__ import annotations

import pytest
from quantx_application.assistant.contracts import (
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolRisk,
)
from quantx_application.assistant.policies import (
  authorize_tool,
  tool_requires_approval,
)


def _context(**overrides) -> AssistantExecutionContext:
  values = {
    "user_id": "user-1",
    "permissions": frozenset({"assistant:write", "market:read"}),
    "authorized_account_ids": ("account-1",),
    "thread_id": "thread-1",
    "run_id": "run-1",
    "request_id": "request-1",
    "account_id": "account-1",
  }
  values.update(overrides)
  return AssistantExecutionContext(**values)


def test_non_trading_write_always_requires_approval() -> None:
  metadata = AssistantToolMetadata(
    name="create_research_task",
    version="1",
    description="create a non-trading task",
    risk_level=AssistantToolRisk.NON_TRADING_WRITE,
  )

  assert tool_requires_approval(metadata) is True


@pytest.mark.parametrize(
  "risk",
  [AssistantToolRisk.TRADING, AssistantToolRisk.ADMIN],
)
def test_trading_and_admin_tools_are_forbidden(risk: AssistantToolRisk) -> None:
  metadata = AssistantToolMetadata(
    name="forbidden",
    version="1",
    description="forbidden",
    risk_level=risk,
  )

  with pytest.raises(PermissionError, match="tool risk is not available"):
    authorize_tool(_context(), metadata)


def test_tool_authorization_rechecks_permissions_and_account_scope() -> None:
  metadata = AssistantToolMetadata(
    name="portfolio",
    version="1",
    description="portfolio",
    risk_level=AssistantToolRisk.READ,
    required_permissions=frozenset({"portfolio:read"}),
    account_scoped=True,
  )

  with pytest.raises(PermissionError, match="missing permission"):
    authorize_tool(_context(), metadata)

  with pytest.raises(PermissionError, match="account is not authorized"):
    authorize_tool(
      _context(
        permissions=frozenset({"portfolio:read"}),
        account_id="revoked-account",
      ),
      metadata,
    )
