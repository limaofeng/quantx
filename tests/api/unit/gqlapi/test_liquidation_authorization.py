from datetime import datetime, timedelta, timezone

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.security import required_permission
from quantx_api.gqlapi.trade_approval import (
  EXIT_PLAN_SELL_APPROVAL,
  TradeApprovalChallengeService,
  TradeApprovalDispatchData,
)
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
async def test_confirm_exit_intent_binds_durable_challenge_to_engine_command(
  monkeypatch,
):
  captured = {}

  async def exit_plan_account_id(plan_id):
    assert plan_id == "exit-plan-1"
    return "AUTHORIZED-ACCOUNT"

  async def consume(**kwargs):
    captured.update(kwargs)
    return TradeApprovalDispatchData(
      challenge_id="challenge-1",
      message_id="message-1",
      idempotency_key="exit-plan-confirm:challenge-1",
    )

  async def existing_engine_request(message_id, command_type):
    assert message_id == "message-1"
    assert command_type == "EXIT_PLAN_CONFIRM_INTENT"
    return {
      "success": True,
      "code": "APPROVED",
      "message": "卖出意图已确认",
    }

  async def forbidden_legacy_confirm(**_kwargs):
    raise AssertionError("legacy exit-plan confirm attempted a second enqueue")

  monkeypatch.setattr(
    LiquidationResolver,
    "exit_plan_account_id",
    exit_plan_account_id,
  )
  monkeypatch.setattr(TradeApprovalChallengeService, "consume", consume)
  monkeypatch.setattr(
    LiquidationResolver,
    "_existing_engine_request",
    existing_engine_request,
  )
  monkeypatch.setattr(
    LiquidationResolver,
    "confirm_exit_intent",
    forbidden_legacy_confirm,
  )

  result = await schema.execute(
    """
    mutation {
      confirmExitIntent(
        planId: "exit-plan-1"
        intentId: "exit-intent-1"
        confirmationToken: "confirmation-token-1"
      ) {
        success
        code
        challengeId
      }
    }
    """,
    context_value=_native_context("orders:write", "trade:approve"),
  )

  assert result.errors is None
  assert result.data == {
    "confirmExitIntent": {
      "success": True,
      "code": "APPROVED",
      "challengeId": "challenge-1",
    }
  }
  assert captured["action"] == EXIT_PLAN_SELL_APPROVAL
  assert captured["account_id"] == "AUTHORIZED-ACCOUNT"
  assert captured["business_owner_id"] == "exit-plan-1"
  assert captured["intent_id"] == "exit-intent-1"
  assert captured["confirmation_token"] == "confirmation-token-1"
  assert captured["command_type"] == "EXIT_PLAN_CONFIRM_INTENT"
  assert captured["command_aggregate_id"] == (
    "AUTHORIZED-ACCOUNT:exit-plan-1"
  )
  assert captured["command_idempotency_key_factory"]("challenge-1") == (
    "exit-plan-confirm:challenge-1"
  )
  assert captured["command_payload"] == {
    "plan_id": "exit-plan-1",
    "intent_id": "exit-intent-1",
    "account_id": "AUTHORIZED-ACCOUNT",
    "approval_audit": {
      "actor_id": "liquidation-user",
      "device_session_id": "liquidation-session",
      "channel": "EXIT_PLAN_DEVICE_CHALLENGE",
    },
  }
  assert captured["return_command_reference"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("resolver_name", "operation", "variables"),
  [
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


def test_legacy_group_liquidation_contract_is_removed():
  rendered = schema.as_str()

  assert "liquidatePositions" not in rendered
  assert "LiquidatePositionsInput" not in rendered


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
