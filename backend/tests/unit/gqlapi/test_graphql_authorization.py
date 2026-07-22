from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
import strawberry

from auth.errors import unauthenticated
from auth.principal import Principal
from gqlapi.security import AuthorizationExtension


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
