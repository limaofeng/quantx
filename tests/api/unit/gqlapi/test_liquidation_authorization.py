from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.security import required_permission
from quantx_api.gqlapi.types.liquidation_types import PositionLiquidationResult
from quantx_infrastructure.services import trade_command_service


@pytest.fixture(autouse=True)
def _disable_real_trading(monkeypatch):
  monkeypatch.setattr(
    trade_command_service.settings,
    "enable_real_trading",
    False,
  )
  monkeypatch.setattr(
    trade_command_service.settings,
    "t_trade_live_enabled",
    False,
  )


def _context(*accounts: str) -> dict:
  principal = Principal(
    user_id="liquidation-user",
    username="liquidation-user",
    display_name="Liquidation User",
    device_session_id="liquidation-session",
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    + timedelta(minutes=5),
    permissions=frozenset({"orders:write"}),
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
    is_native_session=True,
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

  assert result.data is None
  assert result.errors
  error = result.errors[0]
  assert error.path == ["confirmLiquidation"]
  assert error.extensions["code"] == "FORBIDDEN"
  assert "trade:approve" in error.message


def test_liquidation_contract_requires_account_and_defaults_to_paper():
  schema_sdl = schema.as_str()
  input_sdl = schema_sdl.split("input LiquidationPreviewInput {", 1)[1].split(
    "}", 1
  )[0]
  assert "accountId: String!" in input_sdl
  assert "executionMode: LiquidationExecutionMode! = PAPER" in input_sdl


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("resolver_name", "operation", "variables"),
  [
    (
      "liquidate_positions",
      """
      mutation Legacy($input: LiquidatePositionsInput!) {
        liquidatePositions(input: $input) { success }
      }
      """,
      {
        "input": {
          "completionStrategy": "AVAILABLE_NOW",
          "conflictStrategy": "UNALLOCATED_ONLY",
          "confirm": True,
          "scope": "SELECTED",
          "instrumentCodes": ["000001.SZ"],
          "executionMode": "live",
          "autoExitAuthorized": True,
        }
      },
    ),
    (
      "liquidate_position",
      """
      mutation Legacy($input: LiquidatePositionInput!) {
        liquidatePosition(input: $input) { success }
      }
      """,
      {"input": {"stockCode": "000001.SZ", "confirm": True}},
    ),
    (
      "liquidate_all_positions",
      """
      mutation Legacy($input: LiquidateAllPositionsInput!) {
        liquidateAllPositions(input: $input) { success }
      }
      """,
      {"input": {"confirm": True}},
    ),
  ],
)
async def test_native_session_cannot_call_legacy_liquidation_mutations(
  monkeypatch,
  resolver_name,
  operation,
  variables,
):
  called = False

  async def forbidden_resolver(*_args, **_kwargs):
    nonlocal called
    called = True
    raise AssertionError("native session reached legacy liquidation resolver")

  monkeypatch.setattr(LiquidationResolver, resolver_name, forbidden_resolver)
  result = await schema.execute(
    operation,
    variable_values=variables,
    context_value=_native_context("mutation:write"),
  )

  assert not called
  assert result.errors
  assert result.errors[0].extensions["code"] == "FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("execution_mode", "auto_exit_authorized"),
  [("live", False), ("paper", True), ("live", True)],
)
async def test_legacy_web_unsafe_liquidation_fails_before_engine(
  monkeypatch,
  execution_mode,
  auto_exit_authorized,
):
  called = False

  async def forbidden_engine_request(*_args, **_kwargs):
    nonlocal called
    called = True
    raise AssertionError("unsafe legacy liquidation reached Engine")

  monkeypatch.setattr(
    LiquidationResolver,
    "_request_engine",
    forbidden_engine_request,
  )
  result = await schema.execute(
    """
    mutation Legacy($input: LiquidatePositionsInput!) {
      liquidatePositions(input: $input) { success }
    }
    """,
    variable_values={
      "input": {
        "accountId": "AUTHORIZED-ACCOUNT",
        "completionStrategy": "AVAILABLE_NOW",
        "conflictStrategy": "UNALLOCATED_ONLY",
        "confirm": True,
        "scope": "SELECTED",
        "instrumentCodes": ["000001.SZ"],
        "executionMode": execution_mode,
        "autoExitAuthorized": auto_exit_authorized,
      }
    },
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert not called
  assert result.errors
  error = result.errors[0]
  assert error.extensions["code"] == "LEGACY_LIQUIDATION_UNSAFE_MODE"
  assert error.message == "旧清仓接口仅支持 PAPER 且不允许自动卖出授权"
  assert "AUTHORIZED-ACCOUNT" not in error.message
  assert "000001.SZ" not in error.message


@pytest.mark.asyncio
async def test_legacy_web_default_liquidation_keeps_safe_paper_payload(monkeypatch):
  captured = {}

  async def fake_request(command_type, payload, *, aggregate_id):
    captured.update(
      command_type=command_type,
      payload=payload,
      aggregate_id=aggregate_id,
    )
    return {"group_id": "legacy-safe-group", "success": True, "items": []}

  monkeypatch.setattr(LiquidationResolver, "_request_engine", fake_request)
  result = await schema.execute(
    """
    mutation Legacy($input: LiquidatePositionsInput!) {
      liquidatePositions(input: $input) { success }
    }
    """,
    variable_values={
      "input": {
        "accountId": "AUTHORIZED-ACCOUNT",
        "completionStrategy": "AVAILABLE_NOW",
        "conflictStrategy": "UNALLOCATED_ONLY",
        "confirm": True,
        "scope": "SELECTED",
        "instrumentCodes": ["000001.SZ"],
      }
    },
    context_value=_context("AUTHORIZED-ACCOUNT"),
  )

  assert result.errors is None
  assert result.data == {"liquidatePositions": {"success": True}}
  assert captured["command_type"] == "EXIT_PLAN_LIQUIDATE_POSITIONS"
  assert captured["aggregate_id"] == "AUTHORIZED-ACCOUNT"
  assert captured["payload"]["execution_mode"] == "paper"
  assert captured["payload"]["auto_exit_authorized"] is False


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
