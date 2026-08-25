from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies.ashare_managed_entry_plan import (
  ENTRY_PLAN_ENABLED_KEY,
  MANAGED_ENTRY_STATE_KEY,
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyContext,
  TradeExecutionEvent,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_domain.trading.entry_plan import (
  EntryEnvironment,
  EntryPlanStatus,
  ManagedEntryPlanConfig,
  ManagedEntryPlanState,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import StrategyRunStatus
from quantx_infrastructure.models.parameter_schema import validate_parameters
from quantx_infrastructure.services.entry_plan_service import (
  _WORKING_ORDER_STATUSES,
  ENTRY_PLAN_LAST_COMMAND_ID_KEY,
  EntryPlanFacts,
  EntryPlanService,
  _LoadedPlan,
)


def _input(
  *,
  environment: str = "PAPER",
  authorization_mode: str = "MANUAL_CONFIRM",
  start_immediately: bool = False,
) -> dict[str, Any]:
  return {
    "instrument_code": "605499.sh",
    "bucket": "core",
    "target_policy": {
      "mode": "INCREMENTAL_AMOUNT_CNY",
      "incremental_amount_cny": 20_000,
      "max_total_amount_cny": 20_000,
      "max_position_pct": 0.2,
      "baseline_snapshot": {
        "position_volume": 200,
        "market_value_cny": 25_000,
        "total_asset_cny": 500_000,
        "reference_price": 125.0,
        "account_snapshot_version": "account-v7",
      },
    },
    "trigger_rules": [
      {
        "rule_id": "trend-1",
        "rule_type": "TREND_PULLBACK_CONFIRMATION",
        "priority": 100,
        "min_pullback_pct": 0.8,
        "rebound_confirmation_pct": 0.2,
        "fast_ema_period": 5,
        "slow_ema_period": 20,
      }
    ],
    "pacing_policy": {
      "tranche_count": 4,
      "max_single_intent_amount_cny": 5_000,
      "max_daily_filled_amount_cny": 10_000,
      "max_orders_per_day": 2,
      "min_interval_seconds": 300,
      "cooldown_after_reject_seconds": 60,
    },
    "execution_policy": {
      "environment": environment,
      "authorization_mode": authorization_mode,
      "price_reference": "ASK1_PROTECTED_LIMIT",
      "max_slippage_bps": 20,
      "max_price_deviation_bps": 30,
      "approval_ttl_ms": 15_000,
    },
    "completion_policy": {
      "max_buy_price": 130.0,
      "expire_at_ms": 4_102_444_799_000,
    },
    "start_immediately": start_immediately,
  }


@dataclass
class _Runtime:
  status: Any = field(default_factory=lambda: SimpleNamespace(value="PENDING"))
  task: Any = None
  strategy: Any = None
  state_manager: Any = None
  parameters: dict[str, Any] = field(default_factory=dict)


class _Manager:
  def __init__(self) -> None:
    self.runs: dict[str, _Runtime] = {}
    self.run_calls: list[dict[str, Any]] = []
    self.start_calls: list[str] = []
    self.pause_calls: list[str] = []
    self.resume_calls: list[str] = []
    self.stop_calls: list[str] = []
    self.updated_parameters: list[tuple[str, dict[str, Any]]] = []
    self.gate_values_at_start: list[bool] = []
    self.start_success = True
    self.fail_enabled_write = False
    self.executor = SimpleNamespace()

  async def run_strategy(self, **kwargs: Any) -> str:
    self.run_calls.append(kwargs)
    self.runs[kwargs["run_id"]] = _Runtime(
      parameters=dict(kwargs["parameters"]),
    )
    return str(kwargs["run_id"])

  def get_run(self, run_id: str) -> Any:
    return self.runs.get(run_id)

  async def start_strategy(self, run_id: str) -> bool:
    self.start_calls.append(run_id)
    self.gate_values_at_start.append(
      self.runs[run_id].parameters.get(ENTRY_PLAN_ENABLED_KEY) is True
    )
    if self.start_success:
      self.runs[run_id].status = SimpleNamespace(value="RUNNING")
    return self.start_success

  async def pause_strategy(self, run_id: str) -> bool:
    self.pause_calls.append(run_id)
    self.runs[run_id].status = SimpleNamespace(value="PAUSED")
    return True

  async def resume_strategy(self, run_id: str) -> bool:
    self.resume_calls.append(run_id)
    self.runs[run_id].status = SimpleNamespace(value="RUNNING")
    return True

  async def stop_strategy(self, run_id: str) -> bool:
    self.stop_calls.append(run_id)
    return True

  async def update_run_parameters(
    self, run_id: str, parameters: dict[str, Any]
  ) -> None:
    self.updated_parameters.append((run_id, parameters))
    if self.fail_enabled_write and parameters.get(ENTRY_PLAN_ENABLED_KEY) is True:
      raise RuntimeError("parameter persistence failed")
    runtime = self.runs.get(run_id)
    if runtime is not None:
      runtime.parameters = dict(parameters)


class _Service(EntryPlanService):
  def __init__(self, manager: _Manager) -> None:
    super().__init__(manager, session_factory=lambda: None)
    self.persisted_statuses: list[tuple[str, StrategyRunStatus]] = []
    self.revocations: list[tuple[str, str]] = []
    self.phases: list[tuple[str, EntryPlanStatus]] = []
    self.loaded: _LoadedPlan | None = None
    self.facts = EntryPlanFacts()
    self.authorization_checked = False
    self.entry_states: dict[str, ManagedEntryPlanState] = {}
    self.terminal_requests: list[tuple[str, EntryPlanStatus, str]] = []
    self.overlap_checks: list[tuple[str, str, str]] = []
    self.offline_terminal_intent_id = ""
    self.offline_terminal_calls: list[tuple[str, str, str]] = []

  async def _ensure_no_active_overlap(
    self, account_id: str, config: ManagedEntryPlanConfig, *, exclude_plan_id: str = ""
  ) -> None:
    self.overlap_checks.append(
      (account_id, config.instrument_code, exclude_plan_id)
    )

  async def _strategy_template_id(self) -> int:
    return 81

  async def _authoritative_baseline(
    self, account_id: str, instrument_code: str, environment: Any
  ) -> dict[str, Any]:
    del account_id, instrument_code, environment
    return {
      "position_volume": 200,
      "market_value_cny": 25_000,
      "total_asset_cny": 500_000,
      "reference_price": 125.0,
      "account_snapshot_version": "account-v7",
    }

  async def _persist_run_status(self, plan_id: str, status: StrategyRunStatus) -> None:
    self.persisted_statuses.append((plan_id, status))

  async def _load_owned_plan(self, plan_id: str, account_id: str) -> _LoadedPlan:
    del plan_id, account_id
    assert self.loaded is not None
    return self.loaded

  async def _load_owned_plan_if_exists(
    self, plan_id: str, account_id: str
  ) -> _LoadedPlan | None:
    del plan_id, account_id
    return self.loaded

  async def _facts(self, plan_id: str) -> EntryPlanFacts:
    del plan_id
    return self.facts

  async def _revoke_authorization(
    self, plan_id: str, *, actor_user_id: str, reason: str
  ) -> None:
    del actor_user_id
    self.revocations.append((plan_id, reason))

  async def _set_phase(
    self,
    plan_id: str,
    phase: EntryPlanStatus,
    *,
    reason: str = "",
    reconciled_zero_intent_id: str = "",
  ) -> None:
    del reason
    state = self.entry_states.setdefault(plan_id, ManagedEntryPlanState())
    if (
      reconciled_zero_intent_id
      and state.pending_intent_id == reconciled_zero_intent_id
    ):
      state.apply_order_terminal(
        status="RECONCILED_ZERO_FILL",
        timestamp_ms=0,
        cooldown_after_reject_seconds=0,
      )
    state.phase = phase
    self.phases.append((plan_id, phase))

  async def _terminalize_offline_awaiting_intent(
    self,
    plan_id: str,
    intent_id: str,
    *,
    account_id: str,
    instrument_code: str,
    reason: str,
    stable_plan_id: str | None = None,
  ) -> str:
    del account_id, instrument_code, stable_plan_id
    self.offline_terminal_calls.append((plan_id, intent_id, reason))
    if self.offline_terminal_intent_id != intent_id:
      return ""
    self.facts = EntryPlanFacts(reconciled_zero_intent_id=intent_id)
    return intent_id

  async def _request_terminal(
    self,
    plan_id: str,
    status: EntryPlanStatus,
    *,
    reason: str,
    pending_work: bool = False,
    reconciled_zero_intent_id: str = "",
  ) -> EntryPlanStatus:
    state = self.entry_states.setdefault(plan_id, ManagedEntryPlanState())
    if (
      reconciled_zero_intent_id
      and state.pending_intent_id == reconciled_zero_intent_id
    ):
      state.apply_order_terminal(
        status="RECONCILED_ZERO_FILL",
        timestamp_ms=0,
        cooldown_after_reject_seconds=0,
      )
    state.request_terminal(status, reason=reason, pending_work=pending_work)
    self.terminal_requests.append((plan_id, status, reason))
    self.phases.append((plan_id, state.phase))
    return state.phase

  async def _require_plan_not_terminal(self, plan_id: str) -> None:
    state = self.entry_states.setdefault(plan_id, ManagedEntryPlanState())
    if state.terminal_requested is not None or state.phase in {
      EntryPlanStatus.CANCELLED,
      EntryPlanStatus.EXPIRED,
      EntryPlanStatus.COMPLETED,
    }:
      raise ValueError("ENTRY_PLAN_TERMINAL:计划已终止，继续操作请新建计划")

  async def _require_live_auto_authorization(
    self,
    plan_id: str,
    account_id: str,
    config: ManagedEntryPlanConfig,
  ) -> None:
    del plan_id, account_id, config
    self.authorization_checked = True


class _ScalarResult:
  def __init__(self, value: Any) -> None:
    self._value = value

  def scalar_one_or_none(self) -> Any:
    return self._value


class _BaselineSession:
  def __init__(
    self,
    *,
    account: Any,
    position: Any,
    position_snapshot: Any,
    instrument: Any,
    rollout: Any = None,
  ) -> None:
    self._execute_values = [account, position]
    self._get_values = {
      "BrokerPositionSnapshot": position_snapshot,
      "Instrument": instrument,
      "AccountExecutionControl": rollout,
    }

  async def __aenter__(self) -> _BaselineSession:
    return self

  async def __aexit__(self, *args: Any) -> None:
    del args

  async def execute(self, statement: Any) -> _ScalarResult:
    del statement
    return _ScalarResult(self._execute_values.pop(0))

  async def get(self, model: type[Any], key: str) -> Any:
    del key
    return self._get_values[model.__name__]


class _EmptySession:
  async def __aenter__(self) -> _EmptySession:
    return self

  async def __aexit__(self, *args: Any) -> None:
    del args


@pytest.mark.asyncio
@pytest.mark.parametrize("has_order_artifact", [False, True])
async def test_offline_awaiting_intent_requires_authoritative_zero_order_proof(
  has_order_artifact: bool,
) -> None:
  intent = SimpleNamespace(
    strategy_run_id="plan-1",
    # RuntimeStateManager currently leaves this optional; ownership comes from
    # the already-loaded run parameters when the intent row has no account id.
    account_id=None,
    instrument_code="605499.SH",
    direction="BUY",
    status="AWAITING_APPROVAL",
    executed_volume=0,
    executed_price=None,
    executed_time=None,
    order_id=None,
    notes=None,
    intent_metadata={
      "entry_plan_id": "plan-1",
      "execution_mode": "MANUAL_CONFIRM",
      "mobile_trade_approval_challenge_v1": {"challenge_id": "old-challenge"},
    },
  )

  class Session:
    def __init__(self) -> None:
      self.scalar_values = iter(
        ["pending-order" if has_order_artifact else None, None, None, None]
      )
      self.committed = False

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return False

    async def get(self, _model, key, **kwargs):
      assert key == "intent-1"
      assert kwargs == {"with_for_update": True}
      return intent

    async def scalar(self, _statement):
      return next(self.scalar_values)

    async def commit(self):
      self.committed = True

  session = Session()
  manager = _Manager()
  service = EntryPlanService(manager, session_factory=lambda: session)

  result = await service._terminalize_offline_awaiting_intent(
    "plan-1",
    "intent-1",
    account_id="acct-1",
    instrument_code="605499.SH",
    reason="ENTRY_PLAN_PAUSED",
  )

  if has_order_artifact:
    assert result == ""
    assert intent.status == "AWAITING_APPROVAL"
    assert not session.committed
  else:
    assert result == "intent-1"
    assert intent.status == "RECONCILED_ZERO_FILL"
    assert intent.notes == "ENTRY_PLAN_PAUSED_BEFORE_ORDER_RECONCILED_ZERO_FILL"
    assert intent.intent_metadata["execution_terminal_source"] == (
      "ENTRY_PLAN_SERVICE_OFFLINE"
    )
    assert intent.intent_metadata["mobile_trade_approval_challenge_v1"] == {
      "challenge_id": "old-challenge"
    }
    assert session.committed


def _baseline_session(
  now: datetime,
  *,
  position_snapshot: Any,
  position: Any = None,
  rollout: Any = None,
) -> _BaselineSession:
  return _BaselineSession(
    account=SimpleNamespace(updated_at=now, total_asset=500_000),
    position=position,
    position_snapshot=position_snapshot,
    instrument=SimpleNamespace(
      updated_at=now,
      pre_close=125.0,
      settlement_price=None,
    ),
    rollout=rollout,
  )


@pytest.mark.asyncio
async def test_active_plan_overlap_is_per_instrument_not_bucket(
  monkeypatch,
) -> None:
  core = EntryPlanService._build_config(
    _input(),
    plan_id="plan-new",
    account_id="acct-1",
    config_version=1,
  )
  swing_input = _input()
  swing_input["bucket"] = "swing"
  swing = EntryPlanService._build_config(
    swing_input,
    plan_id="plan-existing",
    account_id="acct-1",
    config_version=1,
  )
  existing = SimpleNamespace(
    id="plan-existing",
    parameters={
      "account_id": "acct-1",
      MANAGED_ENTRY_STATE_KEY: swing.to_dict(),
    },
    instruments=["605499.SH"],
  )

  async def active_runs(_repository, _class_name):
    return [existing]

  persisted = {"state": ManagedEntryPlanState(phase=EntryPlanStatus.ARMED)}

  async def run_state(_repository, _run_id):
    return SimpleNamespace(
      custom_state={
        MANAGED_ENTRY_STATE_KEY: persisted["state"].to_dict()
      }
    )

  monkeypatch.setattr(
    "quantx_infrastructure.services.entry_plan_service."
    "StrategyRunRepository.find_active_runs_by_strategy_class",
    active_runs,
  )
  monkeypatch.setattr(
    "quantx_infrastructure.services.entry_plan_service."
    "StrategyRunStateRepository.get_state",
    run_state,
  )
  service = EntryPlanService(
    _Manager(),
    session_factory=_EmptySession,
  )

  with pytest.raises(ValueError, match="ACTIVE_ENTRY_PLAN_EXISTS:plan-existing"):
    await service._ensure_no_active_overlap("acct-1", core)

  draining = ManagedEntryPlanState(
    phase=EntryPlanStatus.ENTRY_PENDING,
    pending_intent_id="intent-existing",
    pending_stage_id="stage-existing",
  )
  draining.request_terminal(
    EntryPlanStatus.CANCELLED,
    reason="USER_CANCELLED",
    pending_work=True,
  )
  persisted["state"] = draining
  with pytest.raises(ValueError, match="ACTIVE_ENTRY_PLAN_EXISTS:plan-existing"):
    await service._ensure_no_active_overlap("acct-1", core)

  draining.apply_order_terminal(
    status="RECONCILED_ZERO_FILL",
    timestamp_ms=1,
    cooldown_after_reject_seconds=0,
  )
  assert draining.phase == EntryPlanStatus.CANCELLED
  await service._ensure_no_active_overlap("acct-1", core)


@pytest.mark.asyncio
async def test_create_defaults_to_paused_real_strategy_run() -> None:
  manager = _Manager()
  service = _Service(manager)
  raw_input = _input()
  raw_input["target_policy"]["baseline_snapshot"] = {
    "position_volume": 0,
    "market_value_cny": 0,
    "total_asset_cny": 99_999_999,
    "reference_price": 1,
    "account_snapshot_version": "client-forged",
  }

  result = await service.create(
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": raw_input,
    }
  )

  assert result["plan_id"] != result["run_id"]
  assert result["started"] is False
  assert manager.start_calls == []
  assert manager.run_calls[0]["strategy_class"].__name__ == (
    "AshareManagedEntryPlanStrategy"
  )
  assert manager.run_calls[0]["instruments"] == ["605499.SH"]
  assert manager.run_calls[0]["auto_start"] is False
  assert manager.run_calls[0]["parameters"][ENTRY_PLAN_ENABLED_KEY] is False
  baseline = manager.run_calls[0]["parameters"]["managed_entry_plan"]["target_policy"][
    "baseline_snapshot"
  ]
  assert baseline == {
    "position_volume": 200,
    "market_value_cny": 25_000,
    "total_asset_cny": 500_000,
    "reference_price": 125.0,
    "account_snapshot_version": "account-v7",
  }
  assert service.persisted_statuses == [(result["run_id"], StrategyRunStatus.PAUSED)]


@pytest.mark.asyncio
async def test_authoritative_baseline_accepts_confirmed_fresh_empty_position(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 8, 20, 10, 0)
  snapshot = SimpleNamespace(
    is_complete=True,
    sequence=27,
    last_error=None,
    reported_at=now - timedelta(seconds=2),
    received_at=now - timedelta(seconds=1),
    source="QMT_AGENT",
    position_count=0,
  )
  session = _baseline_session(now, position_snapshot=snapshot)
  service = EntryPlanService(
    SimpleNamespace(),
    session_factory=lambda: session,
  )
  monkeypatch.setattr(time_utils, "now", lambda: now)

  baseline = await service._authoritative_baseline(
    "acct-1",
    "605499.SH",
    EntryEnvironment.PAPER,
  )

  assert baseline["position_volume"] == 0
  assert baseline["market_value_cny"] == 0
  assert baseline["total_asset_cny"] == 500_000
  assert baseline["reference_price"] == 125
  assert len(baseline["account_snapshot_version"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("snapshot", "error_code"),
  [
    (None, "ENTRY_POSITION_SNAPSHOT_UNAVAILABLE"),
    (
      SimpleNamespace(
        is_complete=False,
        sequence=27,
        last_error=None,
        reported_at=datetime(2026, 8, 20, 9, 59, 58),
        received_at=datetime(2026, 8, 20, 9, 59, 59),
        source="QMT_AGENT",
        position_count=0,
      ),
      "ENTRY_POSITION_SNAPSHOT_INCOMPLETE",
    ),
    (
      SimpleNamespace(
        is_complete=True,
        sequence=27,
        last_error=None,
        reported_at=datetime(2026, 8, 20, 9, 58),
        received_at=datetime(2026, 8, 20, 9, 59, 59),
        source="QMT_AGENT",
        position_count=0,
      ),
      "ENTRY_POSITION_SNAPSHOT_STALE",
    ),
  ],
)
async def test_authoritative_baseline_rejects_unproven_or_stale_empty_position(
  monkeypatch: pytest.MonkeyPatch,
  snapshot: Any,
  error_code: str,
) -> None:
  now = datetime(2026, 8, 20, 10, 0)
  session = _baseline_session(now, position_snapshot=snapshot)
  service = EntryPlanService(
    SimpleNamespace(),
    session_factory=lambda: session,
  )
  monkeypatch.setattr(time_utils, "now", lambda: now)

  with pytest.raises(ValueError, match=error_code):
    await service._authoritative_baseline(
      "acct-1",
      "605499.SH",
      EntryEnvironment.PAPER,
    )


@pytest.mark.asyncio
async def test_live_baseline_requires_matching_fresh_reconciliation_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 8, 20, 10, 0)
  reported_at = now - timedelta(seconds=2)
  snapshot = SimpleNamespace(
    is_complete=True,
    sequence=27,
    last_error=None,
    reported_at=reported_at,
    received_at=now - timedelta(seconds=1),
    source="QMT_AGENT",
    position_count=0,
  )
  rollout = SimpleNamespace(
    reconcile_status="READY",
    last_snapshot_id="snapshot-27",
    last_snapshot_hash="a" * 64,
    last_snapshot_at=reported_at,
  )
  service = EntryPlanService(
    SimpleNamespace(),
    session_factory=lambda: _baseline_session(
      now,
      position_snapshot=snapshot,
      rollout=rollout,
    ),
  )
  monkeypatch.setattr(time_utils, "now", lambda: now)

  baseline = await service._authoritative_baseline(
    "acct-1",
    "605499.SH",
    EntryEnvironment.LIVE,
  )

  assert baseline["position_volume"] == 0
  assert len(baseline["account_snapshot_version"]) == 64


def test_managed_entry_schema_accepts_service_parameters_and_rejects_unknowns() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  parameters = {
    **service._build_parameters(
      account_id="acct-1",
      actor_user_id="user-1",
      note="按趋势分批建仓",
      config=config,
    ),
    ENTRY_PLAN_ENABLED_KEY: False,
    "entry_plan_last_command_id": "command-1",
  }
  schema = AshareManagedEntryPlanStrategy.get_parameter_schema().model_dump(
    exclude_none=True
  )

  valid, error = validate_parameters(parameters, schema)

  assert valid is True, error
  assert schema["additionalProperties"] is False
  assert set(schema["properties"]) == set(parameters)

  valid, error = validate_parameters(
    {**parameters, "unexpected_runtime_parameter": True}, schema
  )

  assert valid is False
  assert error == "不允许的参数: unexpected_runtime_parameter"


@pytest.mark.asyncio
async def test_live_auto_create_never_starts_before_exact_authorization() -> None:
  manager = _Manager()
  service = _Service(manager)

  result = await service.create(
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": _input(
        environment="LIVE",
        authorization_mode="AUTO",
        start_immediately=True,
      ),
    }
  )

  assert result["authorization_required"] is True
  assert result["started"] is False
  assert manager.start_calls == []
  assert service.persisted_statuses[-1][1] == StrategyRunStatus.PAUSED


@pytest.mark.asyncio
async def test_create_start_failure_closes_persistent_entry_gate() -> None:
  manager = _Manager()
  manager.start_success = False
  service = _Service(manager)

  with pytest.raises(RuntimeError, match="启动失败"):
    await service.create(
      {
        "account_id": "acct-1",
        "actor_user_id": "user-1",
        "input": _input(start_immediately=True),
      }
    )

  assert manager.updated_parameters[-1][1][ENTRY_PLAN_ENABLED_KEY] is False
  assert service.persisted_statuses[-1][1] == StrategyRunStatus.PAUSED


@pytest.mark.asyncio
async def test_create_uses_command_id_as_stable_plan_id_on_replay() -> None:
  manager = _Manager()
  service = _Service(manager)
  payload = {
    "account_id": "acct-1",
    "actor_user_id": "user-1",
    "input": _input(),
  }

  first = await service.create(payload, command_id="command-create-1")
  persisted = manager.run_calls[0]["parameters"]
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id=first["run_id"]),
    parameters=persisted,
    config=ManagedEntryPlanConfig.from_dict(persisted["managed_entry_plan"]),
    plan_id=first["plan_id"],
  )
  second = await service.create(payload, command_id="command-create-1")

  assert first == second
  assert first["plan_id"] == "command-create-1"
  assert len(manager.run_calls) == 1
  assert persisted[ENTRY_PLAN_LAST_COMMAND_ID_KEY] == "command-create-1"


@pytest.mark.asyncio
async def test_create_replay_with_changed_config_fails_closed() -> None:
  manager = _Manager()
  service = _Service(manager)
  payload = {
    "account_id": "acct-1",
    "actor_user_id": "user-1",
    "input": _input(),
  }
  await service.create(payload, command_id="command-create-1")
  persisted = manager.run_calls[0]["parameters"]
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="command-create-1"),
    parameters=persisted,
    config=ManagedEntryPlanConfig.from_dict(persisted["managed_entry_plan"]),
  )
  changed_payload = {
    **payload,
    "input": {
      **payload["input"],
      "completion_policy": {
        **payload["input"]["completion_policy"],
        "max_buy_price": 129.0,
      },
    },
  }

  with pytest.raises(ValueError, match="ENTRY_COMMAND_REPLAY_CONFLICT"):
    await service.create(changed_payload, command_id="command-create-1")

  assert len(manager.run_calls) == 1


@pytest.mark.asyncio
async def test_enable_live_auto_checks_authorization_before_start() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(environment="LIVE", authorization_mode="AUTO"),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": config.to_dict()},
    config=config,
  )
  manager.runs["plan-1"] = _Runtime()

  result = await service.set_enabled(
    "plan-1",
    True,
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )

  assert service.authorization_checked is True
  assert manager.start_calls == ["plan-1"]
  assert service.phases == [("plan-1", EntryPlanStatus.ARMED)]
  assert result["code"] == "ENTRY_PLAN_ARMED"
  assert manager.gate_values_at_start == [False]
  assert manager.updated_parameters[-1][1][ENTRY_PLAN_ENABLED_KEY] is True


@pytest.mark.asyncio
async def test_enable_start_failure_rolls_back_gate_and_phase() -> None:
  manager = _Manager()
  manager.start_success = False
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: False,
      "managed_entry_plan": config.to_dict(),
    },
    config=config,
  )
  manager.runs["plan-1"] = _Runtime()

  with pytest.raises(RuntimeError, match="启动失败"):
    await service.set_enabled(
      "plan-1",
      True,
      account_id="acct-1",
      config_version=1,
      actor_user_id="user-1",
    )

  assert [item[1][ENTRY_PLAN_ENABLED_KEY] for item in manager.updated_parameters] == [
    False,
    False,
  ]
  assert manager.gate_values_at_start == [False]
  assert service.phases == [
    ("plan-1", EntryPlanStatus.ARMED),
    ("plan-1", EntryPlanStatus.PAUSED),
  ]


@pytest.mark.asyncio
async def test_enable_gate_write_failure_leaves_running_runtime_disabled() -> None:
  manager = _Manager()
  manager.fail_enabled_write = True
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: False,
      "managed_entry_plan": config.to_dict(),
    },
    config=config,
  )
  manager.runs["plan-1"] = _Runtime(
    parameters=dict(service.loaded.parameters),
  )

  with pytest.raises(RuntimeError, match="门禁开启失败"):
    await service.set_enabled(
      "plan-1",
      True,
      account_id="acct-1",
      config_version=1,
      actor_user_id="user-1",
    )

  assert manager.gate_values_at_start == [False]
  assert manager.runs["plan-1"].status.value == "RUNNING"
  assert manager.runs["plan-1"].parameters[ENTRY_PLAN_ENABLED_KEY] is False
  assert manager.updated_parameters[-1][1][ENTRY_PLAN_ENABLED_KEY] is False


@pytest.mark.asyncio
async def test_pause_disables_entry_and_rejects_awaiting_approval() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: True,
      "managed_entry_plan": config.to_dict(),
    },
    config=config,
  )
  service.facts = EntryPlanFacts(
    pending_intent_id="intent-1",
    active_intent_id="intent-1",
  )
  rejected: list[tuple[str, str, str]] = []

  async def reject_trade_intent(
    run_id: str, intent_id: str, *, reason: str
  ) -> dict[str, Any]:
    rejected.append((run_id, intent_id, reason))
    return {"success": True}

  manager.executor.reject_trade_intent = reject_trade_intent

  result = await service.set_enabled(
    "plan-1",
    False,
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )

  assert result["code"] == "ENTRY_PLAN_PAUSED"
  assert manager.updated_parameters[-1][1][ENTRY_PLAN_ENABLED_KEY] is False
  assert rejected == [("plan-1", "intent-1", "ENTRY_PLAN_PAUSED")]
  assert service.phases == [("plan-1", EntryPlanStatus.PAUSED)]


@pytest.mark.asyncio
async def test_runtime_absent_pause_terminalizes_awaiting_intent_and_clears_state() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: True,
      MANAGED_ENTRY_STATE_KEY: config.to_dict(),
    },
    config=config,
  )
  service.facts = EntryPlanFacts(
    pending_intent_id="intent-1",
    active_intent_id="intent-1",
  )
  service.offline_terminal_intent_id = "intent-1"
  service.entry_states["plan-1"] = ManagedEntryPlanState(
    phase=EntryPlanStatus.AWAITING_APPROVAL,
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="manual-1",
  )

  async def reject_missing_runtime(*_args, **_kwargs):
    return {"success": False, "code": "RUN_NOT_FOUND"}

  manager.executor.reject_trade_intent = reject_missing_runtime

  result = await service.set_enabled(
    "plan-1",
    False,
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )

  state = service.entry_states["plan-1"]
  assert result["code"] == "ENTRY_PLAN_PAUSED"
  assert state.phase == EntryPlanStatus.PAUSED
  assert state.pending_intent_id == ""
  assert service.facts.active_intent_id == ""
  assert service.facts.reconciled_zero_intent_id == "intent-1"
  assert service.offline_terminal_calls == [
    ("plan-1", "intent-1", "ENTRY_PLAN_PAUSED")
  ]


@pytest.mark.asyncio
async def test_manual_rule_trigger_reaches_runtime_serial_event_queue() -> None:
  manager = _Manager()
  service = _Service(manager)
  raw_input = _input()
  raw_input["trigger_rules"] = [
    {
      "rule_id": "manual-1",
      "rule_type": "MANUAL_TRIGGER",
      "preset_id": "manual-safe",
      "manual_trigger_sequence": 7,
    }
  ]
  config = EntryPlanService._build_config(
    raw_input,
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: True,
      "managed_entry_plan": config.to_dict(),
    },
    config=config,
  )
  market_data = MarketDataSnapshot(
    instrument_code="605499.SH",
    timestamp=datetime(2026, 8, 20, 10, 15),
    price=125.0,
  )
  queue: asyncio.Queue[Any] = asyncio.Queue()
  manager.runs["plan-1"] = SimpleNamespace(
    status=SimpleNamespace(value="RUNNING"),
    durable_event_barrier_key=None,
    latest_market_data={"605499.SH": market_data},
    event_queue=queue,
  )

  result = await service.trigger_manual(
    "plan-1",
    "manual-1",
    account_id="acct-1",
  )

  event_type, event = queue.get_nowait()
  assert result["code"] == "ENTRY_PLAN_MANUAL_TRIGGER_QUEUED"
  assert event_type == "entry_plan_evaluate"
  assert event == {
    "type": "ENTRY_PLAN_MANUAL_TRIGGER",
    "rule_id": "manual-1",
    "instrument_code": "605499.SH",
    "market_data": market_data,
  }
  assert config.trigger_rules[0].parameters == {
    "preset_id": "manual-safe",
    "trigger_sequence": 7,
  }


@pytest.mark.asyncio
async def test_evaluate_now_queues_snapshot_reconcile_instead_of_raw_tick() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: True,
      "managed_entry_plan": config.to_dict(),
    },
    config=config,
  )
  market_data = MarketDataSnapshot(
    instrument_code="605499.SH",
    timestamp=datetime(2026, 8, 20, 10, 15),
    price=125.0,
  )
  queue: asyncio.Queue[Any] = asyncio.Queue()
  manager.runs["plan-1"] = SimpleNamespace(
    status=SimpleNamespace(value="RUNNING"),
    context=SimpleNamespace(instruments=["605499.SH"]),
    durable_event_barrier_key=None,
    latest_market_data={"605499.SH": market_data},
    event_queue=queue,
  )

  result = await service.evaluate_now("plan-1", account_id="acct-1")

  event_type, event = queue.get_nowait()
  assert result["code"] == "ENTRY_PLAN_EVALUATION_QUEUED"
  assert event_type == "entry_plan_evaluate"
  assert event == {
    "type": "ENTRY_PLAN_EVALUATE_NOW",
    "instrument_code": "605499.SH",
    "market_data": market_data,
  }


@pytest.mark.asyncio
async def test_update_revokes_grant_and_increments_config_version() -> None:
  manager = _Manager()
  service = _Service(manager)
  original = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=3,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": original.to_dict()},
    config=original,
  )
  updated_input = _input()
  updated_input.update({"plan_id": "plan-1", "config_version": 3})
  updated_input["target_policy"]["incremental_amount_cny"] = 25_000
  updated_input["target_policy"]["max_total_amount_cny"] = 25_000

  result = await service.update(
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": updated_input,
    }
  )

  assert result["config_version"] == 4
  assert result["run_id"] != "plan-1"
  assert service.revocations == [("plan-1", "CONFIG_UPDATED")]
  persisted = manager.run_calls[-1]["parameters"]["managed_entry_plan"]
  assert persisted["config_version"] == 4
  assert persisted["target_policy"]["incremental_amount_cny"] == 25_000
  assert service.overlap_checks[-1] == ("acct-1", "605499.SH", "plan-1")


@pytest.mark.asyncio
async def test_update_command_replay_returns_exact_next_version_without_mutation() -> (
  None
):
  manager = _Manager()
  service = _Service(manager)
  updated = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=4,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_LAST_COMMAND_ID_KEY: "command-update-1",
      "managed_entry_plan": updated.to_dict(),
    },
    config=updated,
  )
  replay_input = _input()
  replay_input.update({"plan_id": "plan-1", "config_version": 3})

  result = await service.update(
    {
      "account_id": "acct-1",
      "actor_user_id": "user-1",
      "input": replay_input,
    },
    command_id="command-update-1",
  )

  assert result["config_version"] == 4
  assert service.revocations == []
  assert manager.updated_parameters == []


@pytest.mark.asyncio
async def test_update_command_replay_rejects_wrong_landing_version() -> None:
  manager = _Manager()
  service = _Service(manager)
  updated = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=4,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_LAST_COMMAND_ID_KEY: "command-update-1",
      "managed_entry_plan": updated.to_dict(),
    },
    config=updated,
  )
  replay_input = _input()
  replay_input.update({"plan_id": "plan-1", "config_version": 2})

  with pytest.raises(ValueError, match="ENTRY_COMMAND_REPLAY_CONFLICT"):
    await service.update(
      {
        "account_id": "acct-1",
        "actor_user_id": "user-1",
        "input": replay_input,
      },
      command_id="command-update-1",
    )

  assert manager.updated_parameters == []


@pytest.mark.asyncio
async def test_cancel_with_working_buy_enters_draining_and_requests_cancel() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": config.to_dict()},
    config=config,
  )
  service.facts = EntryPlanFacts(has_working_order=True)
  cancelled: list[tuple[str, str]] = []

  async def cancel_open_buy_orders(run_id: str, reason: str) -> int:
    cancelled.append((run_id, reason))
    return 1

  manager.executor.cancel_open_buy_orders = cancel_open_buy_orders
  manager.executor.reject_trade_intent = None

  result = await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
    cancel_working_order=True,
  )

  assert result["code"] == "ENTRY_PLAN_DRAINING"
  assert result["cancel_requested_count"] == 1
  assert service.phases == [("plan-1", EntryPlanStatus.DRAINING)]
  assert service.terminal_requests == [
    ("plan-1", EntryPlanStatus.CANCELLED, "USER_CANCELLED")
  ]
  assert service.entry_states["plan-1"].terminal_requested == (
    EntryPlanStatus.CANCELLED
  )
  assert cancelled == [("plan-1", "ENTRY_PLAN_CANCELLED")]
  assert manager.stop_calls == []


def test_pending_order_status_is_treated_as_working() -> None:
  assert "PENDING" in _WORKING_ORDER_STATUSES


@pytest.mark.asyncio
async def test_cancel_recomputes_active_intent_after_failed_runtime_rejection() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": config.to_dict()},
    config=config,
  )
  facts = iter(
    [
      EntryPlanFacts(
        pending_intent_id="intent-1",
        active_intent_id="intent-1",
      ),
      EntryPlanFacts(active_intent_id="intent-1"),
    ]
  )

  async def current_facts(_plan_id: str) -> EntryPlanFacts:
    return next(facts)

  async def reject_missing_runtime(*_args, **_kwargs):
    return {"success": False, "code": "RUN_NOT_FOUND"}

  service._facts = current_facts
  manager.executor.reject_trade_intent = reject_missing_runtime

  result = await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )

  assert result["code"] == "ENTRY_PLAN_DRAINING"
  assert service.entry_states["plan-1"].phase == EntryPlanStatus.DRAINING
  assert manager.stop_calls == []


@pytest.mark.asyncio
async def test_runtime_absent_cancel_terminalizes_awaiting_intent_without_draining() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={
      "account_id": "acct-1",
      ENTRY_PLAN_ENABLED_KEY: True,
      MANAGED_ENTRY_STATE_KEY: config.to_dict(),
    },
    config=config,
  )
  service.facts = EntryPlanFacts(
    pending_intent_id="intent-1",
    active_intent_id="intent-1",
  )
  service.offline_terminal_intent_id = "intent-1"
  service.entry_states["plan-1"] = ManagedEntryPlanState(
    phase=EntryPlanStatus.AWAITING_APPROVAL,
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="manual-1",
  )

  async def reject_missing_runtime(*_args, **_kwargs):
    return {"success": False, "code": "RUN_NOT_FOUND"}

  manager.executor.reject_trade_intent = reject_missing_runtime

  result = await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )

  state = service.entry_states["plan-1"]
  assert result["code"] == "ENTRY_PLAN_CANCELLED"
  assert state.phase == EntryPlanStatus.CANCELLED
  assert state.pending_intent_id == ""
  assert state.terminal_requested == EntryPlanStatus.CANCELLED
  assert service.offline_terminal_calls == [
    ("plan-1", "intent-1", "ENTRY_PLAN_CANCELLED")
  ]
  assert manager.stop_calls == ["plan-1"]


@pytest.mark.asyncio
async def test_runtime_absent_local_zero_cancel_settles_stale_pending_state() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": config.to_dict()},
    config=config,
  )
  service.entry_states["plan-1"] = ManagedEntryPlanState(
    phase=EntryPlanStatus.ENTRY_PENDING,
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="trend-1",
    pending_requested_volume=100,
  )
  facts = iter(
    [
      EntryPlanFacts(
        active_intent_id="intent-1",
        has_working_order=True,
      ),
      EntryPlanFacts(
        active_intent_id="intent-1",
        has_working_order=True,
      ),
      EntryPlanFacts(reconciled_zero_intent_id="intent-1"),
    ]
  )

  async def current_facts(_plan_id: str) -> EntryPlanFacts:
    return next(facts)

  async def cancel_local_order(_run_id: str, _reason: str) -> int:
    return 1

  service._facts = current_facts
  manager.executor.cancel_open_buy_orders = cancel_local_order

  result = await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
    cancel_working_order=True,
  )

  state = service.entry_states["plan-1"]
  assert result["code"] == "ENTRY_PLAN_CANCELLED"
  assert result["cancel_requested_count"] == 1
  assert state.phase == EntryPlanStatus.CANCELLED
  assert state.pending_intent_id == ""
  assert manager.stop_calls == ["plan-1"]


@pytest.mark.asyncio
async def test_cancelled_plan_cannot_be_enabled_or_updated() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters={"account_id": "acct-1", "managed_entry_plan": config.to_dict()},
    config=config,
  )

  await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )
  writes_after_cancel = len(manager.updated_parameters)

  with pytest.raises(ValueError, match="ENTRY_PLAN_TERMINAL"):
    await service.set_enabled(
      "plan-1",
      True,
      account_id="acct-1",
      config_version=1,
      actor_user_id="user-1",
    )

  update_input = _input()
  update_input.update({"plan_id": "plan-1", "config_version": 1})
  with pytest.raises(ValueError, match="ENTRY_PLAN_TERMINAL"):
    await service.update(
      {
        "account_id": "acct-1",
        "actor_user_id": "user-1",
        "input": update_input,
      }
    )

  assert len(manager.updated_parameters) == writes_after_cancel
  assert manager.start_calls == []


@pytest.mark.asyncio
async def test_service_cancel_persists_terminal_request_before_late_fill() -> None:
  manager = _Manager()
  service = _Service(manager)
  config = EntryPlanService._build_config(
    _input(),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=1,
  )
  parameters = {
    "account_id": "acct-1",
    ENTRY_PLAN_ENABLED_KEY: True,
    MANAGED_ENTRY_STATE_KEY: config.to_dict(),
  }
  item = AshareManagedEntryPlanStrategy(
    StrategyContext(
      run_id="plan-1",
      mode=StrategyRunMode.PAPER,
      instruments=["605499.SH"],
      parameters=parameters,
      current_time=datetime(2026, 8, 20, 10, 0),
    )
  )
  await item.initialize()
  pending = ManagedEntryPlanState(
    phase=EntryPlanStatus.ENTRY_PENDING,
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
    pending_rule_id="trend-1",
    pending_requested_amount_cny=5_000,
  )
  item.apply_state_snapshot({MANAGED_ENTRY_STATE_KEY: pending.to_dict()})
  manager.runs["plan-1"] = _Runtime(strategy=item, parameters=parameters)
  manager.executor.apply_external_state_patch = (
    lambda _plan_id, patch: item.apply_state_snapshot(patch.set)
  )
  service._request_terminal = EntryPlanService._request_terminal.__get__(service)
  service.loaded = _LoadedPlan(
    run=SimpleNamespace(id="plan-1"),
    parameters=parameters,
    config=config,
  )
  service.facts = EntryPlanFacts(has_working_order=True)

  result = await service.cancel(
    "plan-1",
    account_id="acct-1",
    config_version=1,
    actor_user_id="user-1",
  )
  requested = ManagedEntryPlanState.from_dict(
    item.state.get(MANAGED_ENTRY_STATE_KEY)
  )

  assert result["code"] == "ENTRY_PLAN_DRAINING"
  assert requested.phase == EntryPlanStatus.DRAINING
  assert requested.terminal_requested == EntryPlanStatus.CANCELLED
  with pytest.raises(ValueError, match="ENTRY_PLAN_TERMINAL"):
    await EntryPlanService._require_plan_not_terminal(service, "plan-1")

  metadata = {
    "entry_plan_id": "plan-1",
    "entry_rule_id": "trend-1",
    "entry_stage_id": "stage-1",
  }
  await item.on_order(
    OrderStateEvent(
      order_id="order-1",
      status="CANCELLED",
      filled_volume=100,
      metadata=metadata,
      timestamp=datetime(2026, 8, 20, 10, 1),
    )
  )
  trade_patch = await item.on_trade(
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="605499.SH",
      trade_type="BUY",
      price=125,
      volume=100,
      trade_time=datetime(2026, 8, 20, 10, 1, 1),
      metadata={**metadata, "trade_id": "late-fill-1"},
    )
  )

  assert trade_patch is not None
  settled = trade_patch.set[MANAGED_ENTRY_STATE_KEY]
  assert settled["phase"] == "CANCELLED"
  assert settled["terminal_requested"] == "CANCELLED"
  assert settled["filled_volume"] == 100
  assert settled["filled_amount_cny"] == 12_500


@pytest.mark.asyncio
async def test_terminal_request_persists_without_loaded_runtime(monkeypatch) -> None:
  manager = _Manager()
  pending = ManagedEntryPlanState(
    phase=EntryPlanStatus.ENTRY_PENDING,
    pending_intent_id="intent-1",
    pending_stage_id="stage-1",
  )
  record = SimpleNamespace(
    custom_state={MANAGED_ENTRY_STATE_KEY: pending.to_dict()},
    cash=1_000.0,
    frozen_cash=500.0,
    total_asset=10_000.0,
    version=7,
  )
  saved: dict[str, Any] = {}

  class _Repository:
    def __init__(self, _db) -> None:
      pass

    async def get_state(self, plan_id: str):
      assert plan_id == "plan-offline"
      return record

    async def upsert_state(self, plan_id: str, **values: Any) -> bool:
      assert plan_id == "plan-offline"
      saved.update(values)
      record.custom_state = values["custom_state"]
      return True

  class _Session:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_args) -> None:
      return None

  monkeypatch.setattr(
    "quantx_infrastructure.services.entry_plan_service.StrategyRunStateRepository",
    _Repository,
  )
  service = EntryPlanService(manager, session_factory=_Session)

  await service._request_terminal(
    "plan-offline",
    EntryPlanStatus.EXPIRED,
    reason="ENTRY_PLAN_EXPIRED",
    pending_work=True,
  )

  restored = ManagedEntryPlanState.from_dict(
    saved["custom_state"][MANAGED_ENTRY_STATE_KEY]
  )
  assert restored.phase == EntryPlanStatus.DRAINING
  assert restored.terminal_requested == EntryPlanStatus.EXPIRED
  assert restored.terminal_request_reason == "ENTRY_PLAN_EXPIRED"
  assert saved["expected_version"] == 7
  with pytest.raises(ValueError, match="ENTRY_PLAN_TERMINAL"):
    await service._require_plan_not_terminal("plan-offline")


def test_authorization_scope_is_stable_and_binds_all_risk_limits() -> None:
  config = EntryPlanService._build_config(
    _input(environment="LIVE", authorization_mode="AUTO"),
    plan_id="plan-1",
    account_id="acct-1",
    config_version=6,
  )

  first = EntryPlanService.authorization_scope("plan-1", config, run_id="run-1")
  second = EntryPlanService.authorization_scope(
    "plan-1", config.to_dict(), run_id="run-1"
  )

  assert first == second
  assert first.plan_id == "plan-1"
  assert first.run_id == "run-1"
  assert first.config_version == 6
  assert first.instrument_code == "605499.SH"
  assert first.account_snapshot_version == "account-v7"
  assert first.max_total_amount_cny == 20_000
  assert first.max_single_amount_cny == 5_000
  assert first.max_daily_amount_cny == 10_000
  assert first.max_buy_price == 130
  assert len(first.plan_fingerprint) == 64
  assert len(first.rule_fingerprint) == 64
