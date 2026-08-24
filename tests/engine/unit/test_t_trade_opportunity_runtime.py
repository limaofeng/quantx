from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3 import (
  MaterializeEvaluationAfterCAS,
  ReadD1ReferenceProfile,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine import strategy_executor as strategy_executor_module
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_engine.t_trade_coordination import t_trade_account_coordination_lock
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


class _OpportunityStrategy(StrategyBase):
  USES_T_TRADE_OPPORTUNITY_PROFILE = True

  def __init__(self, context: StrategyContext, calls: list[str] | None = None) -> None:
    self.calls = calls if calls is not None else []
    super().__init__(context)

  @property
  def name(self) -> str:
    return "V3 opportunity test strategy"

  @property
  def version(self) -> str:
    return "3.0.0"

  @property
  def description(self) -> str:
    return "test"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {}

  async def on_init(self) -> None:
    return None

  async def on_stop(self) -> None:
    return None

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()

  def mark_candidate_awaiting_approval(
    self,
    instrument_code: str,
    candidate_id: str,
    intent_id: str,
    *,
    source_time_ms: int,
  ) -> RuntimeStatePatch:
    self.calls.append("hook")
    opportunity = dict(self.state.get("opportunity", {}) or {})
    opportunity.update(
      {
        "candidate_status": "AWAITING_APPROVAL",
        "candidate": {"candidate_id": candidate_id},
      }
    )
    return RuntimeStatePatch(
      set={
        "opportunity": opportunity,
        "pending_entry_intent_id": intent_id,
        "entry_order_status": "AWAITING_APPROVAL",
        "awaiting": {
          "instrument_code": instrument_code,
          "candidate_id": candidate_id,
          "intent_id": intent_id,
          "source_time_ms": source_time_ms,
        },
      },
      append_events=[
        {
          "type": "T_TRADE_OPPORTUNITY_EVALUATION",
          "event_key": f"intent-linked:{intent_id}",
          "record_kind": "MATERIAL",
          "event_type": "INTENT_LINKED",
          "instrument_code": instrument_code,
          "evaluated_at_ms": source_time_ms,
          "signal_snapshot": _snapshot(),
        }
      ],
    )

  async def on_order(self, event: OrderStateEvent) -> RuntimeStatePatch | None:
    metadata = dict(event.metadata or {})
    intent_id = str(metadata.get("intent_id") or "")
    candidate_id = str(metadata.get("candidate_id") or "")
    awaiting = dict(self.state.get("awaiting", {}) or {})
    opportunity = dict(self.state.get("opportunity", {}) or {})
    candidate = dict(opportunity.get("candidate", {}) or {})
    current_status = str(opportunity.get("candidate_status") or "").upper()
    current_pending = str(self.state.get("pending_entry_intent_id") or "")
    if (
      str(event.status or "").upper() not in {"REJECTED", "EXPIRED"}
      or candidate_id != str(candidate.get("candidate_id") or "")
      or current_status not in {"LATCHED", "AWAITING_APPROVAL"}
      or (current_pending and intent_id != current_pending)
      or (
        current_status == "AWAITING_APPROVAL"
        and intent_id != str(awaiting.get("intent_id") or "")
      )
    ):
      return None
    self.calls.append("compensation_order")
    source_time_ms = int(metadata.get("source_time_ms") or 0)
    snapshot = {
      **_snapshot(),
      "evaluated_at_ms": source_time_ms,
      "source_time_ms": source_time_ms,
      "candidate_status": "SUPPRESSED",
      "pending_entry_intent_id": None,
    }
    return RuntimeStatePatch(
      set={
        "opportunity": {**opportunity, "candidate_status": "SUPPRESSED"},
        "pending_entry_intent_id": "",
        "entry_order_status": str(event.status or "").upper(),
        "awaiting": {},
      },
      append_events=[
        {
          "type": "T_TRADE_OPPORTUNITY_EVALUATION",
          "event_key": f"candidate-suppressed:{intent_id}",
          "record_kind": "MATERIAL",
          "event_type": "CANDIDATE_SUPPRESSED",
          "instrument_code": str(metadata.get("instrument_code") or ""),
          "evaluated_at_ms": source_time_ms,
          "signal_snapshot": snapshot,
          "transition": {
            "candidate_id": str(metadata.get("candidate_id") or ""),
            "from": "AWAITING_APPROVAL",
            "to": "SUPPRESSED",
            "reason": str(metadata.get("approval_reason") or ""),
          },
          "intent_link": {"intent_id": intent_id},
        }
      ],
    )


class _StateManager:
  def __init__(
    self,
    calls: list[str],
    *,
    checkpoints: list[bool] | None = None,
    fail_statuses: set[str] | None = None,
    fail_intent_record: bool = False,
    snapshot_failure_code: str | None = None,
  ) -> None:
    self.calls = calls
    self.checkpoints = list(checkpoints or [True, True])
    self.status_updates: list[tuple[str, str]] = []
    self.fail_statuses = set(fail_statuses or set())
    self.fail_intent_record = fail_intent_record
    self.last_snapshot_failure_code = snapshot_failure_code
    self.material_outbox: dict[str, dict[str, object]] = {}
    self.quota: dict[str, object] = {"total_asset": 100_000.0}
    self.drain_capture_state: list[bool] = []

  async def checkpoint_strategy_state_changes(self) -> bool:
    self.calls.append("checkpoint")
    return self.checkpoints.pop(0)

  async def drain_strategy_state_changes(
    self,
    *,
    capture_state: bool = True,
  ) -> bool:
    self.drain_capture_state.append(capture_state)
    self.calls.append("drain")
    return True

  async def record_trade_intent_strict(
    self,
    _intent: TradeIntent,
    *,
    status: str,
  ) -> None:
    self.calls.append(f"intent:{status}")
    if self.fail_intent_record:
      raise RuntimeError("intent persistence failed")

  async def update_trade_intent_status(
    self,
    intent_id: str,
    status: str,
    **_updates: object,
  ) -> None:
    self.status_updates.append((intent_id, status))

  async def update_trade_intent_status_strict(
    self,
    intent_id: str,
    status: str,
    **_updates: object,
  ) -> None:
    self.calls.append(f"status:{status}")
    if status in self.fail_statuses:
      raise RuntimeError(f"status persistence failed: {status}")
    self.status_updates.append((intent_id, status))

  def enqueue_t_trade_material_events(
    self,
    events: list[dict[str, object]],
  ) -> None:
    for event in events:
      event_key = str(event.get("event_key") or "")
      if event_key:
        self.material_outbox.setdefault(event_key, dict(event))

  def pending_t_trade_material_events(self) -> list[dict[str, object]]:
    return [dict(event) for event in self.material_outbox.values()]

  def acknowledge_t_trade_material_events(self, event_keys: list[str]) -> None:
    for event_key in event_keys:
      self.material_outbox.pop(event_key, None)

  async def force_save(self) -> bool:
    return True

  def record_decision_trace(self, _trace: object) -> None:
    return None

  def get_account_quota(self) -> dict[str, object]:
    return dict(self.quota)

  def get_all_positions(self) -> dict[str, object]:
    return {}

  def get_bucket_ledger_snapshot(self) -> dict[str, object]:
    return {}


def _runtime(
  calls: list[str],
  *,
  checkpoints: list[bool] | None = None,
  fail_statuses: set[str] | None = None,
  fail_intent_record: bool = False,
  snapshot_failure_code: str | None = None,
) -> StrategyRuntime:
  context = StrategyContext(
    run_id="run-opportunity-v3",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={
      "account_id": "account-1",
      "target_trade_amount": 10_000.0,
      "max_concurrent_batches": 3,
      "max_total_t_exposure_pct": 0.2,
    },
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="opportunity-v3",
    strategy_id=1,
    strategy_class=_OpportunityStrategy,
    context=context,
    status=ExecutionStatus.RUNNING,
  )
  runtime.strategy = _OpportunityStrategy(context, calls)
  runtime.strategy.state["instrument_states"] = {}
  runtime.state_manager = _StateManager(
    calls,
    checkpoints=checkpoints,
    fail_statuses=fail_statuses,
    fail_intent_record=fail_intent_record,
    snapshot_failure_code=snapshot_failure_code,
  )
  runtime.t_trade_intent_emission_by_instrument["600000.SH"] = {
    "account_id": "account-1",
    "run_id": context.run_id,
    "instrument_code": "600000.SH",
    "eligible": True,
    "allowed": True,
    "blockers": [],
  }
  return runtime


def _executor(service, update_service=None, outcome_facade=None) -> StrategyExecutor:
  return StrategyExecutor(
    opportunity_runtime_service=service,
    candidate_outcome_facade=outcome_facade,
    opportunity_update_service=(
      update_service
      or SimpleNamespace(
        notify_opportunity=AsyncMock(return_value=True),
        flush_opportunity_notices=AsyncMock(return_value=0),
      )
    ),
  )


@pytest.mark.asyncio
async def test_material_evaluation_is_seeded_only_after_durable_materialization():
  calls: list[str] = []

  async def materialize(**_kwargs: object) -> None:
    calls.append("evaluation")

  async def seed(**_kwargs: object) -> None:
    calls.append("outcome")

  executor = _executor(
    SimpleNamespace(materialize_evaluation=materialize),
    outcome_facade=SimpleNamespace(seed_material_event=seed),
  )
  materializer_execute = AsyncMock(wraps=executor._evaluation_materializer.execute)
  executor._evaluation_materializer.execute = materializer_execute
  runtime = _runtime(calls)
  await executor._process_strategy_output(
    runtime,
    StrategyOutput(runtime_state_patch=_candidate_patch()),
    _input(),
  )

  assert calls == ["checkpoint", "evaluation", "outcome"]
  assert isinstance(executor._evaluation_materializer, MaterializeEvaluationAfterCAS)
  materializer_execute.assert_awaited_once()
  assert materializer_execute.await_args.args[0].cas_committed is True


@pytest.mark.asyncio
async def test_durable_runtime_never_starts_periodic_hot_state_snapshot(
  tmp_path,
) -> None:
  """Every policy is coordinated by explicit seals, never a timer CAS."""

  durable_backtest = RuntimeStateManager(
    run_id="run-durable-backtest-no-periodic-snapshot",
    persist_enabled=True,
    is_backtest=True,
    log_dir=str(tmp_path),
  )
  normal_durable_runtime = RuntimeStateManager(
    run_id="run-normal-durable-periodic-snapshot",
    persist_enabled=True,
    log_dir=str(tmp_path),
  )
  try:
    await durable_backtest.start()
    await normal_durable_runtime.start()

    assert durable_backtest._snapshot_task is None
    assert normal_durable_runtime._snapshot_task is None
  finally:
    await durable_backtest.abort_without_final_snapshot()
    await normal_durable_runtime.abort_without_final_snapshot()


@pytest.mark.asyncio
async def test_ignored_opportunity_output_does_not_enter_durability_chain():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(calls)

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      decision_tags=["opportunity_tick_ignored", "no_trade"],
      trace_payload={
        "accepted": False,
        "ignored": True,
        "reason": "DUPLICATE_SOURCE_IDENTITY",
      },
    ),
    _input(),
  )

  assert calls == []
  service.materialize_evaluation.assert_not_awaited()
  assert runtime.state_manager.material_outbox == {}
  executor.opportunity_update_service.notify_opportunity.assert_not_awaited()


def _snapshot() -> dict[str, object]:
  return {
    "evaluated_at_ms": 1_724_300_000_000,
    "source_time_ms": 1_724_300_000_000,
    "tick_ordinal": 3,
    "continuity_generation": "1",
    "features": {"sample_count": 25},
    "pullback": {"phase": "REBOUND_CONFIRMING"},
    "momentum": {"phase": "BASELINING"},
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "signal_version": 7,
    "candidate_state_version": 7,
    "state_schema_version": 3,
    "feature_schema_version": 1,
    "policy_version": "t_trade_opportunity_v3.0.0",
    "config_version": 3,
  }


def _event() -> dict[str, object]:
  return {
    "type": "T_TRADE_OPPORTUNITY_EVALUATION",
    "event_key": "run-opportunity-v3:600000.SH:candidate-1:MATERIAL",
    "record_kind": "MATERIAL",
    "event_type": "CANDIDATE_LATCHED",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": 1_724_300_000_000,
    "signal_snapshot": _snapshot(),
  }


def _candidate_patch() -> RuntimeStatePatch:
  return RuntimeStatePatch(
    set={
      "opportunity": {
        "candidate_status": "LATCHED",
        "candidate": {"candidate_id": "candidate-1"},
      }
    },
    append_events=[_event()],
  )


def _intent() -> TradeIntent:
  return TradeIntent(
    strategy_id="1",
    run_id="run-opportunity-v3",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="V3_OPPORTUNITY",
    target_amount=10_000.0,
    execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
    metadata={
      "t_trade_role": "entry",
      "opportunity_schema_version": 3,
      "candidate_id": "candidate-1",
      "candidate_status": "AWAITING_APPROVAL",
    },
  )


def _input() -> StrategyInput:
  timestamp = datetime(2026, 8, 23, 10, 0)
  return StrategyInput(
    run_id="run-opportunity-v3",
    strategy_id="1",
    timestamp=timestamp,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
  )


def _assert_candidate_suppressed(
  runtime: StrategyRuntime,
  intent: TradeIntent,
) -> None:
  assert runtime.pending_approvals == {}
  assert runtime.strategy.state.opportunity["candidate_status"] == "SUPPRESSED"
  assert runtime.strategy.state.pending_entry_intent_id == ""
  assert runtime.strategy.state.entry_order_status in {"REJECTED", "EXPIRED"}
  assert runtime.strategy.state.awaiting == {}
  assert intent.intent_id not in runtime.pending_approvals


def _batched_diagnostic_output(ordinal: int) -> StrategyOutput:
  source_time_ms = 1_724_300_000_000 + ordinal
  event = {
    "type": "T_TRADE_OPPORTUNITY_EVALUATION",
    "event_key": f"run-opportunity-v3:600000.SH:diagnostic:{ordinal}",
    "record_kind": "COALESCED_DIAGNOSTIC",
    "event_type": "HEARTBEAT",
    "instrument_code": "600000.SH",
    "evaluated_at_ms": source_time_ms,
    "signal_snapshot": {
      **_snapshot(),
      "evaluated_at_ms": source_time_ms,
      "source_time_ms": source_time_ms,
      "tick_ordinal": ordinal,
    },
  }
  return StrategyOutput(runtime_state_patch=RuntimeStatePatch(append_events=[event]))


@pytest.mark.asyncio
async def test_candidate_is_exposed_only_after_checkpoint_evaluation_intent_and_hook():
  calls: list[str] = []
  service = SimpleNamespace()

  async def materialize(**_kwargs: object) -> None:
    calls.append("evaluation")

  service.materialize_evaluation = materialize
  executor = _executor(service)
  runtime = _runtime(calls)
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == [
    "checkpoint",
    "evaluation",
    "intent:PENDING",
    "hook",
    "checkpoint",
    "evaluation",
    "status:AWAITING_APPROVAL",
  ]
  assert runtime.pending_approvals == {intent.intent_id: intent}
  assert runtime.strategy.state.awaiting["intent_id"] == intent.intent_id
  assert service.materialize_evaluation is materialize
  executor.opportunity_update_service.notify_opportunity.assert_awaited_once()
  assert runtime.state_manager.material_outbox == {}
  assert (
    executor.opportunity_update_service.notify_opportunity.await_args.kwargs[
      "immediate"
    ]
    is True
  )


@pytest.mark.asyncio
async def test_account_coordination_in_progress_suppresses_before_strict_recorder():
  calls: list[str] = []
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))
  runtime = _runtime(calls)
  intent = _intent()
  lock = t_trade_account_coordination_lock("account-1")
  await lock.acquire()
  try:
    await executor._process_strategy_output(
      runtime,
      StrategyOutput(
        trade_intents=[intent],
        runtime_state_patch=_candidate_patch(),
      ),
      _input(),
    )
  finally:
    lock.release()

  assert not any(item.startswith("intent:") for item in calls)
  _assert_candidate_suppressed(runtime, intent)
  assert (
    runtime._t_trade_opportunity_failures["600000.SH"]["code"]
    == "T_TRADE_ACCOUNT_COORDINATION_IN_PROGRESS"
  )


@pytest.mark.asyncio
async def test_account_facts_toctou_recheck_suppresses_before_strict_recorder():
  calls: list[str] = []
  runtime_holder: dict[str, StrategyRuntime] = {}

  async def materialize(**_kwargs: object) -> None:
    runtime = runtime_holder["runtime"]
    runtime.state_manager.quota = {"total_asset": 100_000.0}
    runtime.context.parameters["max_concurrent_batches"] = 1
    runtime.t_trade_entry_reservations["other-intent"] = {
      "instrument_code": "000001.SZ",
      "batch_id": "other-batch",
      "amount": 1_000.0,
    }

  executor = _executor(SimpleNamespace(materialize_evaluation=materialize))
  runtime = _runtime(calls)
  runtime_holder["runtime"] = runtime
  intent = _intent()

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  assert not any(item.startswith("intent:") for item in calls)
  _assert_candidate_suppressed(runtime, intent)
  assert (
    runtime._t_trade_opportunity_failures["600000.SH"]["code"]
    == "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED"
  )
  assert runtime._t_trade_opportunity_failures["600000.SH"]["compensation"][
    intent.intent_id
  ]["evaluation_materialized"] is True


@pytest.mark.asyncio
async def test_candidate_and_invalidation_interleave_without_deadlock():
  calls: list[str] = []
  events: list[str] = []
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))
  runtime = _runtime(calls)
  executor.runs[runtime.run_id] = runtime
  runtime.strategy.state["exit_plan"] = {"status": "ACTIVE"}
  intent = _intent()
  strict_started = asyncio.Event()
  release_strict = asyncio.Event()
  config_waiting = asyncio.Event()
  original_record = runtime.state_manager.record_trade_intent_strict

  async def blocked_record(candidate, *, status):
    events.append("strict-start")
    strict_started.set()
    await release_strict.wait()
    await original_record(candidate, status=status)
    events.append("strict-done")

  runtime.state_manager.record_trade_intent_strict = blocked_record
  candidate_task = asyncio.create_task(
    executor._process_strategy_output(
      runtime,
      StrategyOutput(
        trade_intents=[intent],
        runtime_state_patch=_candidate_patch(),
      ),
      _input(),
    )
  )
  await strict_started.wait()

  coordination_lock = t_trade_account_coordination_lock("account-1")

  async def config_side():
    config_waiting.set()
    await coordination_lock.acquire()
    try:
      result = await executor.invalidate_t_trade_entry_authority(
        runtime.run_id,
        account_id="account-1",
        reason="CONFIG_APPLY_PENDING",
      )
      events.append("invalidated")
      return result
    finally:
      coordination_lock.release()

  config_task = asyncio.create_task(config_side())
  await config_waiting.wait()
  await asyncio.sleep(0)
  release_strict.set()
  result, invalidated = await asyncio.wait_for(
    asyncio.gather(candidate_task, config_task),
    timeout=2.0,
  )

  assert result is None
  assert invalidated is True
  assert events.index("strict-done") < events.index("invalidated")
  # Invalidation removes only new-entry authority.  A candidate that already
  # crossed the durable AWAITING_APPROVAL boundary remains visible for the
  # normal approval path to re-check the latest gate and terminalize it with
  # an auditable reason.
  assert runtime.pending_approvals.get(intent.intent_id) is intent
  assert runtime.t_trade_intent_emission_by_instrument == {}
  assert runtime.strategy.state["exit_plan"] == {"status": "ACTIVE"}


@pytest.mark.asyncio
async def test_entry_authority_invalidation_keeps_exit_state_and_reservations():
  calls: list[str] = []
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))
  runtime = _runtime(calls)
  executor.runs[runtime.run_id] = runtime
  runtime.t_trade_entry_reservations["intent-1"] = {
    "instrument_code": "600000.SH",
    "amount": 100.0,
  }
  runtime.strategy.state["exit_plan"] = {"status": "ACTIVE"}

  result = await executor.invalidate_t_trade_entry_authority(
    runtime.run_id,
    account_id="account-1",
    reason="CONFIG_APPLY_PENDING",
  )

  assert result is True
  assert runtime.t_trade_intent_emission_by_instrument == {}
  assert "intent-1" in runtime.t_trade_entry_reservations
  assert runtime.strategy.state["exit_plan"] == {"status": "ACTIVE"}


@pytest.mark.asyncio
async def test_durable_diagnostic_uses_coalesced_client_wakeup():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(calls)
  event = {**_event(), "record_kind": "COALESCED_DIAGNOSTIC"}

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(
        set={"opportunity": {"state_version": 8}},
        append_events=[event],
      )
    ),
    _input(),
  )

  assert calls == ["checkpoint"]
  service.materialize_evaluation.assert_awaited_once()
  executor.opportunity_update_service.notify_opportunity.assert_awaited_once()
  notice = executor.opportunity_update_service.notify_opportunity.await_args.kwargs
  assert notice["immediate"] is False
  assert notice["version"].endswith(":8")


@pytest.mark.asyncio
async def test_checkpoint_failure_never_materializes_or_exposes_candidate():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(calls, checkpoints=[False, True])
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == ["checkpoint", "compensation_order", "checkpoint"]
  service.materialize_evaluation.assert_awaited_once()
  assert (
    service.materialize_evaluation.await_args.kwargs["event"]["event_type"]
    == "CANDIDATE_SUPPRESSED"
  )
  _assert_candidate_suppressed(runtime, intent)
  failure = runtime._t_trade_opportunity_failures["600000.SH"]
  assert failure["code"] == ("T_TRADE_STATE_CHECKPOINT_FAILED")
  assert failure["compensation"][intent.intent_id] == {
    "state_compensated": True,
    "checkpointed": True,
    "evaluation_materialized": True,
  }


@pytest.mark.asyncio
async def test_checkpoint_cas_conflict_fail_stops_without_stale_compensation() -> None:
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(
    calls,
    checkpoints=[False],
    snapshot_failure_code="CAS_CONFLICT",
  )
  intent = _intent()

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  assert calls == ["checkpoint"]
  assert runtime.status is ExecutionStatus.ERROR
  assert runtime.error_message == "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
  assert runtime.pending_approvals == {}
  assert "compensation_order" not in calls
  assert "compensation" not in runtime._t_trade_opportunity_failures["600000.SH"]
  service.materialize_evaluation.assert_not_awaited()
  executor.opportunity_update_service.notify_opportunity.assert_not_awaited()


@pytest.mark.asyncio
async def test_awaiting_link_checkpoint_cas_conflict_never_compensates_loser() -> None:
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  outcome = SimpleNamespace(seed_material_event=AsyncMock())
  executor = _executor(service, outcome_facade=outcome)
  runtime = _runtime(
    calls,
    checkpoints=[True, False],
    snapshot_failure_code="CAS_CONFLICT",
  )
  intent = _intent()

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  assert runtime.status is ExecutionStatus.ERROR
  assert runtime.error_message == "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
  assert calls == ["checkpoint", "intent:PENDING", "hook", "checkpoint"]
  assert "compensation_order" not in calls
  assert runtime.pending_approvals == {}
  assert runtime.state_manager.status_updates == []
  assert runtime._t_trade_opportunity_failures["600000.SH"]["code"] == (
    "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
  )
  service.materialize_evaluation.assert_awaited_once()
  outcome.seed_material_event.assert_awaited_once()
  executor.opportunity_update_service.notify_opportunity.assert_not_awaited()


@pytest.mark.asyncio
async def test_material_outbox_enqueue_failure_rolls_back_state_and_fail_stops_runtime() -> None:
  """A full/corrupt outbox must not leave a state-sync-able ghost candidate."""

  class EnqueueFailureStateManager(_StateManager):
    def enqueue_t_trade_material_events(self, _events) -> None:
      raise RuntimeError("MATERIAL outbox capacity exceeded")

  calls: list[str] = []
  runtime = _runtime(calls)
  runtime.state_manager = EnqueueFailureStateManager(calls)
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[_intent()],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  assert runtime.status is ExecutionStatus.ERROR
  assert runtime.strategy.state.to_dict() == {"instrument_states": {}}
  assert runtime.state_manager.material_outbox == {}


@pytest.mark.asyncio
async def test_failed_suppression_checkpoint_never_wakes_clients():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(calls, checkpoints=[False, False])
  intent = _intent()

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  assert calls == ["checkpoint", "compensation_order", "checkpoint"]
  service.materialize_evaluation.assert_not_awaited()
  executor.opportunity_update_service.notify_opportunity.assert_not_awaited()
  assert runtime.pending_approvals == {}
  compensation = runtime._t_trade_opportunity_failures["600000.SH"]["compensation"][
    intent.intent_id
  ]
  assert compensation["state_compensated"] is True
  assert compensation["checkpointed"] is False


@pytest.mark.asyncio
async def test_materialization_failure_never_persists_or_exposes_intent():
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(side_effect=RuntimeError("db unavailable"))
  )
  executor = _executor(service)
  runtime = _runtime(calls)
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == ["checkpoint", "compensation_order", "checkpoint"]
  assert service.materialize_evaluation.await_count == 2
  _assert_candidate_suppressed(runtime, intent)
  failure = runtime._t_trade_opportunity_failures["600000.SH"]
  assert failure["code"] == "T_TRADE_EVALUATION_PERSIST_FAILED"
  assert failure["compensation"][intent.intent_id]["state_compensated"] is True
  assert failure["compensation"][intent.intent_id]["checkpointed"] is True
  assert failure["compensation"][intent.intent_id]["evaluation_materialized"] is False
  executor.opportunity_update_service.notify_opportunity.assert_not_awaited()
  assert set(runtime.state_manager.material_outbox) == {
    "run-opportunity-v3:600000.SH:candidate-1:MATERIAL",
    f"candidate-suppressed:{intent.intent_id}",
  }
  assert runtime.strategy.state.opportunity["candidate_status"] == "SUPPRESSED"


@pytest.mark.asyncio
async def test_material_outbox_replays_stable_events_once_after_restart() -> None:
  calls: list[str] = []
  failing_service = SimpleNamespace(
    materialize_evaluation=AsyncMock(side_effect=RuntimeError("db unavailable"))
  )
  first_executor = _executor(failing_service)
  first_runtime = _runtime(calls)
  intent = _intent()

  await first_executor._process_strategy_output(
    first_runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )

  durable_outbox = {
    key: dict(event)
    for key, event in first_runtime.state_manager.material_outbox.items()
  }
  assert len(durable_outbox) == 2
  expected_event_keys = list(durable_outbox)

  materialized_keys: list[str] = []
  seeded_keys: list[str] = []

  async def materialize(*, event: dict[str, object], **_kwargs: object) -> None:
    materialized_keys.append(str(event["event_key"]))

  async def seed(*, event: dict[str, object], **_kwargs: object) -> None:
    seeded_keys.append(str(event["event_key"]))

  restarted = _runtime([])
  restarted.state_manager.material_outbox = durable_outbox
  restarted_executor = _executor(
    SimpleNamespace(materialize_evaluation=materialize),
    outcome_facade=SimpleNamespace(seed_material_event=seed),
  )

  await restarted_executor._replay_pending_actionable_t_trade_material_events(
    restarted
  )
  await restarted_executor._replay_pending_actionable_t_trade_material_events(
    restarted
  )

  assert materialized_keys == expected_event_keys
  assert seeded_keys == expected_event_keys
  assert restarted.state_manager.material_outbox == {}


@pytest.mark.asyncio
async def test_intent_persistence_failure_suppresses_latched_candidate():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(
    calls,
    checkpoints=[True, True],
    fail_intent_record=True,
  )
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == [
    "checkpoint",
    "intent:PENDING",
    "compensation_order",
    "checkpoint",
  ]
  assert service.materialize_evaluation.await_count == 2
  _assert_candidate_suppressed(runtime, intent)
  assert runtime.state_manager.status_updates == []
  failure = runtime._t_trade_opportunity_failures["600000.SH"]
  assert failure["code"] == "T_TRADE_INTENT_PERSIST_FAILED"
  assert failure["compensation"][intent.intent_id] == {
    "state_compensated": True,
    "checkpointed": True,
    "evaluation_materialized": True,
  }


@pytest.mark.asyncio
async def test_second_checkpoint_failure_rejects_persisted_intent_without_exposure():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(calls, checkpoints=[True, False, True])
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == [
    "checkpoint",
    "intent:PENDING",
    "hook",
    "checkpoint",
    "status:REJECTED",
    "compensation_order",
    "checkpoint",
  ]
  _assert_candidate_suppressed(runtime, intent)
  assert service.materialize_evaluation.await_count == 2
  suppression_event = service.materialize_evaluation.await_args_list[-1].kwargs["event"]
  assert suppression_event["event_type"] == "CANDIDATE_SUPPRESSED"
  assert runtime.state_manager.status_updates == [(intent.intent_id, "REJECTED")]
  compensation = runtime._t_trade_opportunity_failures["600000.SH"]["compensation"]
  assert compensation[intent.intent_id] == {
    "state_compensated": True,
    "checkpointed": True,
    "evaluation_materialized": True,
  }


@pytest.mark.asyncio
async def test_intent_link_evaluation_failure_keeps_manual_intent_unexposed():
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(
      side_effect=[
        None,
        RuntimeError("linked evaluation failed"),
        RuntimeError("suppression evaluation failed"),
      ]
    )
  )
  executor = _executor(service)
  runtime = _runtime(calls, checkpoints=[True, True, True])
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert service.materialize_evaluation.await_count == 3
  _assert_candidate_suppressed(runtime, intent)
  assert runtime.state_manager.status_updates == [(intent.intent_id, "REJECTED")]
  failure = runtime._t_trade_opportunity_failures["600000.SH"]
  assert failure["code"] == "T_TRADE_EVALUATION_PERSIST_FAILED"
  assert failure["compensation"][intent.intent_id]["state_compensated"] is True
  assert failure["compensation"][intent.intent_id]["checkpointed"] is True
  assert failure["compensation"][intent.intent_id]["evaluation_materialized"] is False
  assert failure["compensation"][intent.intent_id]["error"].startswith(
    "SUPPRESSION_EVALUATION_FAILED"
  )


@pytest.mark.asyncio
async def test_awaiting_status_failure_suppresses_checkpointed_candidate():
  calls: list[str] = []
  service = SimpleNamespace(materialize_evaluation=AsyncMock())
  executor = _executor(service)
  runtime = _runtime(
    calls,
    checkpoints=[True, True, True],
    fail_statuses={"AWAITING_APPROVAL"},
  )
  intent = _intent()
  output = StrategyOutput(
    trade_intents=[intent],
    runtime_state_patch=_candidate_patch(),
  )

  await executor._process_strategy_output(runtime, output, _input())

  assert calls == [
    "checkpoint",
    "intent:PENDING",
    "hook",
    "checkpoint",
    "status:AWAITING_APPROVAL",
    "status:REJECTED",
    "compensation_order",
    "checkpoint",
  ]
  assert service.materialize_evaluation.await_count == 3
  _assert_candidate_suppressed(runtime, intent)
  assert runtime.state_manager.status_updates == [(intent.intent_id, "REJECTED")]
  failure = runtime._t_trade_opportunity_failures["600000.SH"]
  assert failure["code"] == "T_TRADE_INTENT_STATUS_PERSIST_FAILED"
  assert failure["compensation"][intent.intent_id] == {
    "state_compensated": True,
    "checkpointed": True,
    "evaluation_materialized": True,
  }


@pytest.mark.asyncio
async def test_profile_is_loaded_once_per_instrument_trade_day_and_injected():
  profile = {
    "profile_version": "p-20260822",
    "profile_schema_version": 1,
    "as_of_trade_date": "2026-08-22",
    "profile_fingerprint": "a" * 64,
    "pullback_threshold_pct": 0.8,
    "momentum_rise_threshold_pct": 0.9,
    "momentum_amount_velocity_ratio": 2.1,
    "pullback_max_spread_ticks": 3,
    "momentum_max_spread_ticks": 10,
  }
  service = SimpleNamespace(
    load_reference_profile=AsyncMock(side_effect=[profile, None])
  )
  executor = _executor(service)
  profile_execute = AsyncMock(wraps=executor._d1_profile_reader.execute)
  executor._d1_profile_reader.execute = profile_execute
  runtime = _runtime([])
  runtime.context.mode = StrategyRunMode.BACKTEST
  first_time = datetime(2026, 8, 23, 9, 31)

  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=first_time,
  )
  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=datetime(2026, 8, 23, 10, 0),
  )
  first_input = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=first_time,
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      timestamp=first_time,
      price=10.0,
    ),
  )

  assert service.load_reference_profile.await_count == 1
  assert isinstance(executor._d1_profile_reader, ReadD1ReferenceProfile)
  assert profile_execute.await_count == 1
  assert profile_execute.await_args.args[0].evaluated_at == first_time
  assert first_input.market_context["t_trade_instrument_profile"] == profile

  next_day = datetime(2026, 8, 24, 9, 31)
  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=next_day,
  )
  next_input = executor._build_strategy_input(
    runtime,
    cadence=StrategyCadence.TICK,
    instrument_code="600000.SH",
    timestamp=next_day,
    market_data=MarketDataSnapshot(
      instrument_code="600000.SH",
      timestamp=next_day,
      price=10.0,
    ),
  )

  assert service.load_reference_profile.await_count == 2
  assert next_input.market_context["t_trade_instrument_profile"] is None
  evaluated_times = [
    call.kwargs["evaluated_at"]
    for call in service.load_reference_profile.await_args_list
  ]
  assert evaluated_times == [first_time, next_day]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_profile_lookup_failure_retries_after_wall_clock_ttl_and_recovers(
  monkeypatch: pytest.MonkeyPatch,
  mode: StrategyRunMode,
):
  profile = {
    "profile_version": "p-20260822",
    "profile_schema_version": 1,
    "as_of_trade_date": "2026-08-22",
    "profile_fingerprint": "b" * 64,
    "pullback_threshold_pct": 0.8,
    "momentum_rise_threshold_pct": 0.9,
    "momentum_amount_velocity_ratio": 2.1,
    "pullback_max_spread_ticks": 3,
    "momentum_max_spread_ticks": 10,
  }
  service = SimpleNamespace(
    load_reference_profile=AsyncMock(
      side_effect=[RuntimeError("temporary database outage"), profile]
    )
  )
  executor = _executor(service)
  runtime = _runtime([])
  runtime.context.mode = mode
  failure_messages: list[str] = []
  executor._runtime_log = lambda _runtime, _level, message: failure_messages.append(
    message
  )
  evaluated_at = datetime(2026, 8, 23, 9, 31)
  retry_clock = iter((100.0, 129.9, 130.0))
  monkeypatch.setattr(
    strategy_executor_module,
    "monotonic",
    lambda: next(retry_clock),
  )
  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=evaluated_at,
  )
  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=evaluated_at + timedelta(hours=5),
  )
  assert service.load_reference_profile.await_count == 1
  cache_key = ("600000.SH", "2026-08-23")
  assert runtime._t_trade_opportunity_profiles[cache_key] is None
  assert runtime._t_trade_opportunity_profile_errors[cache_key] == (
    "PROFILE_LOOKUP_FAILED"
  )

  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=evaluated_at + timedelta(hours=5, seconds=1),
  )

  assert service.load_reference_profile.await_count == 2
  assert runtime._t_trade_opportunity_profiles[cache_key] == profile
  assert cache_key not in runtime._t_trade_opportunity_profile_errors
  assert cache_key not in runtime._t_trade_opportunity_profile_retry_after
  assert "定时重试" in failure_messages[0]


@pytest.mark.asyncio
async def test_backtest_failed_profile_is_read_once_per_trade_date_across_1000_ticks():
  service = SimpleNamespace(
    load_reference_profile=AsyncMock(
      side_effect=RuntimeError("historical profile storage unavailable")
    )
  )
  executor = _executor(service)
  runtime = _runtime([])
  runtime.context.mode = StrategyRunMode.BACKTEST
  failure_messages: list[str] = []
  executor._runtime_log = lambda _runtime, _level, message: failure_messages.append(
    message
  )
  first_tick = datetime(2026, 8, 23, 9, 30)

  for ordinal in range(1_000):
    await executor._ensure_t_trade_opportunity_profile(
      runtime,
      instrument_code="600000.SH",
      evaluated_at=first_tick + timedelta(seconds=20 * ordinal),
    )

  cache_key = ("600000.SH", "2026-08-23")
  assert service.load_reference_profile.await_count == 1
  assert runtime._t_trade_opportunity_profiles[cache_key] is None
  assert runtime._t_trade_opportunity_profile_errors[cache_key] == (
    "PROFILE_LOOKUP_FAILED"
  )
  assert cache_key not in runtime._t_trade_opportunity_profile_retry_after
  assert "本交易日固定失败关闭，不重试" in failure_messages[0]


@pytest.mark.asyncio
async def test_backtest_not_found_profile_is_read_once_per_trade_date():
  service = SimpleNamespace(load_reference_profile=AsyncMock(return_value=None))
  executor = _executor(service)
  runtime = _runtime([])
  runtime.context.mode = StrategyRunMode.BACKTEST
  first_tick = datetime(2026, 8, 23, 9, 30)

  for offset in (0, 3_600, 18_000):
    await executor._ensure_t_trade_opportunity_profile(
      runtime,
      instrument_code="600000.SH",
      evaluated_at=first_tick + timedelta(seconds=offset),
    )

  cache_key = ("600000.SH", "2026-08-23")
  assert service.load_reference_profile.await_count == 1
  assert runtime._t_trade_opportunity_profiles[cache_key] is None
  assert cache_key not in runtime._t_trade_opportunity_profile_errors
  assert cache_key not in runtime._t_trade_opportunity_profile_retry_after


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StrategyRunMode.PAPER, StrategyRunMode.LIVE])
async def test_not_found_profile_retries_on_wall_clock_not_source_time(
  monkeypatch: pytest.MonkeyPatch,
  mode: StrategyRunMode,
):
  service = SimpleNamespace(load_reference_profile=AsyncMock(return_value=None))
  executor = _executor(service)
  runtime = _runtime([])
  runtime.context.mode = mode
  retry_clock = iter((200.0, 229.9, 230.0, 230.0))
  monkeypatch.setattr(
    strategy_executor_module,
    "monotonic",
    lambda: next(retry_clock),
  )
  first_tick = datetime(2026, 8, 23, 9, 30)

  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=first_tick,
  )
  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=first_tick + timedelta(hours=5),
  )
  assert service.load_reference_profile.await_count == 1

  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=first_tick + timedelta(hours=5, seconds=1),
  )

  assert service.load_reference_profile.await_count == 2
  assert runtime._t_trade_opportunity_profiles[("600000.SH", "2026-08-23")] is None


@pytest.mark.asyncio
async def test_backtest_tick_progress_never_writes_replay_projection(
  monkeypatch: pytest.MonkeyPatch,
):
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))
  runtime = _runtime([])
  runtime.context.mode = StrategyRunMode.BACKTEST
  runtime.context.parameters["t_trade_replay"] = True
  runtime.context.backtest_start_time = datetime(2026, 8, 23, 9, 30)
  runtime.context.backtest_end_time = datetime(2026, 8, 23, 15, 0)
  update = AsyncMock()
  monkeypatch.setattr(
    strategy_executor_module.t_trade_replay_projection_service,
    "update",
    update,
  )

  for ordinal in range(1_000):
    runtime.context.current_time = (
      runtime.context.backtest_start_time + timedelta(seconds=ordinal)
    )
    await executor._report_t_trade_replay_progress(runtime)

  update.assert_not_awaited()
  assert runtime._last_t_trade_replay_projection_trade_date is None


@pytest.mark.asyncio
async def test_backtest_day_boundary_projection_writes_once_per_day_across_windows(
  monkeypatch: pytest.MonkeyPatch,
):
  class FakeHistoricalDataAdapter:
    async def get_ticks(self, **_kwargs):
      return []

  class FakeTradingDateHelper:
    async def get_trading_calendar(self, **_kwargs):
      return [datetime(2026, 8, 23).date(), datetime(2026, 8, 24).date()]

  monkeypatch.setattr(
    strategy_executor_module,
    "HistoricalDataAdapter",
    FakeHistoricalDataAdapter,
  )
  monkeypatch.setattr(
    strategy_executor_module,
    "TradingDateHelper",
    FakeTradingDateHelper,
  )
  executor = _executor(SimpleNamespace(materialize_evaluation=AsyncMock()))
  executor._run_backtest_warmup_klines = AsyncMock()
  executor._runtime_log = lambda *_args, **_kwargs: None
  executor._get_backtest_window_hours = lambda: 1
  update = AsyncMock()
  monkeypatch.setattr(
    strategy_executor_module.t_trade_replay_projection_service,
    "update",
    update,
  )
  start_time = datetime(2026, 8, 23, 9, 30)
  end_time = datetime(2026, 8, 24, 13, 45)
  context = StrategyContext(
    run_id="replay-day-boundary",
    mode=StrategyRunMode.BACKTEST,
    instruments=["600000.SH"],
    parameters={"t_trade_replay": True, "account_id": "account-1"},
    backtest_start_time=start_time,
    backtest_end_time=end_time,
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="replay-day-boundary",
    strategy_id=1,
    strategy_class=object,
    context=context,
    data_adapter=FakeHistoricalDataAdapter(),
    status=ExecutionStatus.RUNNING,
  )

  await executor._run_backtest_multi_instrument_timeline(
    runtime,
    context.instruments,
    [],
    start_time,
    end_time,
    use_tick_data=True,
  )

  assert update.await_count == 2
  assert [
    call.kwargs["processed_until"] for call in update.await_args_list
  ] == [
    datetime(2026, 8, 23, 15, 30),
    datetime(2026, 8, 24, 13, 45),
  ]


@pytest.mark.asyncio
async def test_profile_request_uses_shanghai_trade_date_before_d1_validation():
  profile = {
    "profile_version": "p-20260822",
    "profile_schema_version": 1,
    "as_of_trade_date": "2026-08-22",
    "profile_fingerprint": "c" * 64,
    "pullback_threshold_pct": 0.8,
    "momentum_rise_threshold_pct": 0.9,
    "momentum_amount_velocity_ratio": 2.1,
    "pullback_max_spread_ticks": 3,
    "momentum_max_spread_ticks": 10,
  }
  service = SimpleNamespace(load_reference_profile=AsyncMock(return_value=profile))
  executor = _executor(service)
  runtime = _runtime([])
  evaluated_at = datetime(2026, 8, 22, 16, 30, tzinfo=timezone.utc)

  await executor._ensure_t_trade_opportunity_profile(
    runtime,
    instrument_code="600000.SH",
    evaluated_at=evaluated_at,
  )

  request = executor._d1_profile_reader.port.load_reference_profile.await_args
  assert request.kwargs["evaluated_at"] == datetime(2026, 8, 23, 0, 30)
  assert runtime._t_trade_opportunity_profiles[("600000.SH", "2026-08-23")]


@pytest.mark.asyncio
async def test_profile_cache_is_globally_bounded_with_retry_metadata(monkeypatch):
  monkeypatch.setattr(
    strategy_executor_module,
    "_T_TRADE_PROFILE_CACHE_MAX_ENTRIES",
    2,
  )
  service = SimpleNamespace(load_reference_profile=AsyncMock(return_value=None))
  executor = _executor(service)
  runtime = _runtime([])
  evaluated_at = datetime(2026, 8, 23, 9, 31)

  for instrument_code in ("600000.SH", "000001.SZ", "000002.SZ"):
    await executor._ensure_t_trade_opportunity_profile(
      runtime,
      instrument_code=instrument_code,
      evaluated_at=evaluated_at,
    )

  evicted_key = ("600000.SH", "2026-08-23")
  assert len(runtime._t_trade_opportunity_profiles) == 2
  assert evicted_key not in runtime._t_trade_opportunity_profiles
  assert evicted_key not in runtime._t_trade_opportunity_profile_errors
  assert evicted_key not in runtime._t_trade_opportunity_profile_retry_after
  assert ("000002.SZ", "2026-08-23") in runtime._t_trade_opportunity_profiles


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "mode",
  [StrategyRunMode.BACKTEST, StrategyRunMode.PAPER, StrategyRunMode.LIVE],
)
async def test_1000_hot_diagnostics_stay_memory_only_until_explicit_day_or_session_seal(mode):
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(),
    materialize_checkpoint_batch=AsyncMock(),
  )
  executor = _executor(service)
  runtime = _runtime(calls)
  runtime.context.mode = mode
  runtime.state_manager.persist_enabled = True
  runtime.state_manager.checkpoint_strategy_state_changes = AsyncMock(
    return_value=True
  )
  runtime.state_manager.force_save = AsyncMock(return_value=True)
  runtime.state_manager.prepare_checkpoint = AsyncMock(return_value=None)
  runtime.state_manager.finalize_prepared_checkpoint = AsyncMock(return_value=None)

  for ordinal in range(1_000):
    await executor._process_strategy_output(
      runtime,
      _batched_diagnostic_output(ordinal),
      _input(),
    )

  assert calls == ["drain"] * 1_000
  assert runtime.state_manager.drain_capture_state == [False] * 1_000
  assert len(runtime._checkpoint_diagnostic_summaries) == 1
  summary = runtime._checkpoint_diagnostic_summaries["600000.SH"]
  assert summary["checkpoint_coalesced_count"] == 1_000
  runtime.state_manager.checkpoint_strategy_state_changes.assert_not_awaited()
  runtime.state_manager.force_save.assert_not_awaited()
  runtime.state_manager.prepare_checkpoint.assert_not_awaited()
  runtime.state_manager.finalize_prepared_checkpoint.assert_not_awaited()
  service.materialize_evaluation.assert_not_awaited()
  service.materialize_checkpoint_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_actionless_material_evaluation_uses_the_same_memory_only_day_policy():
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(),
    materialize_checkpoint_batch=AsyncMock(),
  )
  executor = _executor(service)
  runtime = _runtime(calls)
  runtime.context.mode = StrategyRunMode.BACKTEST
  runtime.state_manager.persist_enabled = True

  await executor._process_strategy_output(
    runtime,
    StrategyOutput(runtime_state_patch=_candidate_patch()),
    _input(),
  )

  assert calls == ["drain"]
  assert set(runtime._checkpoint_diagnostic_summaries) == {
    "MATERIAL:run-opportunity-v3:600000.SH:candidate-1:MATERIAL"
  }
  assert runtime.state_manager.material_outbox == {}
  service.materialize_evaluation.assert_not_awaited()
  service.materialize_checkpoint_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_pure_material_closes_the_hot_diagnostic_segment_before_later_tick():
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(),
    materialize_checkpoint_batch=AsyncMock(),
  )
  executor = _executor(service)
  runtime = _runtime(calls)
  runtime.context.mode = StrategyRunMode.BACKTEST
  runtime.state_manager.persist_enabled = True

  await executor._process_strategy_output(
    runtime,
    _batched_diagnostic_output(1),
    _input(),
  )
  await executor._process_strategy_output(
    runtime,
    StrategyOutput(runtime_state_patch=_candidate_patch()),
    _input(),
  )
  await executor._process_strategy_output(
    runtime,
    _batched_diagnostic_output(2),
    _input(),
  )

  first_key = "run-opportunity-v3:600000.SH:diagnostic:1"
  material_key = "run-opportunity-v3:600000.SH:candidate-1:MATERIAL"
  assert set(runtime._checkpoint_diagnostic_summaries) == {
    f"DIAGNOSTIC:{first_key}",
    f"MATERIAL:{material_key}",
    "600000.SH",
  }
  first_segment = runtime._checkpoint_diagnostic_summaries[f"DIAGNOSTIC:{first_key}"]
  assert first_segment["checkpoint_segment_closed_by_event_key"] == material_key
  assert first_segment["checkpoint_segment_boundary"] == "MATERIAL"
  assert (
    runtime._checkpoint_diagnostic_summaries["600000.SH"]["event_key"]
    == "run-opportunity-v3:600000.SH:diagnostic:2"
  )
  assert calls == ["drain", "drain", "drain"]
  service.materialize_evaluation.assert_not_awaited()
  service.materialize_checkpoint_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_actionable_output_does_not_block_on_hot_diagnostics_and_segments_them():
  calls: list[str] = []
  service = SimpleNamespace(
    materialize_evaluation=AsyncMock(),
    materialize_checkpoint_batch=AsyncMock(),
  )
  executor = _executor(service)
  runtime = _runtime(calls)
  runtime.state_manager.persist_enabled = True

  await executor._process_strategy_output(
    runtime,
    _batched_diagnostic_output(1),
    _input(),
  )
  assert calls == ["drain"]

  intent = _intent()
  await executor._process_strategy_output(
    runtime,
    StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=_candidate_patch(),
    ),
    _input(),
  )
  await executor._process_strategy_output(
    runtime,
    _batched_diagnostic_output(2),
    _input(),
  )

  first_key = "run-opportunity-v3:600000.SH:diagnostic:1"
  assert runtime.status == ExecutionStatus.RUNNING
  assert runtime.error_message != "CHECKPOINT_DIAGNOSTIC_FINALIZATION_REQUIRED"
  assert f"DIAGNOSTIC:{first_key}" in runtime._checkpoint_diagnostic_summaries
  assert "600000.SH" in runtime._checkpoint_diagnostic_summaries
  assert (
    runtime._checkpoint_diagnostic_summaries["600000.SH"]["event_key"]
    == "run-opportunity-v3:600000.SH:diagnostic:2"
  )
  service.materialize_evaluation.assert_awaited()
  service.materialize_checkpoint_batch.assert_not_awaited()
  assert "checkpoint" in calls
