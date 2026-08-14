from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.security import required_permission
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


def _native_context(*permissions: str) -> dict:
  principal = Principal(
    user_id="liquidation-user",
    username="liquidation-user",
    display_name="Liquidation User",
    device_session_id="liquidation-session",
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    + timedelta(minutes=5),
    permissions=frozenset(permissions),
    authorized_account_ids=("AUTHORIZED-ACCOUNT",),
    active_account_id="AUTHORIZED-ACCOUNT",
  )
  return {"principal": principal, "request_id": "liquidation-native-request"}


@pytest.mark.parametrize(
  "field_name",
  ["previewLiquidation", "confirmLiquidation"],
)
def test_native_liquidation_mutations_use_dedicated_permission(field_name):
  assert required_permission("Mutation", field_name) == "liquidation:control"


@pytest.mark.asyncio
async def test_confirm_liquidation_explicitly_requires_trade_approve():
  result = await schema.execute(
    """
    mutation Confirm($input: LiquidationConfirmationInput!) {
      confirmLiquidation(input: $input) { success code }
    }
    """,
    variable_values={
      "input": {
        "challengeId": "challenge-1",
        "confirmationToken": "token-1",
      }
    },
    context_value=_native_context("liquidation:control"),
  )

  assert result.errors is None
  assert result.data == {
    "confirmLiquidation": {"success": False, "code": "FORBIDDEN"}
  }


def test_liquidation_contract_requires_account_and_defaults_to_paper():
  schema_sdl = schema.as_str()
  input_sdl = schema_sdl.split("input LiquidationPreviewInput {", 1)[1].split(
    "}", 1
  )[0]
  assert "accountId: String!" in input_sdl
  assert "executionMode: LiquidationExecutionMode! = PAPER" in input_sdl


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
