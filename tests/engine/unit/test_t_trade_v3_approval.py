from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.command_processor as command_processor
import quantx_engine.strategy_executor as executor_module
from quantx_domain.strategies.base import (
  StrategyContext,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.core.utils import time_utils

_DEFAULT_POLICY = OpportunityPolicy()


class _StateManager:
  def __init__(self) -> None:
    self.updates: list[tuple[str, str, dict[str, object]]] = []
    self.on_update = None
    self.strict_error: Exception | None = None

  async def update_trade_intent_status(
    self, intent_id: str, status: str, **updates: object
  ) -> None:
    self.updates.append((intent_id, status, dict(updates)))
    if self.on_update is not None:
      self.on_update(status)

  async def update_trade_intent_status_strict(
    self, intent_id: str, status: str, **updates: object
  ) -> None:
    if self.strict_error is not None:
      raise self.strict_error
    await self.update_trade_intent_status(intent_id, status, **updates)

  def get_account_quota(self) -> dict[str, float]:
    return {"total_asset": 100_000.0}


class _ApprovalStrategy:
  def __init__(self, state: dict[str, object]) -> None:
    self.state = state
    self.order_events: list[object] = []

  def validate_manual_approval(self, _intent: object, _market_data: object) -> None:
    return None

  def on_order(self, event: object) -> None:
    self.order_events.append(event)
    return None


def _expectation() -> dict[str, object]:
  return {
    "signal_version": 7,
    "candidate_id": "toc_candidate_1",
    "candidate_fingerprint": "fingerprint-1",
    "candidate_state_version": 7,
    "config_version": 3,
    "policy_version": _DEFAULT_POLICY.policy_version,
  }


def _opportunity() -> dict[str, object]:
  return {
    "schema_version": 3,
    "state_version": 7,
    "config_version": 3,
    "policy_version": _DEFAULT_POLICY.policy_version,
    "revalidate_score": 60.0,
    "data_health": "READY",
    "candidate_status": "AWAITING_APPROVAL",
    "candidate_awaiting_approval": True,
    "candidate": {
      "candidate_id": "toc_candidate_1",
      "fingerprint": "fingerprint-1",
      "path": "PULLBACK_REBOUND",
    },
    "latest_evaluation": {
      "candidate_id": "toc_candidate_1",
      "candidate_fingerprint": "fingerprint-1",
      "candidate_status": "AWAITING_APPROVAL",
      "selected_path": "PULLBACK_REBOUND",
      "policy_version": _DEFAULT_POLICY.policy_version,
      "data_health": "READY",
      "opportunity_score": 74.0,
      "hard_gates": [{"code": "SPREAD_OK", "passed": True}],
      "blockers": [],
    },
  }


def _runtime_and_intent() -> tuple[StrategyExecutor, StrategyRuntime, TradeIntent]:
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-v3-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "max_trade_amount": 12_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.2,
      "execution_quote_max_age_seconds": 3.0,
      "signal_policy": _DEFAULT_POLICY.to_dict(),
    },
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=context.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="V3_OPPORTUNITY",
    target_amount=9_500.0,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    max_price_deviation_bps=30.0,
    metadata={
      "t_trade_role": "entry",
      "opportunity_schema_version": 3,
      "signal_version": 7,
      "candidate_id": "toc_candidate_1",
      "candidate_fingerprint": "fingerprint-1",
      "candidate_state_version": 7,
      "candidate_status": "AWAITING_APPROVAL",
      "config_version": 3,
      "policy_version": _DEFAULT_POLICY.policy_version,
      "t_batch_id": "batch-1",
    },
  )
  state = {
    "instrument_states": {
      intent.instrument_code: {
        "pending_entry_intent_id": intent.intent_id,
        "entry_order_status": "AWAITING_APPROVAL",
        "opportunity": _opportunity(),
      }
    }
  }
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="v3-approval",
    strategy_id=1,
    strategy_class=_ApprovalStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = _ApprovalStrategy(state)
  runtime.state_manager = _StateManager()
  runtime.latest_market_data[intent.instrument_code] = MarketDataSnapshot(
    instrument_code=intent.instrument_code,
    timestamp=time_utils.now(),
    price=10.0,
    ask_price=[10.0],
  )
  runtime.pending_approvals[intent.intent_id] = intent
  runtime.t_trade_intent_emission_by_instrument[intent.instrument_code] = {
    "account_id": "account-1",
    "run_id": context.run_id,
    "instrument_code": intent.instrument_code,
    "eligible": True,
    "allowed": True,
    "blockers": [],
  }
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  return executor, runtime, intent


@pytest.mark.asyncio
async def test_t_trade_command_preserves_complete_candidate_expectation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  captured: dict[str, object] = {}

  class _Service:
    async def approve_entry(self, run_id: str, intent_id: str, **kwargs: object):
      captured.update(run_id=run_id, intent_id=intent_id, **kwargs)
      return {"success": False, "code": "CAPTURED"}

  monkeypatch.setattr(command_processor, "TTradeService", lambda _manager: _Service())
  result = await command_processor._dispatch(
    "T_TRADE_APPROVE_ENTRY",
    {
      "run_id": "run-1",
      "intent_id": "intent-1",
      "expected_signal_version": 7,
      "expected_candidate_id": "toc_candidate_1",
      "expected_candidate_fingerprint": "fingerprint-1",
      "expected_candidate_state_version": 7,
      "expected_config_version": 3,
      "expected_policy_version": _DEFAULT_POLICY.policy_version,
      "approval_audit": {"actor_id": "user-1", "channel": "WEB"},
    },
  )

  assert result == {"success": False, "code": "CAPTURED"}
  assert captured["approval_expectation"].to_dict() == _expectation()
  assert captured["approval_audit"] == {"actor_id": "user-1", "channel": "WEB"}


@pytest.mark.asyncio
async def test_v3_approval_rejects_stale_client_token_without_invalidating_candidate():
  executor, runtime, intent = _runtime_and_intent()
  expectation = _expectation()
  expectation["candidate_fingerprint"] = "stale-fingerprint"

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=expectation,
  )

  assert result == {
    "success": False,
    "code": "T_TRADE_CANDIDATE_FINGERPRINT_MISMATCH",
    "message": "候选指纹不一致，请刷新后确认最新候选",
  }
  assert intent.intent_id in runtime.pending_approvals
  assert runtime.state_manager.updates == []
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_approval_persistence_failure_keeps_pending_and_never_routes():
  executor, runtime, intent = _runtime_and_intent()
  runtime.state_manager.strict_error = RuntimeError("database unavailable")

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result == {
    "success": False,
    "code": "T_TRADE_APPROVAL_STATUS_PERSIST_FAILED",
    "message": "确认状态保存失败，信号仍保持待确认，请稍后重试",
  }
  assert runtime.pending_approvals[intent.intent_id] is intent
  assert runtime.t_trade_entry_reservations == {}
  assert runtime.state_manager.updates == []
  assert runtime.strategy.order_events == []
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_user_rejection_persistence_failure_keeps_pending() -> None:
  executor, runtime, intent = _runtime_and_intent()
  runtime.state_manager.strict_error = RuntimeError("database unavailable")

  result = await executor.reject_trade_intent(
    runtime.run_id,
    intent.intent_id,
  )

  assert result == {
    "success": False,
    "code": "T_TRADE_APPROVAL_STATUS_PERSIST_FAILED",
    "message": "信号状态保存失败，信号仍保持待确认，请稍后重试",
  }
  assert runtime.pending_approvals[intent.intent_id] is intent
  assert runtime.state_manager.updates == []
  assert runtime.strategy.order_events == []
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_invalidation_persistence_failure_keeps_pending() -> None:
  executor, runtime, intent = _runtime_and_intent()
  runtime.state_manager.strict_error = RuntimeError("database unavailable")
  state = runtime.strategy.state["instrument_states"][intent.instrument_code]
  state["opportunity"]["candidate"]["fingerprint"] = "new-fingerprint"

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result == {
    "success": False,
    "code": "T_TRADE_APPROVAL_STATUS_PERSIST_FAILED",
    "message": "信号状态保存失败，信号仍保持待确认，请稍后重试",
  }
  assert runtime.pending_approvals[intent.intent_id] is intent
  assert runtime.state_manager.updates == []
  assert runtime.strategy.order_events == []
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_approval_expires_intent_that_is_not_latest_candidate():
  executor, runtime, intent = _runtime_and_intent()
  state = runtime.strategy.state["instrument_states"][intent.instrument_code]
  state["opportunity"]["candidate"]["fingerprint"] = "new-fingerprint"

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_CANDIDATE_NOT_LATEST"
  assert intent.intent_id not in runtime.pending_approvals
  assert runtime.state_manager.updates[-1][1:] == (
    "EXPIRED",
    {"notes": "T_TRADE_CANDIDATE_NOT_LATEST"},
  )
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_approval_rejects_revalidation_from_another_fsm_path():
  executor, runtime, intent = _runtime_and_intent()
  opportunity = runtime.strategy.state["instrument_states"][intent.instrument_code][
    "opportunity"
  ]
  opportunity["latest_evaluation"]["selected_path"] = "MOMENTUM_ACCELERATION"

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_CANDIDATE_PATH_MISMATCH"
  assert intent.intent_id not in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_v3_approval_rejects_candidate_from_old_durable_config(
  monkeypatch: pytest.MonkeyPatch,
):
  executor, runtime, intent = _runtime_and_intent()
  runtime.context.parameters.update(
    account_id="account-1",
    global_monitor_id="monitor-1",
    global_config_version=3,
  )
  config = SimpleNamespace(
    id="monitor-1",
    account_id="account-1",
    enabled=True,
    strategy_run_id=runtime.run_id,
    mode="paper",
    config_version=4,
  )

  class _Result:
    def scalar_one_or_none(self):
      return config

  class _Session:
    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

    async def execute(self, _statement):
      return _Result()

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", _Session)

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_CONFIG_VERSION_CHANGED"
  assert intent.intent_id not in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("mutator", "expected_code"),
  [
    (
      lambda opportunity: opportunity["latest_evaluation"].update(
        data_health="DEGRADED"
      ),
      "T_TRADE_DATA_HEALTH_NOT_READY",
    ),
    (
      lambda opportunity: opportunity["latest_evaluation"].update(
        opportunity_score=59.9
      ),
      "T_TRADE_REVALIDATE_SCORE_BELOW_FLOOR",
    ),
    (
      lambda opportunity: opportunity["latest_evaluation"].update(
        hard_gates=[{"code": "SPREAD_TOO_WIDE", "passed": False}]
      ),
      "T_TRADE_HARD_GATE_BLOCKED",
    ),
    (
      lambda opportunity: opportunity["latest_evaluation"].update(
        blockers=["ENTRY_CUTOFF_REACHED"]
      ),
      "T_TRADE_REVALIDATION_BLOCKED",
    ),
  ],
)
async def test_v3_approval_fails_closed_on_current_revalidation(
  mutator,
  expected_code: str,
) -> None:
  executor, runtime, intent = _runtime_and_intent()
  opportunity = runtime.strategy.state["instrument_states"][intent.instrument_code][
    "opportunity"
  ]
  mutator(opportunity)

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == expected_code
  assert intent.intent_id not in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_target_amount_is_reserved_and_left_for_order_sizer():
  executor, runtime, intent = _runtime_and_intent()

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=deepcopy(_expectation()),
  )

  assert result["success"] is True
  reservation = runtime.t_trade_entry_reservations[intent.intent_id]
  assert reservation["requested_volume"] == 0
  assert reservation["requested_amount"] == pytest.approx(9_500.0)
  assert reservation["amount"] == pytest.approx(9_500.0)
  executor._process_trade_intent.assert_awaited_once_with(runtime, intent)


@pytest.mark.asyncio
async def test_v3_approval_rechecks_mutable_gates_after_durable_status_yield():
  executor, runtime, intent = _runtime_and_intent()
  opportunity = runtime.strategy.state["instrument_states"][intent.instrument_code][
    "opportunity"
  ]

  def invalidate_after_status(status: str) -> None:
    if status == "APPROVED":
      opportunity["latest_evaluation"]["data_health"] = "STALE"

  runtime.state_manager.on_update = invalidate_after_status

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_DATA_HEALTH_NOT_READY"
  assert runtime.t_trade_entry_reservations == {}
  assert [item[1] for item in runtime.state_manager.updates] == [
    "APPROVED",
    "EXPIRED",
  ]
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.parametrize(
  ("entry_change", "expected_code"),
  [
    (None, "UNIVERSE_ELIGIBILITY_UNAVAILABLE"),
    ({"eligible": False, "blockers": []}, "POSITION_NOT_ELIGIBLE"),
    ({"eligible": True, "blockers": ["IGNORED_BY_USER"]}, "IGNORED_BY_USER"),
    (
      {"eligible": True, "blockers": ["HOLDING_NOT_ELIGIBLE"]},
      "HOLDING_NOT_ELIGIBLE",
    ),
  ],
)
def test_v3_approval_rebuilds_latest_universe_gate_and_excludes_current_intent(
  entry_change,
  expected_code: str,
):
  executor, runtime, intent = _runtime_and_intent()
  assert executor._t_trade_portfolio_approval_failure(runtime, intent) is None

  if entry_change is None:
    runtime.t_trade_intent_emission_by_instrument.clear()
  else:
    runtime.t_trade_intent_emission_by_instrument[intent.instrument_code].update(
      entry_change
    )
  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure is not None
  assert failure[0] == expected_code


@pytest.mark.asyncio
async def test_v3_approval_late_account_gate_change_expires_without_routing():
  executor, runtime, intent = _runtime_and_intent()

  def invalidate_after_status(status: str) -> None:
    if status == "APPROVED":
      runtime.context.parameters["max_concurrent_batches"] = 1
      runtime.strategy.state["instrument_states"]["000001.SZ"] = {
        "instrument_code": "000001.SZ",
        "batch_id": "late-active-batch",
        "entry_order_status": "FILLED",
        "entry_filled_volume": 100,
        "exit_filled_volume": 0,
        "entry_avg_price": 100.0,
      }

  runtime.state_manager.on_update = invalidate_after_status
  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED"
  assert [item[1] for item in runtime.state_manager.updates] == [
    "APPROVED",
    "EXPIRED",
  ]
  assert runtime.pending_approvals == {}
  executor._process_trade_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_v3_approval_rechecks_quote_freshness_and_price_deviation():
  executor, runtime, intent = _runtime_and_intent()
  runtime.latest_market_data[intent.instrument_code] = MarketDataSnapshot(
    instrument_code=intent.instrument_code,
    timestamp=time_utils.now() - timedelta(seconds=4),
    price=10.0,
    ask_price=[10.0],
  )

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=_expectation(),
  )

  assert result["success"] is False
  assert result["code"] == "APPROVAL_QUOTE_STALE"
  assert intent.intent_id not in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


def test_v3_approval_quote_age_uses_nested_versioned_signal_policy():
  executor, runtime, intent = _runtime_and_intent()
  runtime.context.parameters["execution_quote_max_age_seconds"] = 1.0
  runtime.context.parameters["signal_policy"] = OpportunityPolicy(
    max_quote_age_ms=5_000
  ).to_dict()
  runtime.latest_market_data[intent.instrument_code] = MarketDataSnapshot(
    instrument_code=intent.instrument_code,
    timestamp=time_utils.now() - timedelta(seconds=4),
    price=10.0,
    ask_price=[10.0],
  )

  assert executor._execution_quote_max_age_seconds(runtime, intent) == 5.0
  assert executor._approval_failure(runtime, intent) is None


@pytest.mark.parametrize(
  "signal_policy",
  [
    None,
    {"max_quote_age_ms": 5_000},
    {**OpportunityPolicy().to_dict(), "max_quote_age_ms": 0},
    {
      **OpportunityPolicy().to_dict(),
      "policy_version": "different-policy-version",
    },
  ],
)
def test_v3_approval_fails_closed_for_invalid_nested_signal_policy(
  signal_policy,
):
  executor, runtime, intent = _runtime_and_intent()
  if signal_policy is None:
    runtime.context.parameters.pop("signal_policy")
  else:
    runtime.context.parameters["signal_policy"] = signal_policy

  failure = executor._approval_failure(runtime, intent)

  assert failure is not None
  assert failure[0] == "T_TRADE_SIGNAL_POLICY_INVALID"


def test_v3_target_amount_is_checked_against_account_single_trade_cap():
  executor, runtime, intent = _runtime_and_intent()
  intent.target_amount = 12_000.01

  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure is not None
  assert failure[0] == "T_TRADE_SINGLE_AMOUNT_LIMIT"


def test_v3_target_amount_reservation_is_restored_before_volume_is_sized():
  executor, runtime, intent = _runtime_and_intent()
  runtime.t_trade_entry_reservations.clear()
  state = runtime.strategy.state["instrument_states"][intent.instrument_code]
  state.update(
    pending_entry_intent_id=intent.intent_id,
    entry_order_status="PENDING",
    requested_entry_amount=9_500.0,
    last_price=10.0,
    opportunity={"latest_evaluation": {"features": {"price": 9.9}}},
    batch_id="batch-1",
  )

  executor._restore_t_trade_entry_reservations(runtime)

  reservation = runtime.t_trade_entry_reservations[intent.intent_id]
  assert reservation["volume"] == 0
  assert reservation["requested_volume"] == 0
  assert reservation["requested_amount"] == pytest.approx(9_500.0)
  assert reservation["price"] == pytest.approx(9.9)
  assert reservation["amount"] == pytest.approx(9_500.0)
