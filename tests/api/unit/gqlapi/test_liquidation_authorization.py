from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.types.liquidation_types import PositionLiquidationResult


def _context(*accounts: str) -> dict:
  principal = Principal(
    user_id="liquidation-user",
    username="liquidation-user",
    display_name="Liquidation User",
    device_session_id="liquidation-session",
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    + timedelta(minutes=5),
    permissions=frozenset({"mutation:write"}),
    authorized_account_ids=accounts,
  )
  return {"principal": principal, "request_id": "liquidation-request"}


@pytest.mark.asyncio
async def test_liquidation_without_account_uses_principal_account(monkeypatch):
  captured = {}

  async def fake_liquidate(input, account_id):
    captured["account_id"] = account_id
    return PositionLiquidationResult(
      success=True,
      stock_code=input.stock_code,
      volume=100,
      order_id="client-order-1",
      message="命令已排队",
      error=None,
    )

  monkeypatch.setattr(LiquidationResolver, "liquidate_position", fake_liquidate)
  result = await schema.execute(
    """
    mutation Liquidate($input: LiquidatePositionInput!) {
      liquidatePosition(input: $input) {
        success
        stockCode
        orderId
      }
    }
    """,
    variable_values={
      "input": {
        "stockCode": "000001.SZ",
        "confirm": True,
      }
    },
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert result.errors is None
  assert captured["account_id"] == "AUTHORIZED-ACCOUNT"


@pytest.mark.asyncio
async def test_liquidation_cross_account_is_rejected_before_resolver(monkeypatch):
  called = False

  async def fake_liquidate(input, account_id):
    nonlocal called
    called = True
    raise AssertionError((input, account_id))

  monkeypatch.setattr(LiquidationResolver, "liquidate_position", fake_liquidate)
  result = await schema.execute(
    """
    mutation Liquidate($input: LiquidatePositionInput!) {
      liquidatePosition(input: $input) {
        success
      }
    }
    """,
    variable_values={
      "input": {
        "stockCode": "000001.SZ",
        "confirm": True,
        "accountId": "OTHER-ACCOUNT",
      }
    },
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert not called
  assert result.errors
  assert result.errors[0].extensions["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_conditional_order_owner_is_authorized_before_update(monkeypatch):
  called = False

  async def fake_owner(order_id):
    assert order_id == "condition-1"
    return "OTHER-ACCOUNT"

  async def fake_set(order_id, enabled, account_id):
    nonlocal called
    called = True
    raise AssertionError((order_id, enabled, account_id))

  monkeypatch.setattr(
    LiquidationResolver,
    "conditional_order_account_id",
    fake_owner,
  )
  monkeypatch.setattr(
    LiquidationResolver,
    "set_conditional_liquidation_order_enabled",
    fake_set,
  )
  result = await schema.execute(
    """
    mutation {
      setConditionalLiquidationOrderEnabled(
        orderId: "condition-1"
        enabled: false
      ) {
        id
      }
    }
    """,
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert not called
  assert result.errors
  assert result.errors[0].extensions["code"] == "FORBIDDEN"
