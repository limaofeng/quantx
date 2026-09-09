from datetime import timedelta
from decimal import Decimal
from inspect import Parameter, signature
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi import trade_approval
from quantx_api.gqlapi.schemas.trading_schema import _manual_command_owner_id
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_infrastructure.auth.tokens import utcnow
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)


def _principal() -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id="device-1",
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset({"trade:approve"}),
    authorized_account_ids=("ACCOUNT-1",),
  )


def test_manual_command_owner_is_stable_across_request_payload_retries():
  owner_id = _manual_command_owner_id(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key="command-1",
    environment=ExecutionEnvironment.PAPER,
  )
  retry_owner_id = _manual_command_owner_id(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key="command-1",
    environment=ExecutionEnvironment.PAPER,
  )

  first_ref = ExecutionOwnerRef.manual_command(owner_id)
  retry_ref = ExecutionOwnerRef.manual_command(retry_owner_id)
  assert first_ref == retry_ref
  assert TradeCommandService.order_idempotency_digest(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key="command-1",
    execution_ref=first_ref,
    environment=ExecutionEnvironment.PAPER,
  ) == TradeCommandService.order_idempotency_digest(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key="command-1",
    execution_ref=retry_ref,
    environment=ExecutionEnvironment.PAPER,
  )
  assert owner_id != _manual_command_owner_id(
    user_id="user-1",
    account_id="ACCOUNT-1",
    idempotency_key="command-1",
    environment=ExecutionEnvironment.LIVE,
  )


def test_trade_command_enqueue_api_has_no_legacy_identity_parameters():
  for method_name in ("enqueue_order", "enqueue_order_for_account"):
    parameters = signature(getattr(TradeCommandService, method_name)).parameters
    assert "strategy_name" not in parameters
    assert "order_remark" not in parameters
    assert parameters["idempotency_key"].default is Parameter.empty


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "idempotency_key,trace_id",
  [
    ("", ""),
    ("   ", ""),
    ("x" * 129, ""),
    ("", "x" * 129),
  ],
)
async def test_enqueue_rejects_missing_or_invalid_stable_key_before_persistence(
  idempotency_key, trace_id
):
  db = SimpleNamespace(values=[])
  service = TradeCommandService(db)

  with pytest.raises(AgentUnavailableError, match="(REQUIRED|INVALID)"):
    await service.enqueue_order(
      user_id="user-1",
      account_id="ACCOUNT-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      execution_ref=ExecutionOwnerRef.manual_command("manual-command-1"),
      environment=ExecutionEnvironment.PAPER,
      idempotency_key=idempotency_key,
      trace_id=trace_id,
    )

  assert db.values == []


@pytest.mark.asyncio
async def test_manual_idempotency_key_rejects_changed_payload_without_second_command():
  class Result:
    def __init__(self, value):
      self.value = value

    def scalar_one_or_none(self):
      return self.value

  class Database:
    def __init__(self):
      self.values = []

    async def execute(self, _statement):
      return Result(next(
        (
          value
          for value in self.values
          if isinstance(value, TradeCommandOutbox)
        ),
        None,
      ))

    def add(self, value):
      self.values.append(value)

    async def flush(self):
      return None

  db = Database()
  service = TradeCommandService(db)
  service._device_for = AsyncMock(return_value=SimpleNamespace(id="agent-1"))
  service._require_durable_order_intent = AsyncMock(return_value=None)
  owner = ExecutionOwnerRef.manual_command(
    _manual_command_owner_id(
      user_id="user-1",
      account_id="ACCOUNT-1",
      idempotency_key="command-1",
      environment=ExecutionEnvironment.PAPER,
    )
  )

  first = await service.enqueue_order(
    user_id="user-1",
    account_id="ACCOUNT-1",
    instrument_code="600000.SH",
    side="BUY",
    order_type="FIX_PRICE",
    limit_price=Decimal("10"),
    volume=100,
    execution_ref=owner,
    environment=ExecutionEnvironment.PAPER,
    idempotency_key="command-1",
    commit_transaction=False,
  )

  with pytest.raises(AgentUnavailableError, match="同一幂等键对应不同交易请求"):
    await service.enqueue_order(
      user_id="user-1",
      account_id="ACCOUNT-1",
      instrument_code="600001.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("11"),
      volume=200,
      execution_ref=owner,
      environment=ExecutionEnvironment.PAPER,
      idempotency_key="command-1",
      commit_transaction=False,
    )

  assert sum(isinstance(value, TradeCommandOutbox) for value in db.values) == 1
  assert sum(isinstance(value, PendingTradeOrder) for value in db.values) == 1
  assert first.client_order_id == next(
    value.client_order_id
    for value in db.values
    if isinstance(value, TradeCommandOutbox)
  )


def _challenge(*, owner_type: str, owner_id: str, environment: str):
  token = "confirmation-token"
  payload = {
    "action": "LIQUIDATION_GROUP",
    "account_id": "ACCOUNT-1",
    "group_id": "group-1",
    "execution_mode": "PAPER",
  }
  challenge = SimpleNamespace(
    action="LIQUIDATION_GROUP",
    user_id="user-1",
    device_session_id="device-1",
    account_id="ACCOUNT-1",
    owner_type=owner_type,
    owner_id=owner_id,
    environment=environment,
    token_digest=None,
    payload_fingerprint=None,
    expires_at=utcnow() + timedelta(minutes=1),
    consumed_at=None,
  )
  challenge.token_digest = trade_approval.challenge_token_digest(token)
  challenge.payload_fingerprint = trade_approval.signed_payload_fingerprint(payload)
  return challenge, payload, token


def test_liquidation_group_requires_exact_manual_owner_and_environment(monkeypatch):
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="test-trade-approval-signing-key-at-least-32-bytes",
      algorithm="HS256",
    ),
  )
  challenge, payload, token = _challenge(
    owner_type="MANUAL_COMMAND",
    owner_id="group-1",
    environment="PAPER",
  )

  trade_approval.validate_persistent_trade_challenge(
    challenge=challenge,
    principal=_principal(),
    action="LIQUIDATION_GROUP",
    confirmation_token=token,
    now=utcnow(),
    payload=payload,
  )

  challenge.environment = "LIVE"
  with pytest.raises(
    trade_approval.TradeApprovalChallengeError,
    match="执行归属或环境已变化",
  ):
    trade_approval.validate_persistent_trade_challenge(
      challenge=challenge,
      principal=_principal(),
      action="LIQUIDATION_GROUP",
      confirmation_token=token,
      now=utcnow(),
      payload=payload,
    )


@pytest.mark.asyncio
async def test_runtime_event_writes_owner_only_to_typed_columns():
  from quantx_api import agent_api

  owner = ExecutionOwnerRef.manual_command("manual-command-1")
  command = SimpleNamespace(
    message_id="message-1",
    client_order_id="client-order-1",
    owner_type=owner.owner_type.value,
    owner_id=owner.owner_id,
    environment=ExecutionEnvironment.PAPER.value,
  )
  pending = SimpleNamespace(
    client_order_id="client-order-1",
    account_id="ACCOUNT-1",
    owner_type=owner.owner_type.value,
    owner_id=owner.owner_id,
    environment=ExecutionEnvironment.PAPER.value,
    strategy_run_id=None,
    intent_id=None,
    instrument_code="600000.SH",
    side="BUY",
    volume=100,
    limit_price="10",
    batch_id=None,
    bucket="manual",
    t_trade_role=None,
    status="QUEUED",
  )
  correlation = SimpleNamespace(
    client_order_id="client-order-1",
    account_id="ACCOUNT-1",
    owner_type=owner.owner_type.value,
    owner_id=owner.owner_id,
    environment=ExecutionEnvironment.PAPER.value,
    strategy_run_id=None,
    strategy_order_id=None,
    intent_id=None,
    batch_id=None,
    bucket="manual",
    t_trade_role=None,
    risk_decision_id=None,
    trace_id="trace-1",
    substitution_plan=None,
    broker_order_id=None,
    request_metadata={
      "origin": "manual",
      "owner_type": "STALE_OWNER",
      "owner_id": "stale-owner",
      "environment": "LIVE",
      "strategy_run_id": "stale-run",
    },
  )

  class Nested:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

  class Database:
    def __init__(self):
      self.events = []

    async def scalar(self, _statement):
      return None

    def begin_nested(self):
      return Nested()

    def add(self, value):
      self.events.append(value)

    async def flush(self):
      return None

  db = Database()
  assert await agent_api._stage_command_runtime_event(
    db,
    command=command,
    pending=pending,
    correlation=correlation,
    status="REJECTED",
    reason="manual rejection",
    now=utcnow(),
  )
  assert len(db.events) == 1
  event = db.events[0]
  assert isinstance(event, StrategyRuntimeEvent)
  assert (
    event.owner_type,
    event.owner_id,
    event.environment,
  ) == (owner.owner_type.value, owner.owner_id, ExecutionEnvironment.PAPER.value)
  metadata = event.payload["metadata"]
  assert metadata["origin"] == "manual"
  assert all(
    key not in metadata
    for key in ("owner_type", "owner_id", "environment", "strategy_run_id")
  )
