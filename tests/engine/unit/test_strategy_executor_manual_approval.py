import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateStatus,
  DataHealth,
  OpportunityCandidate,
  OpportunityPath,
  OpportunityPolicy,
  OpportunityState,
)
from quantx_engine.replay_clock import ReplayClock
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_engine.t_trade_coordination import t_trade_account_coordination_lock
from quantx_infrastructure.core.utils import time_utils

_V3_POLICY = OpportunityPolicy()


class FakeStateManager:
  def __init__(self):
    self.persist_enabled = True
    self.records = []
    self.updates = []
    self.durable_snapshot = None
    self.recovery_records = None
    self.force_save_results = [True, True]
    self.force_save_count = 0
    self.custom = {}
    self.material_outbox = {}
    self.paper_fill_outbox = {}
    self.last_snapshot_failure_code = None

  def enqueue_t_trade_material_events(self, events):
    for event in events:
      self.material_outbox.setdefault(event["event_key"], dict(event))

  def pending_t_trade_material_events(self):
    return [dict(event) for event in self.material_outbox.values()]

  def acknowledge_t_trade_material_events(self, event_keys):
    for event_key in event_keys:
      self.material_outbox.pop(event_key, None)

  def enqueue_t_trade_paper_fill_fact(self, fact):
    self.paper_fill_outbox.setdefault(fact["fact_key"], dict(fact))

  def pending_t_trade_paper_fill_facts(self):
    return [dict(fact) for fact in self.paper_fill_outbox.values()]

  def acknowledge_t_trade_paper_fill_facts(self, fact_keys):
    for fact_key in fact_keys:
      self.paper_fill_outbox.pop(fact_key, None)

  async def record_trade_intent(self, intent, status="PENDING"):
    self.records.append((intent.intent_id, status))

  async def update_trade_intent_status(self, intent_id, status, **updates):
    self.updates.append((intent_id, status, updates))

  async def update_trade_intent_status_strict(self, intent_id, status, **updates):
    self.updates.append((intent_id, status, updates))

  async def restore_v3_manual_candidate_intents(
    self,
    *,
    account_id,
    linked_intent_ids=None,
  ):
    assert account_id == "account-1"
    if self.recovery_records is not None:
      return list(self.recovery_records)
    if getattr(self, "restored_intent", None) is not None:
      return [
        SimpleNamespace(
          intent=self.restored_intent,
          durable_status="AWAITING_APPROVAL",
        )
      ]
    snapshot = dict(self.durable_snapshot or {})
    if not snapshot or str(snapshot.get("id") or "") not in set(
      linked_intent_ids or []
    ):
      return []
    return [
      SimpleNamespace(
        intent=make_v3_pending_intent(
          str(snapshot.get("strategy_run_id") or ""),
          intent_id=str(snapshot["id"]),
          metadata=dict(snapshot.get("metadata") or {}),
        ),
        durable_status=str(snapshot.get("status") or ""),
      )
    ]

  async def restore_manual_trade_intent(self, intent_id):
    return getattr(self, "restored_intent", None)

  async def get_trade_intent_snapshot(self, intent_id):
    return self.durable_snapshot

  def set_custom(self, key, value):
    self.custom[key] = value

  def update_custom_state(self, updates):
    self.custom.update(updates)

  async def force_save(self):
    self.force_save_count += 1
    return self.force_save_results.pop(0)

  def get_account_quota(self):
    return {"total_asset": 100_000.0}


def make_durable_entry_snapshot(
  intent_id,
  *,
  strategy_run_id,
  status,
  order_id=None,
  executed_volume=0,
  executed_price=0.0,
  metadata=None,
):
  candidate_id = f"candidate-{intent_id}"
  return {
    "id": intent_id,
    "strategy_run_id": strategy_run_id,
    "instrument_code": "600000.SH",
    "status": status,
    "order_id": order_id,
    "executed_volume": executed_volume,
    "executed_price": executed_price,
    "executed_time": datetime(2026, 7, 13, 10, 0).isoformat(),
    "metadata": {
      "t_trade_role": "entry",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "opportunity_schema_version": 3,
      "execution_mode": "MANUAL_CONFIRM",
      "candidate_id": candidate_id,
      "candidate_fingerprint": f"fingerprint-{intent_id}",
      "candidate_state_version": 7,
      **dict(metadata or {}),
    },
  }


def make_t_entry_metadata(strategy, *, batch_id, plan_id):
  policy = strategy._exit_policy_snapshot()
  template = strategy.build_exit_plan_template(
    instrument_code="600000.SH",
    batch_id=batch_id,
    plan_id=plan_id,
    policy=policy,
  )
  return {
    "t_trade_role": "entry",
    "instrument_code": "600000.SH",
    "t_batch_id": batch_id,
    "exit_plan_id": plan_id,
    "exit_plan_template": template.to_dict(),
  }


def make_v3_pending_state(
  strategy: AshareIntradayTAssistantStrategy,
  intent_id: str,
  **overrides,
):
  candidate_id = f"candidate-{intent_id}"
  fingerprint = f"fingerprint-{intent_id}"
  candidate = OpportunityCandidate(
    candidate_id=candidate_id,
    fingerprint=fingerprint,
    episode_id=f"episode-{intent_id}",
    path=OpportunityPath.PULLBACK_REBOUND,
    latched_at_ms=1_000,
    expires_at_ms=9_000_000_000_000,
    source_time_ms=1_000,
    tick_ordinal=1,
    price=10.0,
    score=80.0,
    policy_version=_V3_POLICY.policy_version,
    feature_schema_version=_V3_POLICY.feature_schema_version,
    reference_profile_version="profile-20260712",
    reference_profile_schema_version=1,
  )
  opportunity = OpportunityState(
    instrument_code="600000.SH",
    trade_date="2026-07-13",
    continuity_generation="1",
    candidate=candidate,
    candidate_status=CandidateStatus.AWAITING_APPROVAL,
    candidate_awaiting_approval=True,
  ).to_dict()
  opportunity.update(
    {
      "state_version": 7,
      "config_version": 0,
      "policy_version": _V3_POLICY.policy_version,
      "revalidate_score": _V3_POLICY.revalidate_score,
      "thresholds": {
        "preview": _V3_POLICY.preview_score,
        "candidate": _V3_POLICY.candidate_score,
        "revalidate": _V3_POLICY.revalidate_score,
        "rearm": _V3_POLICY.rearm_score,
      },
      "latest_evaluation": {
        "data_health": DataHealth.READY.value,
        "data_health_reasons": [],
        "policy_version": _V3_POLICY.policy_version,
        "selected_path": OpportunityPath.PULLBACK_REBOUND.value,
        "opportunity_score": 80.0,
        "hard_gates": [{"code": "SPREAD_OK", "passed": True}],
        "blockers": [],
        "external_blockers": [],
        "candidate_id": candidate_id,
        "candidate_fingerprint": fingerprint,
        "candidate_status": CandidateStatus.AWAITING_APPROVAL.value,
        "pullback": {
          "phase": "CONFIRMED",
          "score": 80.0,
          "hard_gates": [{"code": "SPREAD_OK", "passed": True}],
          "blockers": [],
        },
        "momentum": {
          "phase": "IDLE",
          "score": None,
          "hard_gates": [],
          "blockers": [],
        },
      },
    }
  )
  state = strategy._empty_instrument_state()
  state.update(
    {
      "pending_entry_intent_id": intent_id,
      "entry_order_status": "AWAITING_APPROVAL",
      "status": "AWAITING_APPROVAL",
      "opportunity": opportunity,
      **overrides,
    }
  )
  return state


def make_v3_latched_state(
  strategy: AshareIntradayTAssistantStrategy,
  intent_id: str,
):
  state = make_v3_pending_state(strategy, intent_id)
  opportunity = dict(state["opportunity"])
  opportunity.update(
    {
      "candidate_status": CandidateStatus.LATCHED.value,
      "candidate_awaiting_approval": False,
      "state_version": 6,
    }
  )
  evaluation = dict(opportunity.get("latest_evaluation") or {})
  evaluation.update(
    {
      "candidate_status": CandidateStatus.LATCHED.value,
      "candidate_state_version": 6,
      "signal_version": 6,
      "pending_entry_intent_id": None,
    }
  )
  opportunity["latest_evaluation"] = evaluation
  state.update(
    {
      "pending_entry_intent_id": "",
      "entry_order_status": "",
      "status": "OBSERVING",
      "opportunity": opportunity,
    }
  )
  return state


def make_v3_pending_intent(
  run_id: str,
  *,
  intent_id: str | None = None,
  reason: str = "v3_pending_candidate",
  metadata: dict | None = None,
) -> TradeIntent:
  resolved_intent_id = intent_id or "intent-v3-pending"
  candidate_id = f"candidate-{resolved_intent_id}"
  candidate_fingerprint = f"fingerprint-{resolved_intent_id}"
  return TradeIntent(
    strategy_id="1",
    run_id=run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason=reason,
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    metadata={
      "t_trade_role": "entry",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "opportunity_schema_version": 3,
      "signal_version": 7,
      "candidate_id": candidate_id,
      "candidate_fingerprint": candidate_fingerprint,
      "candidate_state_version": 7,
      "candidate_status": CandidateStatus.AWAITING_APPROVAL.value,
      "config_version": 0,
      "policy_version": _V3_POLICY.policy_version,
      **dict(metadata or {}),
    },
    intent_id=resolved_intent_id,
  )


def v3_approval_expectation(intent: TradeIntent) -> dict:
  metadata = dict(intent.metadata or {})
  return {
    "signal_version": metadata["signal_version"],
    "candidate_id": metadata["candidate_id"],
    "candidate_fingerprint": metadata["candidate_fingerprint"],
    "candidate_state_version": metadata["candidate_state_version"],
    "config_version": metadata["config_version"],
    "policy_version": metadata["policy_version"],
  }


def make_v3_recovery_runtime(
  run_id: str,
  *,
  state: dict,
) -> StrategyRuntime:
  context = StrategyContext(
    run_id=run_id,
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
      "signal_policy": _V3_POLICY.to_dict(),
    },
  )
  runtime = StrategyRuntime(
    run_id=run_id,
    name="v3-startup-recovery",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {"instrument_states": {"600000.SH": state}}
  )
  runtime.state_manager = FakeStateManager()
  return runtime


def make_recovery_executor(materialize: AsyncMock) -> StrategyExecutor:
  return StrategyExecutor(
    opportunity_runtime_service=SimpleNamespace(
      materialize_evaluation=materialize,
    ),
    opportunity_update_service=SimpleNamespace(
      notify_opportunity=AsyncMock(return_value=True),
      flush_opportunity_notices=AsyncMock(return_value=0),
    ),
  )


def allow_entry_emission(runtime: StrategyRuntime, instrument_code: str) -> None:
  account_id = str(runtime.context.parameters.get("account_id") or "")
  runtime.t_trade_intent_emission_by_instrument[instrument_code] = {
    "account_id": account_id,
    "run_id": runtime.run_id,
    "instrument_code": instrument_code,
    "eligible": True,
    "allowed": True,
    "blockers": [],
  }


def test_backtest_approval_uses_replay_clock_for_ttl_and_quote_age():
  executor = StrategyExecutor()
  signal_at = time_utils.now().replace(year=2024, month=1, day=2, hour=10)
  context = StrategyContext(
    run_id="run-historical-approval-clock",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"execution_quote_max_age_seconds": 3.0},
    current_time=signal_at + timedelta(seconds=2),
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="historical-approval-clock",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    replay_clock=ReplayClock(context.current_time),
  )
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=signal_at,
    price=10.0,
    ask_price=[10.0],
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="historical_clock",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=15_000,
    expiry_policy={
      "type": "TTL_MS",
      "expire_at_ms": int((signal_at + timedelta(seconds=15)).timestamp() * 1000),
    },
  )

  assert executor._approval_failure(runtime, intent) is None

  runtime.replay_clock.advance_to(signal_at + timedelta(seconds=4))
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_STALE"

  runtime.replay_clock.advance_to(signal_at + timedelta(seconds=15))
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_TTL_EXPIRED"


def test_manual_approval_fails_closed_for_missing_or_stale_execution_quote():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-fresh-quote",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"execution_quote_max_age_seconds": 3.0},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="fresh-quote",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="fresh_quote",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )

  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_MISSING"

  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=time_utils.now() - timedelta(seconds=4),
    price=10.0,
    ask_price=[10.0],
  )
  assert executor._approval_failure(runtime, intent)[0] == "APPROVAL_QUOTE_STALE"


@pytest.mark.asyncio
async def test_manual_approval_fails_closed_while_durable_barrier_is_active():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-durable-approval-barrier",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="durable-approval-barrier",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="durable_barrier",
    target_volume=100,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
  )
  runtime.pending_approvals[intent.intent_id] = intent
  runtime.durable_event_barrier_key = "trade:pending-report"
  executor.runs[runtime.run_id] = runtime

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is False
  assert result["code"] == "DURABLE_RECONCILIATION_REQUIRED"
  assert intent.intent_id in runtime.pending_approvals


def test_t_trade_approval_rechecks_single_amount_hard_cap():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-amount-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "max_trade_amount": 12_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 1.0,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="amount-cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=121.0, ask_price=[121.0]
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="amount_cap",
    target_volume=100,
    limit_price_hint=119.8,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={"t_trade_role": "entry"},
  )

  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure and failure[0] == "T_TRADE_SINGLE_AMOUNT_LIMIT"


@pytest.mark.parametrize("role", ["entry", "exit"])
def test_t_trade_approval_fails_closed_while_any_role_needs_reconciliation(role):
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-reconciliation-gate",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 1.0,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="reconciliation-gate",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          f"pending_{role}_intent_id": "intent-needs-reconcile",
          f"{role}_order_status": "RECONCILE_REQUIRED",
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  allow_entry_emission(runtime, "000001.SZ")
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="blocked_by_reconciliation",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={"t_trade_role": "entry"},
  )

  failure = executor._t_trade_portfolio_approval_failure(runtime, intent)

  assert failure and failure[0] == "T_TRADE_RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_manual_intent_waits_for_approval_before_routing():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id="run-approval",
    name="approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  allow_entry_emission(runtime, "600000.SH")
  runtime.status = ExecutionStatus.RUNNING
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    price=10.0,
    ask_price=[10.0],
  )
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="test",
    target_volume=100,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    max_price_deviation_bps=30,
  )

  await executor._process_strategy_output(runtime, StrategyOutput(trade_intents=[intent]))

  executor._process_trade_intent.assert_not_awaited()
  assert runtime.state_manager.records == [(intent.intent_id, "AWAITING_APPROVAL")]
  assert runtime.pending_approvals[intent.intent_id] is intent

  result = await executor.approve_trade_intent(runtime.run_id, intent.intent_id)

  assert result["success"] is True
  executor._process_trade_intent.assert_awaited_once_with(runtime, intent)
  assert intent.intent_id not in runtime.pending_approvals


@pytest.mark.asyncio
async def test_v3_manual_intent_expires_fail_closed_after_executor_restart():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-restored-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
      "signal_policy": _V3_POLICY.to_dict(),
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="restored-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent = make_v3_pending_intent(runtime.run_id)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent.intent_id,
        )
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  allow_entry_emission(runtime, "600000.SH")
  runtime.state_manager.restored_intent = intent

  await executor._restore_pending_manual_approvals(runtime)

  assert runtime.pending_approvals == {}
  assert runtime.state_manager.updates[-1][0:2] == (intent.intent_id, "EXPIRED")
  assert runtime.state_manager.updates[-1][2]["notes"] == (
    "APPROVAL_SIGNAL_INVALIDATED"
  )
  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["opportunity"]["candidate_status"] == (
    CandidateStatus.SUPPRESSED.value
  )
  assert state["opportunity"]["candidate_awaiting_approval"] is False


@pytest.mark.asyncio
async def test_startup_suppresses_latched_candidate_when_crash_precedes_intent():
  intent_id = "intent-crash-before-persist"
  bootstrap = AshareIntradayTAssistantStrategy(
    StrategyContext(
      run_id="run-crash-before-persist",
      mode=StrategyRunMode.PAPER,
      instruments=["600000.SH"],
      parameters={"account_id": "account-1"},
    )
  )
  runtime = make_v3_recovery_runtime(
    "run-crash-before-persist",
    state=make_v3_latched_state(bootstrap, intent_id),
  )
  runtime.state_manager.recovery_records = []
  materialize = AsyncMock(return_value=None)
  executor = make_recovery_executor(materialize)
  executor._process_trade_intent = AsyncMock()

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert runtime.pending_approvals == {}
  assert runtime.state_manager.updates == []
  assert runtime.state_manager.force_save_count == 2
  assert state["opportunity"]["candidate_status"] == "SUPPRESSED"
  assert state["pending_entry_intent_id"] == ""
  executor._process_trade_intent.assert_not_awaited()
  materialized = materialize.await_args.kwargs["event"]
  assert materialized["record_kind"] == "MATERIAL"
  assert materialized["event_type"] == "CANDIDATE_SUPPRESSED"
  assert materialized["transition"]["reason"] == (
    "T_TRADE_STARTUP_ORPHAN_LATCHED_CANDIDATE"
  )


@pytest.mark.asyncio
async def test_startup_recovery_uses_global_monitor_delegated_account_lock():
  runtime = make_v3_recovery_runtime(
    "run-delegated-account-lock",
    state={},
  )
  runtime.state_manager.recovery_records = []
  executor = make_recovery_executor(AsyncMock(return_value=None))
  lock = t_trade_account_coordination_lock("account-1")

  async with lock:
    await asyncio.wait_for(
      executor._restore_pending_manual_approvals(
        runtime,
        t_trade_account_coordination_held=True,
      ),
      timeout=0.5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state_kind", ["LATCHED", "AWAITING_APPROVAL"])
async def test_startup_rejects_orphan_pending_intent_across_both_crash_windows(
  state_kind: str,
):
  run_id = f"run-crash-pending-{state_kind.lower()}"
  intent_id = f"intent-crash-pending-{state_kind.lower()}"
  bootstrap = AshareIntradayTAssistantStrategy(
    StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.PAPER,
      instruments=["600000.SH"],
      parameters={"account_id": "account-1"},
    )
  )
  state = (
    make_v3_latched_state(bootstrap, intent_id)
    if state_kind == "LATCHED"
    else make_v3_pending_state(bootstrap, intent_id)
  )
  runtime = make_v3_recovery_runtime(run_id, state=state)
  intent = make_v3_pending_intent(run_id, intent_id=intent_id)
  runtime.state_manager.recovery_records = [
    SimpleNamespace(intent=intent, durable_status="PENDING")
  ]
  materialize = AsyncMock(return_value=None)
  executor = make_recovery_executor(materialize)
  executor._process_trade_intent = AsyncMock()

  await executor._restore_pending_manual_approvals(runtime)

  assert runtime.state_manager.updates[0][0:2] == (intent_id, "REJECTED")
  assert runtime.state_manager.updates[0][2]["notes"] == (
    "T_TRADE_STARTUP_ORPHAN_PENDING_INTENT"
  )
  assert runtime.pending_approvals == {}
  assert runtime.state_manager.force_save_count == 2
  recovered = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert recovered["opportunity"]["candidate_status"] == "SUPPRESSED"
  executor._process_trade_intent.assert_not_awaited()
  assert materialize.await_count == 1


@pytest.mark.asyncio
async def test_startup_recovery_is_idempotent_after_audit_before_checkpoint_crash():
  run_id = "run-crash-recovery-idempotent"
  intent_id = "intent-crash-recovery-idempotent"
  bootstrap = AshareIntradayTAssistantStrategy(
    StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.PAPER,
      instruments=["600000.SH"],
      parameters={"account_id": "account-1"},
    )
  )
  original_state = make_v3_latched_state(bootstrap, intent_id)
  pending_intent = make_v3_pending_intent(run_id, intent_id=intent_id)
  materialize = AsyncMock(return_value=None)
  executor = make_recovery_executor(materialize)

  first = make_v3_recovery_runtime(run_id, state=original_state)
  first.state_manager.recovery_records = [
    SimpleNamespace(intent=pending_intent, durable_status="PENDING")
  ]
  first.state_manager.force_save_results = [False]
  with pytest.raises(RuntimeError, match="启动抑制状态保存失败"):
    await executor._restore_pending_manual_approvals(first)

  recovered_intent = make_v3_pending_intent(
    run_id,
    intent_id=intent_id,
    metadata={"startup_recovery": True},
  )
  second = make_v3_recovery_runtime(run_id, state=original_state)
  second.state_manager.recovery_records = [
    SimpleNamespace(intent=recovered_intent, durable_status="REJECTED")
  ]
  await executor._restore_pending_manual_approvals(second)

  assert materialize.await_count == 1
  first_event = next(iter(first.state_manager.material_outbox.values()))
  second_event = materialize.await_args.kwargs["event"]
  assert first_event["event_key"] == second_event["event_key"]
  assert second.state_manager.updates == []
  assert second.state_manager.force_save_count == 2
  assert second.strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]["candidate_status"] == "SUPPRESSED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "row_run_id,row_account_id",
  [("another-run", "account-1"), ("run-recovery-scope", "another-account")],
)
async def test_startup_recovery_rejects_cross_run_or_account_rows(
  row_run_id: str,
  row_account_id: str,
):
  run_id = "run-recovery-scope"
  intent_id = "intent-recovery-scope"
  bootstrap = AshareIntradayTAssistantStrategy(
    StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.PAPER,
      instruments=["600000.SH"],
      parameters={"account_id": "account-1"},
    )
  )
  runtime = make_v3_recovery_runtime(
    run_id,
    state=make_v3_latched_state(bootstrap, intent_id),
  )
  intent = make_v3_pending_intent(
    row_run_id,
    intent_id=intent_id,
    metadata={"account_id": row_account_id},
  )
  runtime.state_manager.recovery_records = [
    SimpleNamespace(intent=intent, durable_status="PENDING")
  ]
  materialize = AsyncMock(return_value=None)
  executor = make_recovery_executor(materialize)

  with pytest.raises(RuntimeError, match="意图作用域无效"):
    await executor._restore_pending_manual_approvals(runtime)

  assert runtime.state_manager.updates == []
  assert runtime.state_manager.force_save_count == 0
  materialize.assert_not_awaited()
  assert runtime.strategy.state["instrument_states"]["600000.SH"][
    "opportunity"
  ]["candidate_status"] == "LATCHED"


@pytest.mark.asyncio
async def test_restore_converges_expired_durable_intent_and_clears_snapshot_gate():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-expired-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="expired-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-expired-before-restart"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent_id,
          batch_id="batch-expired-before-restart",
        )
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    strategy_run_id=runtime.run_id,
    status="EXPIRED",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert runtime.pending_approvals == {}
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "EXPIRED"
  assert state["status"] == "OBSERVING"
  assert state["batch_id"] == ""


@pytest.mark.asyncio
async def test_restore_converges_submitted_durable_intent_without_duplicate_entry():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-submitted-approval",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="submitted-approval",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-submitted-before-restart"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent_id,
        )
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    strategy_run_id=runtime.run_id,
    status="SUBMITTED",
    order_id="broker-order-submitted",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert runtime.pending_approvals == {}
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "SUBMITTED"
  assert state["status"] == "ENTRY_SUBMITTED"


@pytest.mark.asyncio
async def test_restore_requires_reconciliation_for_approved_without_order_id():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-orphaned-approved",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="orphaned-approved",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-approved-without-order"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent_id,
          batch_id="batch-orphaned-approved",
        )
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    strategy_run_id=runtime.run_id,
    status="APPROVED",
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["batch_id"] == "batch-orphaned-approved"
  assert state["reconciliation_reason"] == (
    "APPROVED_WITHOUT_DURABLE_ORDER_CORRELATION"
  )
  assert runtime.state_manager.updates == []


@pytest.mark.asyncio
async def test_restore_filled_intent_waits_for_idempotent_inbox_replay():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-filled-recovery",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="filled-recovery",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  intent_id = "intent-filled-before-snapshot"
  batch_id = "batch-filled-before-snapshot"
  plan_id = "t-exit-filled-before-snapshot"
  metadata = make_t_entry_metadata(
    runtime.strategy,
    batch_id=batch_id,
    plan_id=plan_id,
  )
  policy = runtime.strategy._exit_policy_snapshot()
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent_id,
          batch_id=batch_id,
          exit_plan_id=plan_id,
          entry_filled_volume=100,
          entry_avg_price=10.0,
          exit_policy_snapshot=policy,
        )
      }
    }
  )
  runtime.exit_plan_book.register_entry_fill(
    metadata["exit_plan_template"],
    volume=100,
    price=10.0,
    trade_time=datetime(2026, 7, 13, 9, 59),
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    strategy_run_id=runtime.run_id,
    status="FILLED",
    order_id="broker-order-filled",
    executed_volume=200,
    executed_price=10.5,
    metadata=metadata,
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  plan = runtime.exit_plan_book.plans[plan_id]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["reconciliation_reason"].startswith(
    "DURABLE_FILL_AWAITS_IDEMPOTENT_INBOX_REPLAY"
  )
  assert state["entry_filled_volume"] == 100
  assert state["entry_avg_price"] == pytest.approx(10.0)
  assert plan.entry_filled_volume == 100
  assert plan.entry_avg_price == pytest.approx(10.0)

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="broker-order-filled",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=11.0,
      volume=100,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata=metadata,
    ),
  )
  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="broker-order-filled",
      status="FILLED",
      filled_volume=200,
      metadata={**metadata, "intent_id": intent_id},
    ),
  )

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "FILLED"
  assert state["reconciliation_reason"] == ""
  assert state["entry_filled_volume"] == 200
  assert state["entry_avg_price"] == pytest.approx(10.5)
  assert runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 200


@pytest.mark.asyncio
async def test_restore_cancelled_partial_fill_keeps_open_lot_and_blocks_new_entry():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-cancelled-partial-recovery",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "position_shares": 1000,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="cancelled-partial-recovery",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  await runtime.strategy.initialize()
  intent_id = "intent-cancelled-after-partial-fill"
  batch_id = "batch-cancelled-after-partial-fill"
  plan_id = "t-exit-cancelled-after-partial-fill"
  metadata = make_t_entry_metadata(
    runtime.strategy,
    batch_id=batch_id,
    plan_id=plan_id,
  )
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": make_v3_pending_state(
          runtime.strategy,
          intent_id,
          batch_id=batch_id,
          exit_plan_id=plan_id,
          entry_filled_volume=0,
          entry_avg_price=0.0,
          exit_policy_snapshot=runtime.strategy._exit_policy_snapshot(),
        )
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.state_manager.durable_snapshot = make_durable_entry_snapshot(
    intent_id,
    strategy_run_id=runtime.run_id,
    status="CANCELLED",
    order_id="broker-order-cancelled",
    executed_volume=100,
    executed_price=10.0,
    metadata=metadata,
  )

  await executor._restore_pending_manual_approvals(runtime)

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == intent_id
  assert state["entry_order_status"] == "RECONCILE_REQUIRED"
  assert state["status"] == "RECONCILE_REQUIRED"
  assert state["entry_filled_volume"] == 0
  start = datetime(2026, 7, 13, 9, 30)
  for seconds, price in ((0, 100.0), (60, 99.0), (80, 99.3)):
    tick_at = start + timedelta(seconds=seconds)
    output = await runtime.strategy.step(
      StrategyInput(
        run_id=runtime.run_id,
        strategy_id="1",
        timestamp=tick_at,
        cadence=StrategyCadence.TICK,
        instrument_code="600000.SH",
        event=SimpleNamespace(
          last_price=price,
          bid_price=[price - 0.01],
          ask_price=[price],
          amount=995_000.0,
          pvolume=10_000.0,
        ),
      )
    )
    assert output.trade_intents == []

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="broker-order-cancelled",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      trade_time=datetime(2026, 7, 13, 10, 0),
      metadata=metadata,
    ),
  )
  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="broker-order-cancelled",
      status="CANCELLED",
      filled_volume=100,
      metadata={**metadata, "intent_id": intent_id},
    ),
  )

  state = runtime.strategy.state["instrument_states"]["600000.SH"]
  assert state["pending_entry_intent_id"] == ""
  assert state["entry_order_status"] == "CANCELLED"
  assert state["status"] == "MONITORING"
  assert state["entry_filled_volume"] == 100


def test_t_entry_amount_reservation_is_restored_for_restart():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-approved-restart",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="approved-restart",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="restart_window",
    target_amount=1_000.0,
    limit_price_hint=10.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    approval_ttl_ms=30_000,
    metadata={
      "t_trade_role": "entry",
      "t_batch_id": "batch-restart",
      "instrument_code": "600000.SH",
    },
  )
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "pending_entry_intent_id": intent.intent_id,
          "entry_order_status": "PENDING",
          "requested_entry_amount": 1_000.0,
          "batch_id": "batch-restart",
          "last_price": 10.0,
          "opportunity": {
            "latest_evaluation": {"features": {"price": 9.8}}
          },
        }
      }
    }
  )
  executor._restore_t_trade_entry_reservations(runtime)

  reservation = runtime.t_trade_entry_reservations[intent.intent_id]
  assert reservation["volume"] == 0
  assert reservation["requested_volume"] == 0
  assert reservation["requested_amount"] == pytest.approx(1_000.0)
  assert reservation["price"] == pytest.approx(9.8)
  assert reservation["amount"] == pytest.approx(1_000.0)


@pytest.mark.asyncio
async def test_t_trade_account_batch_limit_keeps_signal_pending():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 1,
      "max_total_t_exposure_pct": 0.1,
      "signal_policy": _V3_POLICY.to_dict(),
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "000001.SZ": {
          "instrument_code": "000001.SZ",
          "batch_id": "batch-other",
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
          "entry_avg_price": 10.0,
        }
      }
    }
  )
  runtime.state_manager = FakeStateManager()
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=time_utils.now(),
    price=10.0,
    ask_price=[10.0],
  )
  executor.runs[runtime.run_id] = runtime
  executor._process_trade_intent = AsyncMock()
  intent = make_v3_pending_intent(
    runtime.run_id,
    intent_id="intent-cap-v3",
    reason="cap",
  )
  runtime.pending_approvals[intent.intent_id] = intent
  instrument_states = dict(runtime.strategy.state.get("instrument_states") or {})
  instrument_states["600000.SH"] = make_v3_pending_state(
    runtime.strategy,
    intent.intent_id,
  )
  runtime.strategy.state.set("instrument_states", instrument_states)
  allow_entry_emission(runtime, "600000.SH")

  result = await executor.approve_trade_intent(
    runtime.run_id,
    intent.intent_id,
    approval_expectation=v3_approval_expectation(intent),
  )

  assert result["success"] is False
  assert result["code"] == "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED"
  assert intent.intent_id in runtime.pending_approvals
  executor._process_trade_intent.assert_not_awaited()


def test_partial_fill_reservation_counts_as_one_batch():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-partial-cap",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 2,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="partial-cap",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "000001.SZ": {
          "batch_id": "batch-partial",
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
          "entry_avg_price": 10.0,
        }
      }
    }
  )
  runtime.t_trade_entry_reservations["intent-partial"] = {
    "batch_id": "batch-partial",
    "instrument_code": "000001.SZ",
    "volume": 100,
    "price": 10.0,
    "amount": 1000.0,
  }
  runtime.state_manager = FakeStateManager()
  allow_entry_emission(runtime, "600000.SH")
  runtime.latest_market_data["600000.SH"] = MarketDataSnapshot(
    instrument_code="600000.SH", price=10.0, ask_price=[10.0]
  )
  intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="next_batch",
    target_volume=100,
    limit_price_hint=10.0,
    metadata={"t_trade_role": "entry", "t_batch_id": "batch-next"},
  )

  assert executor._t_trade_portfolio_approval_failure(runtime, intent) is None


@pytest.mark.asyncio
async def test_filled_order_keeps_exposure_reserved_until_trade_detail_arrives():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-filled-before-trade",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH", "000001.SZ"],
    parameters={
      "account_id": "account-1",
      "max_concurrent_batches": 1,
      "max_total_t_exposure_pct": 0.1,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="filled-before-trade",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = FakeStateManager()
  intent_id = "intent-filled-before-trade"
  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "batch_id": "batch-filled-before-trade",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "PENDING",
          "entry_filled_volume": 0,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  runtime.t_trade_entry_reservations[intent_id] = {
    "batch_id": "batch-filled-before-trade",
    "instrument_code": "600000.SH",
    "requested_volume": 100,
    "volume": 100,
    "price": 10.0,
    "amount": 1000.0,
  }
  allow_entry_emission(runtime, "000001.SZ")
  metadata = {
    "t_trade_role": "entry",
    "t_batch_id": "batch-filled-before-trade",
    "instrument_code": "600000.SH",
    "intent_id": intent_id,
  }

  await executor._notify_strategy_order(
    runtime,
    OrderStateEvent(
      order_id="order-1",
      status="FILLED",
      filled_volume=100,
      metadata=metadata,
    ),
  )

  assert runtime.t_trade_entry_reservations[intent_id]["amount"] == 1000.0
  next_intent = TradeIntent(
    strategy_id="1",
    run_id=runtime.run_id,
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="next_batch",
    target_volume=100,
    limit_price_hint=10.0,
    metadata={"t_trade_role": "entry", "t_batch_id": "batch-next"},
  )
  failure = executor._t_trade_portfolio_approval_failure(runtime, next_intent)
  assert failure and failure[0] == "T_TRADE_RECONCILIATION_REQUIRED"

  await executor._notify_strategy_trade(
    runtime,
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="600000.SH",
      trade_type="BUY",
      price=10.0,
      volume=100,
      metadata=metadata,
    ),
  )

  assert intent_id not in runtime.t_trade_entry_reservations
  assert (
    runtime.strategy.state["instrument_states"]["600000.SH"][
      "entry_filled_volume"
    ]
    == 100
  )
