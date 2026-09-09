from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.resolvers import liquidation as resolver_module
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.types import MessageResponse
from quantx_infrastructure.auth.tokens import utcnow


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "permission,account,allowed",
  [
    ("orders:write", "account-1", True),
    ("orders:read", "account-1", False),
    ("orders:write", "other", False),
  ],
)
async def test_delete_history_enforces_write_permission_and_account(
  monkeypatch, permission, account, allowed
):
  delete = AsyncMock(return_value=MessageResponse(success=True, message="deleted"))
  monkeypatch.setattr(
    LiquidationResolver, "exit_plan_account_id", AsyncMock(return_value="account-1")
  )
  monkeypatch.setattr(LiquidationResolver, "delete_exit_plan_history", delete)
  principal = Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id="session-1",
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset({permission}),
    authorized_account_ids=(account,),
    is_native_session=False,
  )
  result = await schema.execute(
    'mutation { deleteExitPlanHistory(planId: "plan-1") { success message } }',
    context_value={"principal": principal, "request_id": "history-delete"},
  )
  if allowed:
    assert result.errors is None
    assert result.data["deleteExitPlanHistory"]["success"] is True
    delete.assert_awaited_once_with(plan_id="plan-1", account_id="account-1")
  else:
    assert result.errors
    delete.assert_not_awaited()
  assert (
    operation_policy("Mutation", "deleteExitPlanHistory").risk == "NON_TRADING_WRITE"
  )


@pytest.mark.asyncio
async def test_resolver_uses_authorized_repository_delete_without_engine_command(
  monkeypatch,
):
  db = object()

  async def sessions():
    yield db

  delete = AsyncMock()

  class Repository:
    def __init__(self, session):
      assert session is db

    delete_history = delete

  engine = AsyncMock()
  monkeypatch.setattr(resolver_module, "get_async_db", sessions)
  monkeypatch.setattr(resolver_module, "AutoExitPlanRepository", Repository)
  monkeypatch.setattr(LiquidationResolver, "_request_engine", engine)
  result = await LiquidationResolver.delete_exit_plan_history(
    plan_id="plan-1", account_id="account-1"
  )
  assert result.success
  delete.assert_awaited_once_with(plan_id="plan-1", account_id="account-1")
  engine.assert_not_awaited()
