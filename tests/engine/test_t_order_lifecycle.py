from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_order_policy import (
  TOrderPolicyDecision,
  TOrderPolicyResult,
)
from quantx_engine import t_order_lifecycle as runtime
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import trade_command_service as commands
from quantx_infrastructure.services.t_order_lifecycle_state import (
  t_order_lifecycle_active,
  t_order_lifecycle_pending,
)


def order(**changes):
  fields = dict(
    client_order_id="client-0", account_id="account", user_id="user",
    owner_type="EXIT_PLAN", owner_id="plan", environment="LIVE",
    instrument_code="600000.SH", side="SELL", status="ACCEPTED",
    created_at=datetime(2026, 9, 4, 2), t_trade_role="EXIT",
    t_order_original_created_at=datetime(2026, 9, 4, 2), t_order_attempt=0,
    broker_order_id="42", intent_id="intent", batch_id="batch", bucket="swing",
    strategy_run_id=None, strategy_order_id=None, trace_id="trace", volume=200,
    risk_decision_id="old-risk", substitution_plan=None,
    request_metadata={"t_exit_order_policy_version": "TExitOrderPolicy.v1"},
  )
  return SimpleNamespace(**{**fields, **changes})


def test_deadlines_use_utc_and_keep_unresolved_obligations():
  pending = order(t_trade_role="ENTRY")
  assert runtime.expiry_reason(pending, pending.created_at + timedelta(seconds=29)) is None
  assert runtime.expiry_reason(pending, pending.created_at + timedelta(seconds=30)) == "T_ENTRY_ORDER_EXPIRED"
  assert t_order_lifecycle_active(pending, pending.created_at + timedelta(seconds=59))
  assert not t_order_lifecycle_active(pending, pending.created_at + timedelta(seconds=60))
  assert t_order_lifecycle_pending(pending)
  pending.request_metadata["t_order_lifecycle_finished"] = True
  assert not t_order_lifecycle_pending(pending)


def test_entry_cutoff_cancels_even_before_individual_ttl():
  pending = order(t_trade_role="ENTRY", created_at=datetime(2026, 9, 4, 6, 49, 55))
  assert runtime.expiry_reason(pending, datetime(2026, 9, 4, 6, 50)) == "T_ENTRY_CUTOFF_REACHED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,attempt,created_seconds,total_seconds", [
  ("ENTRY", 1, 50, 60), ("EXIT", 2, 80, 90),
])
async def test_working_replacement_is_cancelled_at_original_total_deadline(
  monkeypatch, role, attempt, created_seconds, total_seconds,
):
  original = datetime(2026, 9, 4, 2)
  pending = order(t_trade_role=role, t_order_attempt=attempt,
                  created_at=original + timedelta(seconds=created_seconds))
  db = SimpleNamespace(get=AsyncMock(return_value=pending))
  cancel = AsyncMock()
  monkeypatch.setattr(commands.TradeCommandService, "enqueue_cancel", cancel)
  assert runtime.expiry_reason(pending, original + timedelta(seconds=total_seconds - 1)) is None
  assert await runtime.expire_order(db, "client-0", now=original + timedelta(seconds=total_seconds))
  cancel.assert_awaited_once()
  assert pending.status == "CANCEL_REQUESTED"
  assert pending.status_reason == f"T_{role}_ORDER_EXPIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ACCEPTED", "PARTIAL_FILLED", "UNKNOWN", "RECONCILE_REQUIRED"])
async def test_timeout_uses_same_durable_cancel_identity_and_never_claims_terminal(monkeypatch, status):
  pending = order(status=status)
  db = SimpleNamespace(get=AsyncMock(return_value=pending))
  enqueue = AsyncMock()
  monkeypatch.setattr(commands.TradeCommandService, "enqueue_cancel", enqueue)
  for _ in range(2):
    assert await runtime.expire_order(db, "client-0", now=pending.created_at + timedelta(seconds=31))
  assert enqueue.call_args.kwargs["idempotency_key"] == "t-order-timeout:client-0"
  assert enqueue.call_args.kwargs["execution_ref"] == ExecutionOwnerRef("EXIT_PLAN", "plan")
  assert enqueue.call_args.kwargs["commit_transaction"] is False
  assert pending.status == (status if status in {"UNKNOWN", "RECONCILE_REQUIRED"} else "CANCEL_REQUESTED")


@pytest.mark.asyncio
async def test_no_broker_identity_never_fabricates_local_zero_fill():
  pending = order(broker_order_id="", status="DELIVERED")
  db = SimpleNamespace(get=AsyncMock(return_value=pending))
  assert not await runtime.expire_order(db, "client-0", now=pending.created_at + timedelta(seconds=31))
  assert pending.status == "DELIVERED"
  assert pending.status_reason.endswith("ORDER_RESULT_UNKNOWN")


@pytest.mark.asyncio
@pytest.mark.parametrize("broker_filled,trade_filled,unapplied,allowed", [
  (100, 100, None, True), (200, 100, None, False), (100, 100, "event", False),
  (0, 0, None, False),
])
async def test_replace_requires_converged_broker_fills_and_applied_runtime_events(
  broker_filled, trade_filled, unapplied, allowed,
):
  pending = order(status="CANCELLED")
  broker = SimpleNamespace(account_id="account", stock_code="600000.SH", volume=200,
                           traded_volume=broker_filled, status=54, type=24)
  async def get(model, identity, **kwargs):
    return pending if model is PendingTradeOrder else broker
  db = SimpleNamespace(
    get=get, scalar=AsyncMock(side_effect=[pending, unapplied]),
    scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [SimpleNamespace(volume=trade_filled)] if trade_filled else [])),
  )
  result = await commands.TradeCommandService(db).evaluate_t_order_replacement(
    client_order_id="client-0", now=datetime(2026, 9, 4, 2, 0, 31, tzinfo=timezone.utc),
    reference_price=Decimal("10"), price_tick=Decimal("0.01"),
  )
  assert result.allowed is allowed
  if allowed:
    assert result.remaining_volume == 100


@pytest.mark.asyncio
async def test_replace_reuses_intent_owner_and_trace_and_reruns_risk(monkeypatch):
  pending = order(status="CANCELLED")
  intent = SimpleNamespace(
    status="QUEUED", intent_metadata={"exact_auto_exit_authorized": True},
    strategy_id=None, reason="profit",
  )
  async def get(model, identity, **kwargs):
    return intent if model is TradeIntentRecord else pending
  account = SimpleNamespace(to_dict=lambda: {"cash": 10000, "total_asset": 100000})
  position = SimpleNamespace(to_dict=lambda: {}, can_use_volume=1000, volume=1000)
  db = SimpleNamespace(get=get, scalar=AsyncMock(side_effect=[None, account, position]))
  service = commands.TradeCommandService(db)
  monkeypatch.setattr(commands, "utcnow", lambda: datetime(2026, 9, 4, 2, 0, 31))
  monkeypatch.setattr(service, "evaluate_t_order_replacement", AsyncMock(return_value=TOrderPolicyResult(
    TOrderPolicyDecision.ALLOW_REPLACE, "OK", Decimal("9.97"), 100,
  )))
  enqueue = AsyncMock(return_value="queued")
  monkeypatch.setattr(service, "enqueue_order_for_account", enqueue)
  result = await service.replace_t_order(
    client_order_id="client-0", quote_at=datetime(2026, 9, 4, 2, 0, 31, tzinfo=timezone.utc),
    reference_price=Decimal("10"), price_tick=Decimal("0.01"),
    limit_up=Decimal("11"), limit_down=Decimal("9"),
    market_data=MarketDataSnapshot("600000.SH", timestamp=datetime(2026, 9, 4, 2, 0, 31, tzinfo=timezone.utc), price=10, limit_up=11, limit_down=9,
                                   bid_price=[10], ask_price=[10.01]),
  )
  assert result == "queued"
  args = enqueue.call_args.kwargs
  assert args["intent_id"] == "intent"
  assert args["execution_ref"] == ExecutionOwnerRef("EXIT_PLAN", "plan")
  assert args["trace_id"] == "trace"
  assert args["volume"] == 100
  assert args["idempotency_key"] == "strategy-exit:plan:intent:replace:1"
  assert args["_t_order_parent_client_id"] == "client-0"
  assert args["risk_decision_id"] != "old-risk"
  assert intent.status == "PENDING"


@pytest.mark.asyncio
async def test_restarted_replace_returns_existing_successor_before_new_risk(monkeypatch):
  pending = order(status="CANCELLED")
  successor = order(client_order_id="client-1", t_order_attempt=1)
  outbox = SimpleNamespace(client_order_id="client-1", message_id="message-1", delivery_status="QUEUED")
  db = SimpleNamespace(get=AsyncMock(return_value=pending), scalar=AsyncMock(side_effect=[successor, outbox]))
  monkeypatch.setattr(commands, "utcnow", lambda: datetime(2026, 9, 4, 2, 0, 31))
  result = await commands.TradeCommandService(db).replace_t_order(
    client_order_id="client-0", quote_at=datetime(2026, 9, 4, 2, 0, 31, tzinfo=timezone.utc),
    reference_price=Decimal("10"), price_tick=Decimal("0.01"),
    limit_up=Decimal("11"), limit_down=Decimal("9"), market_data=MarketDataSnapshot("600000.SH"),
  )
  assert result.client_order_id == "client-1"


@pytest.mark.asyncio
async def test_enqueue_replacement_persists_attempt_chain_and_clips_total_deadline(monkeypatch):
  pending = order(status="CANCELLED", t_order_attempt=1)
  intent = SimpleNamespace(intent_metadata={"t_trade_role": "EXIT", "t_batch_id": "batch"})
  batch = SimpleNamespace(
    account_id="account", instrument_code="600000.SH", environment="LIVE",
    source_execution_owner_type="STRATEGY_RUN", source_execution_owner_id="run",
    source_execution_environment="LIVE", exit_reason=None,
  )
  plan = SimpleNamespace(source_execution_owner_type="STRATEGY_RUN",
                         source_execution_owner_id="run", source_execution_environment="LIVE")
  async def get(model, identity, **kwargs):
    return batch if model is TTradeBatch else plan
  added = []
  db = SimpleNamespace(
    get=get, scalar=AsyncMock(return_value=pending), add=added.append, flush=AsyncMock(),
    execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
  )
  service = commands.TradeCommandService(db)
  monkeypatch.setattr(service, "_require_durable_order_intent", AsyncMock(return_value=intent))
  monkeypatch.setattr(service, "_require_account_capacity", AsyncMock(return_value={}))
  monkeypatch.setattr(service, "_device_for", AsyncMock(return_value=SimpleNamespace(id="device")))
  monkeypatch.setattr(service, "evaluate_t_order_replacement", AsyncMock(return_value=TOrderPolicyResult(
    TOrderPolicyDecision.ALLOW_REPLACE, "OK", Decimal("9.97"), 100,
  )))
  monkeypatch.setattr(commands, "utcnow", lambda: datetime(2026, 9, 4, 2, 1, 5))
  await service.enqueue_order(
    user_id="user", account_id="account", instrument_code="600000.SH", side="SELL",
    order_type="FIX_PRICE", limit_price=Decimal("9.97"), volume=100,
    execution_ref=ExecutionOwnerRef("EXIT_PLAN", "plan"), environment=ExecutionEnvironment.LIVE,
    idempotency_key="strategy-exit:plan:intent:replace:2", intent_id="intent",
    batch_id="batch", bucket="swing", t_trade_role="EXIT", trace_id="trace",
    request_metadata={"t_exit_order_policy_version": "TExitOrderPolicy.v1",
                      "t_order_reference_price": "10", "t_order_price_tick": "0.01",
                      "quote_timestamp": "2026-09-04T02:01:05+00:00"},
    commit_transaction=False, _locked_live_control=SimpleNamespace(account_id="account"),
    _t_order_parent_client_id="client-0",
  )
  replacement = next(row for row in added if isinstance(row, PendingTradeOrder))
  outbox = next(row for row in added if isinstance(row, TradeCommandOutbox))
  assert replacement.t_order_attempt == 2
  assert replacement.t_order_parent_client_id == "client-0"
  assert replacement.t_order_original_created_at == pending.t_order_original_created_at
  assert replacement.intent_id == "intent"
  assert outbox.expires_at == datetime(2026, 9, 4, 2, 1, 30)


@pytest.mark.asyncio
async def test_terminal_entry_at_cutoff_finalizes_without_market_or_replace(monkeypatch):
  from quantx_engine import report_processor
  pending = order(status="CANCELLED", t_trade_role="ENTRY",
                  t_order_original_created_at=datetime(2026, 9, 4, 6, 49, 30))
  db = SimpleNamespace(get=AsyncMock(return_value=pending), scalar=AsyncMock(return_value=None))
  finalize = AsyncMock()
  monkeypatch.setattr(report_processor, "finalize_t_order_lifecycle", finalize)
  await runtime.advance_order(db, "client-0", now=datetime(2026, 9, 4, 6, 50))
  finalize.assert_awaited_once_with(db, pending)
