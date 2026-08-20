from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.strategies.ashare_managed_entry_plan import (
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.entry_plan import (
  EntryPlanStatus,
  ManagedEntryPlanState,
)
from quantx_engine import strategy_executor as executor_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)


def _managed_entry_parameters() -> dict:
  return {
    "account_id": "account-1",
    "entry_plan_enabled": True,
    "managed_entry_plan": {
      "template_version": 1,
      "config_version": 3,
      "instrument_code": "605499.SH",
      "bucket": "swing",
      "target_policy": {
        "mode": "INCREMENTAL_AMOUNT_CNY",
        "incremental_amount_cny": 50_000,
        "max_total_amount_cny": 50_000,
        "max_position_pct": 0.5,
        "baseline_snapshot": {
          "position_volume": 100,
          "market_value_cny": 10_000,
          "total_asset_cny": 100_000,
          "reference_price": 100,
          "account_snapshot_version": "snapshot-7",
        },
      },
      "trigger_rules": [
        {
          "rule_id": "manual-1",
          "rule_type": "MANUAL_TRIGGER",
          "priority": 1,
          "parameters": {},
        }
      ],
      "pacing_policy": {
        "tranche_count": 5,
        "max_single_intent_amount_cny": 12_000,
        "max_daily_filled_amount_cny": 20_000,
        "max_orders_per_day": 5,
        "max_open_orders": 1,
      },
      "execution_policy": {
        "environment": "LIVE",
        "authorization_mode": "AUTO",
        "price_reference": "ASK1_PROTECTED_LIMIT",
        "max_slippage_bps": 35,
        "max_price_deviation_bps": 50,
      },
      "completion_policy": {
        "max_buy_price": 130.5,
        "expire_at_ms": 1_788_000_000_000,
      },
      "exit_plan_template": {"enabled": True},
    },
  }


def _runtime(*, mode: StrategyRunMode = StrategyRunMode.LIVE) -> StrategyRuntime:
  context = StrategyContext(
    run_id="run-1",
    mode=mode,
    instruments=["605499.SH"],
    parameters=_managed_entry_parameters(),
    initial_capital=100_000,
  )
  return StrategyRuntime(
    run_id="run-1",
    name="entry",
    strategy_id=7,
    strategy_class=SimpleNamespace,
    context=context,
    state_manager=SimpleNamespace(update_trade_intent_status=AsyncMock()),
    status=ExecutionStatus.RUNNING,
  )


def _intent(
  *,
  execution_mode: TradeIntentExecutionMode = TradeIntentExecutionMode.AUTO,
  direction: TradeIntentDirection = TradeIntentDirection.BUY,
) -> TradeIntent:
  return TradeIntent(
    strategy_id="7",
    run_id="run-1",
    instrument_code="605499.SH",
    direction=direction,
    bucket="swing",
    reason="ENTRY_TEST",
    target_volume=100,
    limit_price_hint=100,
    execution_mode=execution_mode,
    intent_id="intent-1",
    metadata={
      "owner_type": "STRATEGY_RUN",
      "owner_id": "run-1",
      "entry_plan_id": "run-1",
      "entry_config_version": 3,
      "protected_limit_price": 100,
    },
  )


def _request() -> OrderRequest:
  return OrderRequest(
    instrument_code="605499.SH",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=100,
    metadata={"intent_id": "intent-1"},
  )


@pytest.mark.asyncio
async def test_live_auto_entry_first_gate_binds_grant_after_final_sizing(
  monkeypatch,
) -> None:
  captured: dict = {}

  class SessionContext:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_args):
      return False

  class AuthorizationService:
    def __init__(self, _db):
      pass

    async def validate_or_invalidate(self, **kwargs):
      captured.update(kwargs)
      return SimpleNamespace(
        valid=True,
        code="AUTHORIZED",
        message="ok",
        balance=SimpleNamespace(grant_id="grant-1"),
      )

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", SessionContext)
  monkeypatch.setattr(
    executor_module,
    "EntryPlanAuthorizationService",
    AuthorizationService,
  )
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime()
  intent = _intent()
  request = _request()
  try:
    failure = await executor._authorize_live_auto_managed_entry(
      runtime,
      intent,
      request,
      account={"total_asset": 100_000},
      position={"market_value": 10_000, "long_volume": 100},
    )
  finally:
    await executor.shutdown()

  assert failure is None
  assert captured["proposed_amount_cny"] == 10_000
  assert captured["proposed_buy_price"] == 100
  assert captured["resulting_position_pct"] == Decimal("0.2")
  assert intent.metadata["auto_entry_authorization_grant_id"] == "grant-1"
  assert request.metadata["auto_entry_authorization_grant_id"] == "grant-1"
  assert request.metadata["idempotency_key"] == "entry-plan:run-1:intent-1"
  runtime.state_manager.update_trade_intent_status.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("mode", "execution_mode", "direction"),
  [
    (
      StrategyRunMode.PAPER,
      TradeIntentExecutionMode.AUTO,
      TradeIntentDirection.BUY,
    ),
    (
      StrategyRunMode.LIVE,
      TradeIntentExecutionMode.MANUAL_CONFIRM,
      TradeIntentDirection.BUY,
    ),
    (
      StrategyRunMode.LIVE,
      TradeIntentExecutionMode.AUTO,
      TradeIntentDirection.SELL,
    ),
  ],
)
async def test_first_gate_does_not_change_paper_manual_or_sell(
  mode,
  execution_mode,
  direction,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime(mode=mode)
  intent = _intent(execution_mode=execution_mode, direction=direction)
  request = _request()
  try:
    failure = await executor._authorize_live_auto_managed_entry(
      runtime,
      intent,
      request,
      account={"total_asset": 100_000},
      position={"market_value": 10_000},
    )
  finally:
    await executor.shutdown()

  assert failure is None
  assert "auto_entry_authorization_grant_id" not in request.metadata
  runtime.state_manager.update_trade_intent_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_auto_grant_downgrades_to_explicit_manual_confirmation() -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime()
  intent = _intent()
  request = _request()
  try:
    await executor._reject_live_auto_managed_entry(
      runtime,
      intent,
      request,
      code="ENTRY_AUTHORIZATION_EXPIRED",
      message="授权已过期",
      risk_decision_id="risk-1",
    )
  finally:
    await executor.shutdown()

  assert intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
  assert runtime.pending_approvals[intent.intent_id] is intent
  runtime.state_manager.update_trade_intent_status.assert_awaited_once_with(
    intent.intent_id,
    "AWAITING_APPROVAL",
    risk_decision_id="risk-1",
    metadata=intent.metadata,
    notes="ENTRY_AUTHORIZATION_EXPIRED",
  )


@pytest.mark.asyncio
async def test_paused_entry_plan_cannot_confirm_a_previous_manual_intent() -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime()
  runtime.context.parameters["entry_plan_enabled"] = False
  try:
    failure = executor._approval_failure(runtime, _intent())
  finally:
    await executor.shutdown()

  assert failure == (
    "ENTRY_PLAN_PAUSED",
    "买入计划已暂停或取消，不能确认旧意图",
  )


@pytest.mark.asyncio
async def test_managed_entry_legacy_approval_without_challenge_fails_closed() -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime()
  runtime.strategy_class = AshareManagedEntryPlanStrategy
  intent = _intent(execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM)
  runtime.pending_approvals[intent.intent_id] = intent
  executor.runs[runtime.run_id] = runtime
  try:
    result = await executor.approve_trade_intent(
      runtime.run_id,
      intent.intent_id,
      approval_audit={
        "actor_id": "user-1",
        "device_session_id": "session-1",
        "channel": "GRAPHQL_LEGACY",
      },
    )
  finally:
    executor.runs.pop(runtime.run_id, None)
    await executor.shutdown()

  assert result == {
    "success": False,
    "code": "ENTRY_PLAN_DEVICE_CHALLENGE_REQUIRED",
    "message": "托管买入必须使用 confirmEntryIntent 完成设备挑战后确认",
  }
  assert runtime.pending_approvals[intent.intent_id] is intent
  runtime.state_manager.update_trade_intent_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_entry_direct_command_rejects_invalid_challenge_binding(
  monkeypatch,
) -> None:
  executor = StrategyExecutor(max_workers=1)
  runtime = _runtime()
  runtime.strategy_class = AshareManagedEntryPlanStrategy
  intent = _intent(execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM)
  runtime.pending_approvals[intent.intent_id] = intent
  executor.runs[runtime.run_id] = runtime
  record = SimpleNamespace(
    strategy_run_id=runtime.run_id,
    direction="BUY",
    status="AWAITING_APPROVAL",
    intent_metadata={
      "mobile_trade_approval_challenge_v1": {
        "challenge_id": "different-challenge",
        "action": "STRATEGY_TRADE_INTENT_APPROVAL",
        "user_id": "user-1",
        "device_session_id": "session-1",
        "account_id": "account-1",
        "run_id": runtime.run_id,
        "intent_id": intent.intent_id,
        "consumed_at": "2026-08-20T10:00:00+08:00",
      }
    },
  )

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key):
      return record

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", Session)
  try:
    result = await executor.approve_trade_intent(
      runtime.run_id,
      intent.intent_id,
      approval_audit={
        "actor_id": "user-1",
        "device_session_id": "session-1",
        "challenge_id": "forged-challenge",
        "channel": "ENTRY_PLAN_DEVICE_CHALLENGE",
      },
    )
  finally:
    executor.runs.pop(runtime.run_id, None)
    await executor.shutdown()

  assert result["success"] is False
  assert result["code"] == "ENTRY_PLAN_DEVICE_CHALLENGE_REQUIRED"
  assert intent.intent_id in runtime.pending_approvals


class _RestoreScalars:
  def __init__(self, values):
    self._values = list(values)

  def scalars(self):
    return self

  def all(self):
    return list(self._values)


class _ManagedRestoreStateManager:
  def __init__(self):
    self.released = []
    self.checkpoints = []

  async def restore_manual_trade_intent(self, _intent_id):
    return None

  def release_order_resources(self, order_id):
    self.released.append(order_id)

  def update_strategy_custom_state(self, state, *, full_snapshot=False):
    self.checkpoints.append((dict(state), full_snapshot))


def test_restored_checkpoint_falls_back_to_runtime_state_manager_head_api() -> None:
  runtime = _managed_restore_runtime("intent-checkpoint-fallback")
  update_custom_state = Mock()
  runtime.state_manager = SimpleNamespace(update_custom_state=update_custom_state)

  StrategyExecutor._checkpoint_restored_strategy_state(runtime)

  update_custom_state.assert_called_once_with(runtime.strategy.state.to_dict())


def _managed_restore_runtime(
  intent_id: str,
  *,
  phase: EntryPlanStatus = EntryPlanStatus.AWAITING_APPROVAL,
) -> StrategyRuntime:
  runtime = _runtime()
  runtime.strategy_class = AshareManagedEntryPlanStrategy
  runtime.strategy = AshareManagedEntryPlanStrategy(runtime.context)
  runtime.state_manager = _ManagedRestoreStateManager()
  state = ManagedEntryPlanState(
    phase=phase,
    pending_intent_id=intent_id,
    pending_stage_id="stage-1",
    pending_rule_id="manual-1",
    pending_rule_type="MANUAL_TRIGGER",
    pending_requested_volume=100,
    pending_requested_amount_cny=10_000,
    reserved_amount_cny=10_000,
  )
  runtime.strategy.state.set("managed_entry_plan", state.to_dict())
  return runtime


def _approved_managed_entry_record(
  intent_id: str,
  *,
  execution_mode: str = "MANUAL_CONFIRM",
):
  return SimpleNamespace(
    id=intent_id,
    strategy_run_id="run-1",
    account_id="account-1",
    instrument_code="605499.SH",
    direction="BUY",
    status="APPROVED",
    order_id=None,
    executed_volume=0,
    executed_price=None,
    executed_time=None,
    notes="MANUAL_APPROVAL_ACCEPTED",
    intent_metadata={
      "entry_plan_id": "run-1",
      "execution_mode": execution_mode,
      "mobile_trade_approval_challenge_v1": {
        "challenge_id": "challenge-consumed",
        "consumed_at": "2026-08-20T10:00:00+08:00",
      },
    },
  )


@pytest.mark.asyncio
async def test_managed_entry_restore_reconciles_approved_without_any_order(
  monkeypatch,
) -> None:
  intent_id = "intent-approved-before-outbox-crash"
  record = _approved_managed_entry_record(intent_id)

  class Session:
    def __init__(self):
      self.committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars([])

    async def commit(self):
      self.committed = True

  session = Session()
  monkeypatch.setattr(executor_module, "AsyncSessionLocal", lambda: session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(intent_id)
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert session.committed
  assert record.status == "RECONCILED_ZERO_FILL"
  assert record.notes == (
    "APPROVED_WITHOUT_DURABLE_ORDER_RECONCILED_ZERO_FILL"
  )
  assert record.intent_metadata["managed_entry_restore"]["zero_order_proof"] == {
    "order_id_empty": True,
    "executed_volume": 0,
    "executed_price_non_positive": True,
    "executed_time_empty": True,
    "pending_order_count": 0,
    "outbox_count": 0,
    "correlation_count": 0,
    "runtime_event_count": 0,
  }
  assert restored.pending_intent_id == ""
  assert restored.phase == EntryPlanStatus.ARMED
  assert runtime.state_manager.released == [intent_id]
  assert runtime.state_manager.checkpoints[-1][1] is True
  assert runtime.pending_approvals == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("order_id", "strategy-order-1"),
    ("executed_volume", 1),
    ("executed_price", 1.0),
    ("executed_time", datetime(2026, 8, 20, 10, 0)),
  ],
)
async def test_managed_entry_restore_refuses_zero_proof_with_execution_fact(
  monkeypatch,
  field,
  value,
) -> None:
  intent_id = f"intent-ambiguous-{field}"
  record = _approved_managed_entry_record(intent_id)
  setattr(record, field, value)

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars([])

    async def commit(self):
      self.committed = True

  session = Session()
  monkeypatch.setattr(executor_module, "AsyncSessionLocal", lambda: session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(intent_id)
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert session.committed
  assert record.status == "RECONCILE_REQUIRED"
  assert restored.pending_intent_id == intent_id
  assert restored.phase == EntryPlanStatus.ENTRY_PENDING
  assert restored.data_quality == "RECONCILE_REQUIRED"
  assert runtime.state_manager.released == []


@pytest.mark.asyncio
async def test_managed_entry_restore_terminal_zero_fill_is_idempotent(
  monkeypatch,
) -> None:
  intent_id = "intent-zero-fill-replayed"
  record = _approved_managed_entry_record(intent_id)
  record.status = "RECONCILED_ZERO_FILL"
  record.notes = "APPROVED_WITHOUT_DURABLE_ORDER_RECONCILED_ZERO_FILL"

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", Session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(intent_id)
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert restored.pending_intent_id == ""
  assert restored.phase == EntryPlanStatus.ARMED
  assert runtime.state_manager.released == [intent_id]


@pytest.mark.asyncio
async def test_managed_entry_restore_checks_entry_pending_manual_crash_gap(
  monkeypatch,
) -> None:
  intent_id = "intent-entry-pending-before-outbox-crash"
  record = _approved_managed_entry_record(intent_id)

  class Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars([])

    async def commit(self):
      return None

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", Session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(
    intent_id,
    phase=EntryPlanStatus.ENTRY_PENDING,
  )
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert record.status == "RECONCILED_ZERO_FILL"
  assert restored.pending_intent_id == ""
  assert restored.phase == EntryPlanStatus.ARMED
  assert runtime.state_manager.released == [intent_id]


@pytest.mark.asyncio
async def test_managed_entry_restore_auto_intent_with_order_artifacts_stays_pending(
  monkeypatch,
) -> None:
  intent_id = "intent-auto-entry-pending"
  record = _approved_managed_entry_record(intent_id, execution_mode="AUTO")
  record.status = "PENDING"
  pending = SimpleNamespace(
    status="QUEUED",
    client_order_id="client-auto-1",
    broker_order_id=None,
  )
  correlation = SimpleNamespace(client_order_id="client-auto-1")
  query_values = iter([[pending], [correlation], [SimpleNamespace()], []])

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars(next(query_values))

    async def commit(self):
      self.committed = True

  session = Session()
  monkeypatch.setattr(executor_module, "AsyncSessionLocal", lambda: session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(
    intent_id,
    phase=EntryPlanStatus.ENTRY_PENDING,
  )
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert not session.committed
  assert record.status == "PENDING"
  assert restored.pending_intent_id == intent_id
  assert restored.phase == EntryPlanStatus.ENTRY_PENDING
  assert restored.data_quality != "RECONCILE_REQUIRED"
  assert restored.last_decision["order_status"] == "QUEUED"
  assert restored.last_decision["client_order_id"] == "client-auto-1"
  assert runtime.state_manager.released == []


@pytest.mark.asyncio
async def test_managed_entry_restore_auto_pending_without_artifacts_reconciles_zero(
  monkeypatch,
) -> None:
  intent_id = "intent-auto-before-outbox-crash"
  record = _approved_managed_entry_record(intent_id, execution_mode="AUTO")
  record.status = "PENDING"

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars([])

    async def commit(self):
      self.committed = True

  session = Session()
  monkeypatch.setattr(executor_module, "AsyncSessionLocal", lambda: session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(
    intent_id,
    phase=EntryPlanStatus.ENTRY_PENDING,
  )
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert session.committed
  assert record.status == "RECONCILED_ZERO_FILL"
  assert record.notes == "AUTO_PENDING_WITHOUT_DURABLE_ORDER_RECONCILED_ZERO_FILL"
  assert restored.pending_intent_id == ""
  assert restored.phase == EntryPlanStatus.ARMED
  assert runtime.state_manager.released == [intent_id]


@pytest.mark.asyncio
async def test_managed_entry_restore_existing_durable_order_never_reorders_or_releases(
  monkeypatch,
) -> None:
  intent_id = "intent-approved-with-durable-order"
  record = _approved_managed_entry_record(intent_id)
  pending = SimpleNamespace(
    status="ACCEPTED",
    client_order_id="client-order-1",
    broker_order_id="broker-order-1",
  )
  correlation = SimpleNamespace(client_order_id="client-order-1")
  query_values = iter([[pending], [correlation], [SimpleNamespace()], []])

  class Session:
    committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, _key, **_kwargs):
      return record

    async def execute(self, _statement):
      return _RestoreScalars(next(query_values))

    async def commit(self):
      self.committed = True

  session = Session()
  monkeypatch.setattr(executor_module, "AsyncSessionLocal", lambda: session)
  executor = StrategyExecutor(max_workers=1)
  runtime = _managed_restore_runtime(intent_id)
  try:
    await executor._restore_pending_manual_approvals(runtime)
  finally:
    await executor.shutdown()

  restored = ManagedEntryPlanState.from_dict(
    runtime.strategy.state["managed_entry_plan"]
  )
  assert not session.committed
  assert record.status == "APPROVED"
  assert restored.pending_intent_id == intent_id
  assert restored.phase == EntryPlanStatus.ENTRY_PENDING
  assert restored.last_decision["order_status"] == "ACCEPTED"
  assert restored.last_decision["client_order_id"] == "client-order-1"
  assert runtime.state_manager.released == []
