from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import pytest
import strawberry
from quantx_api.auth.errors import unauthenticated
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.app import AuthenticatedGraphQLRouter
from quantx_api.gqlapi.security import AuthorizationExtension, required_permission
from starlette.websockets import WebSocketState


@strawberry.type
class AuthorizationQuery:
  @strawberry.field
  def current_account(self, account_id: Optional[str] = None) -> str:
    return account_id or "default"

  @strawberry.field
  def instrument(self) -> str:
    return "market-data"


@strawberry.type
class AuthorizationMutation:
  @strawberry.mutation
  def place_order(self) -> bool:
    raise AssertionError("read-only principal must never reach this resolver")


SCHEMA = strawberry.Schema(
  query=AuthorizationQuery,
  mutation=AuthorizationMutation,
  extensions=[AuthorizationExtension],
)


def _principal(*, permissions, accounts=("TEST-ACCOUNT-1",)) -> Principal:
  return Principal(
    user_id="test-user",
    username="test-user",
    display_name="Test User",
    device_session_id="test-session",
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    + timedelta(minutes=5),
    permissions=frozenset(permissions),
    authorized_account_ids=accounts,
  )


def test_agent_device_query_requires_system_status_permission():
  assert required_permission("Query", "agentDevices") == "system-status:read"


@pytest.mark.parametrize(
  "field_name",
  [
    "approveTTradeEntry",
    "rejectTTradeEntry",
    "previewTTradeEntryApproval",
    "confirmTTradeEntryApproval",
    "approveStrategyTradeIntent",
    "rejectStrategyTradeIntent",
    "previewStrategyTradeIntentApproval",
    "confirmStrategyTradeIntentApproval",
    "beginTTradeControlledWindow",
    "activateTTradeLive",
    "pauseTTradeEntries",
    "triggerTTradeKillSwitch",
  ],
)
def test_trade_approval_mutations_require_independent_permission(
  field_name: str,
):
  assert required_permission("Mutation", field_name) == "trade:approve"


def test_unrelated_mutation_keeps_general_write_permission():
  assert required_permission("Mutation", "pauseStrategyInstance") == "mutation:write"


@pytest.mark.parametrize(
  ("operation", "field_name", "permission"),
  [
    ("Query", "portfolioOverview", "portfolio:read"),
    ("Query", "dailyAssetSnapshotsPage", "portfolio:read"),
    ("Query", "latestMarketQuotes", "market:read"),
    ("Query", "limitUpRadar", "market:read"),
    ("Query", "stockScreenSnapshotStatus", "market:read"),
    ("Query", "researchRuns", "market:read"),
    ("Query", "researchRun", "market:read"),
    ("Query", "tTradeBatchesPage", "strategy:read"),
    ("Query", "tTradeBatchEventsPage", "strategy:read"),
    ("Query", "tTradeSignalHistoryPage", "strategy:read"),
    ("Subscription", "tTradeUpdates", "strategy:read"),
  ],
)
def test_new_portfolio_and_t_trade_fields_have_explicit_permissions(
  operation: str,
  field_name: str,
  permission: str,
):
  assert required_permission(operation, field_name) == permission


@pytest.mark.asyncio
async def test_anonymous_graphql_query_is_rejected_with_safe_extensions():
  result = await SCHEMA.execute(
    "{ instrument }",
    context_value={
      "auth_error": unauthenticated(),
      "principal": None,
      "request_id": "request-anonymous",
    },
  )

  assert result.data is None
  assert result.errors[0].extensions == {
    "code": "UNAUTHENTICATED",
    "requestId": "request-anonymous",
    "retryable": False,
  }


@pytest.mark.asyncio
async def test_read_only_principal_cannot_execute_mutation():
  result = await SCHEMA.execute(
    "mutation { placeOrder }",
    context_value={
      "principal": _principal(permissions={"portfolio:read"}),
      "request_id": "request-mutation",
    },
  )

  assert result.data is None
  assert result.errors[0].extensions["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_cross_account_query_is_rejected_before_resolver():
  result = await SCHEMA.execute(
    '{ currentAccount(accountId: "TEST-ACCOUNT-2") }',
    context_value={
      "principal": _principal(permissions={"portfolio:read"}),
      "request_id": "request-account",
    },
  )

  assert result.data is None
  assert result.errors[0].extensions["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_authorized_read_query_succeeds():
  result = await SCHEMA.execute(
    '{ currentAccount(accountId: "TEST-ACCOUNT-1") }',
    context_value={
      "principal": _principal(permissions={"portfolio:read"}),
      "request_id": "request-allowed",
    },
  )

  assert result.errors is None
  assert result.data == {"currentAccount": "TEST-ACCOUNT-1"}


@pytest.mark.asyncio
async def test_slow_resolver_log_contains_only_bounded_operation_metadata(caplog):
  with (
    patch(
      "quantx_api.gqlapi.security.time.perf_counter",
      side_effect=[10.0, 11.5],
    ),
    caplog.at_level("WARNING", logger="quantx_api.gqlapi.security"),
  ):
    result = await SCHEMA.execute(
      """
      query SafetyStatus {
        currentAccount(accountId: "TEST-ACCOUNT-1")
      }
      """,
      context_value={
        "principal": _principal(permissions={"portfolio:read"}),
        "request_id": "request-slow",
      },
    )

  assert result.errors is None
  assert "operation=SafetyStatus" in caplog.text
  assert "type=Query" in caplog.text
  assert "field=currentAccount" in caplog.text
  assert "duration=1.500s" in caplog.text
  assert "request_id=request-slow" in caplog.text
  assert "TEST-ACCOUNT-1" not in caplog.text


@pytest.mark.asyncio
async def test_expired_context_principal_is_rejected():
  principal = _principal(permissions={"market:read"})
  expired = Principal(
    user_id=principal.user_id,
    username=principal.username,
    display_name=principal.display_name,
    device_session_id=principal.device_session_id,
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    - timedelta(seconds=1),
    permissions=principal.permissions,
    authorized_account_ids=principal.authorized_account_ids,
  )
  result = await SCHEMA.execute(
    "{ instrument }",
    context_value={
      "principal": expired,
      "request_id": "request-expired",
    },
  )

  assert result.data is None
  assert result.errors[0].extensions["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_websocket_is_closed_when_access_token_expires():
  class FakeWebSocket:
    client_state = WebSocketState.CONNECTED
    close_code = None
    close_reason = None

    async def close(self, code: int, reason: str):
      self.close_code = code
      self.close_reason = reason
      self.client_state = WebSocketState.DISCONNECTED

  principal = _principal(permissions={"market:read"})
  expiring = Principal(
    user_id=principal.user_id,
    username=principal.username,
    display_name=principal.display_name,
    device_session_id=principal.device_session_id,
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
    permissions=principal.permissions,
    authorized_account_ids=principal.authorized_account_ids,
  )
  websocket = FakeWebSocket()

  await AuthenticatedGraphQLRouter._close_at_access_expiry(websocket, expiring)

  assert websocket.close_code == 4401
  assert websocket.close_reason == "访问令牌已过期"
