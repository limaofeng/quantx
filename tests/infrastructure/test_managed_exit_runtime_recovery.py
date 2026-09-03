from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_managed_exit_plan import (
  AshareManagedExitPlanStrategy,
)
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.managed_plan import ManagedPlanRecord
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import auto_exit_plan_service as auto_module
from quantx_infrastructure.services.auto_exit_plan_service import (
  MANAGED_RUNTIME_COMMAND_ID_KEY,
  MANAGED_RUNTIME_COMMAND_KIND_KEY,
  AutoExitPlanService,
)
from quantx_infrastructure.services.exit_plan_scope_lock import LockedExitPlanScope
from quantx_infrastructure.services.managed_plan_runtime_service import (
  ManagedPlanRuntimeService,
  managed_runtime_has_live_consumer,
)
from sqlalchemy import JSON, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _legacy_manual_record(
  *,
  plan_id: str,
  source_type: str,
  run_id: str,
  status: ExitPlanStatus,
  enabled: bool,
  exited_volume: int,
  pending_intent_id: str = "",
) -> AutoExitPlanRecord:
  plan = ExitPlan(
    template=ExitPlanTemplate(
      plan_id=plan_id,
      source_type=source_type,
      source_id=plan_id,
      account_id="account-1",
      instrument_code="600000.SH",
      bucket="manual",
      run_id=run_id,
      config_version=1,
      rules=[
        ExitRuleSpec(
          rule_id=f"{plan_id}:stop",
          strategy=ExitRuleType.STOP_PRICE,
          parameters={"stop_price": 9.8},
        )
      ],
      metadata={MANAGED_RUNTIME_COMMAND_ID_KEY: "legacy-command"},
    )
  )
  plan.register_entry_fill(volume=100, price=10.0)
  plan.exited_volume = exited_volume
  plan.status = status
  if pending_intent_id:
    plan.pending_intent_id = pending_intent_id
    plan.pending_rule_id = f"{plan_id}:stop"
    plan.pending_requested_volume = 60
  return AutoExitPlanRecord(
    plan_id=plan_id,
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="manual",
    source_type=source_type,
    source_id=plan_id,
    strategy_run_id=run_id or None,
    source_execution_owner_type=("STRATEGY_RUN" if run_id else "MANUAL_COMMAND"),
    source_execution_owner_id=run_id or plan_id,
    source_execution_environment="LIVE",
    enabled=enabled,
    status=status.value,
    environment="LIVE",
    auto_exit_authorized=True,
    auto_exit_authorization_fingerprint="f" * 64,
    auto_exit_authorization_config_version=1,
    auto_exit_authorized_at=datetime(2026, 8, 29, 9, 30),
    auto_exit_authorization_expires_at=datetime(2026, 8, 29, 15, 0),
    auto_exit_authorization_challenge_id="challenge-1",
    auto_exit_authorization_user_id="user-1",
    auto_exit_authorization_device_session_id="device-1",
    config_version=1,
    state_version=7,
    protected_volume=100,
    exited_volume=exited_volume,
    remaining_volume=100 - exited_volume,
    entry_avg_price=10.0,
    plan_state=plan.to_dict(),
  )


@pytest.fixture
async def migration_database(monkeypatch: pytest.MonkeyPatch):
  instruments_column = StrategyRun.__table__.c.instruments
  original_instruments_type = instruments_column.type
  instruments_column.type = JSON()
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with engine.begin() as connection:
      await connection.run_sync(
        lambda sync_connection: Base.metadata.create_all(
          sync_connection,
          tables=[
            StrategyRun.__table__,
            TradeIntentRecord.__table__,
            ManagedPlanRecord.__table__,
            AutoExitPlanRecord.__table__,
            AutoExitPlanEvent.__table__,
          ],
        )
      )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auto_module, "AsyncSessionLocal", factory)
    yield factory
  finally:
    await engine.dispose()
    instruments_column.type = original_instruments_type


class _RuntimeManager:
  def __init__(self) -> None:
    self.runtime = SimpleNamespace(
      strategy_class=AshareManagedExitPlanStrategy,
      strategy=None,
      status=SimpleNamespace(value="PAUSED"),
      task=None,
      context=SimpleNamespace(parameters={}),
    )

  def get_run(self, _run_id):
    return self.runtime

  async def update_run_parameters(self, _run_id, parameters):
    self.runtime.context.parameters = dict(parameters)

  async def resume_strategy(self, _run_id):
    self.runtime.status = SimpleNamespace(value="RUNNING")
    self.runtime.task = _RuntimeTask()
    return True

  async def start_strategy(self, _run_id):
    self.runtime.status = SimpleNamespace(value="RUNNING")
    self.runtime.task = _RuntimeTask()
    return True


class _RuntimeTask:
  def __init__(self, *, done: bool = False) -> None:
    self._done = done

  def done(self) -> bool:
    return self._done


class _ManagedRuntime:
  def __init__(self) -> None:
    self.current_run_id = ""
    self.current_version = 0
    self.create_calls = 0
    self.revise_calls = 0

  async def create(self, **_kwargs):
    self.create_calls += 1
    self.current_run_id = "run-created"
    self.current_version = 1
    return self.current_run_id, self.current_version

  async def revise(self, **_kwargs):
    self.revise_calls += 1
    previous = self.current_run_id
    self.current_run_id = "run-revised"
    self.current_version = 2
    return self.current_run_id, self.current_version, previous

  async def current_plan(self, _plan_id):
    return SimpleNamespace(
      plan_kind="EXIT",
      account_id="account-1",
      instrument_code="600000.SH",
      current_config_version=self.current_version,
      current_run_id=self.current_run_id,
    )

  async def validate_current_binding(self, **_kwargs):
    return self.current_run_id

  async def set_status(self, _plan_id, _status, **_kwargs):
    return None


@pytest.mark.parametrize(
  "task",
  [None, _RuntimeTask(done=True), _RuntimeTask(done=False)],
  ids=["missing", "done", "live"],
)
def test_managed_runtime_live_consumer_requires_running_live_task(task) -> None:
  runtime = SimpleNamespace(status=SimpleNamespace(value="RUNNING"), task=task)
  assert managed_runtime_has_live_consumer(runtime) is (
    task is not None and not task.done()
  )


@pytest.mark.asyncio
async def test_managed_runtime_start_bool_never_substitutes_for_live_task() -> None:
  class Manager:
    def __init__(self) -> None:
      self.runtime = SimpleNamespace(
        status=SimpleNamespace(value="RUNNING"),
        task=None,
      )

    def get_run(self, _run_id):
      return self.runtime

    async def start_strategy(self, _run_id):
      return True

    async def resume_strategy(self, _run_id):
      return True

  manager = Manager()
  service = ManagedPlanRuntimeService(manager)
  assert not await service._ensure_started("run-1")
  manager.runtime.task = _RuntimeTask()
  assert await service._ensure_started("run-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("task", [None, _RuntimeTask(done=True)], ids=["missing", "done"])
async def test_auto_runtime_finalizers_reject_non_consuming_running_owner(task) -> None:
  manager = SimpleNamespace(
    get_run=lambda _run_id: SimpleNamespace(
      status=SimpleNamespace(value="RUNNING"),
      task=task,
    )
  )
  service = AutoExitPlanService(manager)
  plan = ExitPlan.from_dict(
    dict(
      _legacy_manual_record(
        plan_id="plan-live-task",
        source_type="MANUAL_POSITION",
        run_id="run-live-task",
        status=ExitPlanStatus.PAUSED,
        enabled=False,
        exited_volume=0,
      ).plan_state
      or {}
    )
  )
  record = SimpleNamespace(
    plan_id=plan.plan_id,
    strategy_run_id="run-live-task",
  )
  with pytest.raises(RuntimeError, match="活动消费任务"):
    await service._finalize_managed_runtime_binding(
      plan.plan_id,
      "run-live-task",
      plan=plan,
      enabled=True,
      command_id="legacy-command",
    )
  with pytest.raises(RuntimeError, match="活动消费任务"):
    await service._finalize_managed_runtime_enabled(
      record,
      plan,
      enabled=True,
      command_id="enable-command",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command_kind", ["CREATE", "UPDATE"])
async def test_binding_finalizer_preserves_state_committed_after_runtime_start(
  migration_database,
  command_kind: str,
) -> None:
  plan_id = f"plan-finalize-{command_kind.lower()}"
  command_id = f"command-{command_kind.lower()}"
  run_id = f"run-{command_kind.lower()}"
  record = _legacy_manual_record(
    plan_id=plan_id,
    source_type="MANUAL_POSITION",
    run_id=run_id,
    status=ExitPlanStatus.PAUSED,
    enabled=False,
    exited_volume=0,
  )
  initial = ExitPlan.from_dict(dict(record.plan_state or {}))
  initial.template = ExitPlanTemplate.from_dict(
    {
      **initial.template.to_dict(),
      "metadata": {
        **dict(initial.template.metadata or {}),
        MANAGED_RUNTIME_COMMAND_ID_KEY: command_id,
        MANAGED_RUNTIME_COMMAND_KIND_KEY: command_kind,
      },
    }
  )
  record.plan_state = initial.to_dict()
  record.last_error = (
    f"MANAGED_RUNTIME_BINDING_PENDING:{command_id}:v1"
  )
  async with migration_database() as db:
    db.add(record)
    await db.commit()

  stale_plan = ExitPlan.from_dict(initial.to_dict())
  async with migration_database() as db:
    current = await db.get(AutoExitPlanRecord, plan_id, with_for_update=True)
    assert current is not None
    fresh = ExitPlan.from_dict(dict(current.plan_state or {}))
    fresh.exited_volume = 20
    fresh.exit_avg_price = 10.8
    fresh.pending_intent_id = "intent-after-start"
    fresh.pending_order_id = "client-after-start"
    fresh.pending_rule_id = f"{plan_id}:stop"
    fresh.pending_requested_volume = 80
    fresh.status = ExitPlanStatus.EXIT_PENDING
    AutoExitPlanService._sync_record(current, fresh)
    current.last_error = f"MANAGED_RUNTIME_BINDING_PENDING:{command_id}:v1"
    await db.commit()

  manager = _RuntimeManager()
  manager.runtime.status = SimpleNamespace(value="RUNNING")
  manager.runtime.task = _RuntimeTask()
  service = AutoExitPlanService(manager)
  await service._finalize_managed_runtime_binding(
    plan_id,
    run_id,
    plan=stale_plan,
    enabled=True,
    command_id=command_id,
  )

  async with migration_database() as db:
    current = await db.get(AutoExitPlanRecord, plan_id)
    assert current is not None and current.last_error is None
    finalized = ExitPlan.from_dict(dict(current.plan_state or {}))
    assert finalized.exited_volume == 20
    assert finalized.pending_intent_id == "intent-after-start"
    assert finalized.pending_order_id == "client-after-start"
    assert finalized.status == ExitPlanStatus.EXIT_PENDING


@pytest.mark.asyncio
async def test_enable_finalizer_and_failure_compensation_preserve_fresh_state(
  migration_database,
) -> None:
  run_id = "run-enable-fresh"
  manager = _RuntimeManager()
  manager.runtime.status = SimpleNamespace(value="RUNNING")
  manager.runtime.task = _RuntimeTask()
  service = AutoExitPlanService(manager)
  record = _legacy_manual_record(
    plan_id="plan-enable-fresh",
    source_type="MANUAL_POSITION",
    run_id=run_id,
    status=ExitPlanStatus.PAUSED,
    enabled=False,
    exited_volume=0,
  )
  stale_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  record.last_error = service._managed_enable_marker(
    "enable-command",
    enabled=True,
    config_version=1,
  )
  async with migration_database() as db:
    db.add(record)
    await db.commit()
  async with migration_database() as db:
    current = await db.get(
      AutoExitPlanRecord,
      "plan-enable-fresh",
      with_for_update=True,
    )
    assert current is not None
    fresh = ExitPlan.from_dict(dict(current.plan_state or {}))
    fresh.exited_volume = 20
    fresh.exit_avg_price = 10.8
    fresh.pending_intent_id = "intent-enable-window"
    fresh.pending_order_id = "client-enable-window"
    fresh.pending_rule_id = "plan-enable-fresh:stop"
    fresh.pending_requested_volume = 80
    fresh.status = ExitPlanStatus.EXIT_PENDING
    AutoExitPlanService._sync_record(current, fresh)
    current.last_error = service._managed_enable_marker(
      "enable-command",
      enabled=True,
      config_version=1,
    )
    await db.commit()

  await service._finalize_managed_runtime_enabled(
    SimpleNamespace(plan_id="plan-enable-fresh", strategy_run_id=run_id),
    stale_plan,
    enabled=True,
    command_id="enable-command",
  )
  async with migration_database() as db:
    current = await db.get(AutoExitPlanRecord, "plan-enable-fresh")
    assert current is not None and current.last_error is None
    finalized = ExitPlan.from_dict(dict(current.plan_state or {}))
    assert finalized.exited_volume == 20
    assert finalized.pending_intent_id == "intent-enable-window"
    assert finalized.status == ExitPlanStatus.EXIT_PENDING

    finalized.status = ExitPlanStatus.ERROR
    finalized.error_message = (
      "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:intent-enable-window"
    )
    AutoExitPlanService._sync_record(current, finalized)
    current.last_error = finalized.error_message
    await db.commit()

  service._managed_runtime = None
  await service._mark_managed_runtime_failed(
    "plan-enable-fresh",
    run_id,
    plan=stale_plan,
    error="stale runtime start failure",
  )
  async with migration_database() as db:
    current = await db.get(AutoExitPlanRecord, "plan-enable-fresh")
    assert current is not None and not current.enabled
    failed = ExitPlan.from_dict(dict(current.plan_state or {}))
    assert failed.exited_volume == 20
    assert failed.pending_intent_id == "intent-enable-window"
    assert failed.error_message == (
      "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:intent-enable-window"
    )
    assert current.last_error == failed.error_message
    assert not current.auto_exit_authorized


@pytest.mark.asyncio
@pytest.mark.parametrize(
  (
    "owner_kind",
    "source_type",
    "run_id",
    "managed_marker",
    "sticky_error",
  ),
  [
    (
      "monitor",
      "MANUAL_POSITION",
      "",
      False,
      "ACCOUNT_WIDE_STALE_SELL:intent-old",
    ),
    (
      "runtime-book",
      "T_TRADE_BATCH",
      "run-runtime",
      False,
      "ACCOUNT_WIDE_STALE_SELL:intent-old",
    ),
    (
      "dedicated",
      "MANUAL_POSITION",
      "run-dedicated",
      True,
      "ACCOUNT_WIDE_STALE_SELL:intent-old",
    ),
    (
      "monitor-repaired",
      "MANUAL_POSITION",
      "",
      False,
      "QUARANTINE_REPAIRED:intent-old",
    ),
    (
      "runtime-repaired",
      "T_TRADE_BATCH",
      "run-runtime-repaired",
      False,
      "QUARANTINE_REPAIRED:intent-old",
    ),
    (
      "dedicated-repaired",
      "MANUAL_POSITION",
      "run-dedicated-repaired",
      True,
      "QUARANTINE_REPAIRED:intent-old",
    ),
  ],
)
async def test_sticky_error_cannot_be_enabled_for_any_exit_owner(
  migration_database,
  owner_kind: str,
  source_type: str,
  run_id: str,
  managed_marker: bool,
  sticky_error: str,
) -> None:
  plan_id = f"plan-sticky-{owner_kind}"
  record = _legacy_manual_record(
    plan_id=plan_id,
    source_type=source_type,
    run_id=run_id,
    status=ExitPlanStatus.ERROR,
    enabled=False,
    exited_volume=20,
  )
  plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  plan.template = ExitPlanTemplate.from_dict(
    {
      **plan.template.to_dict(),
      "metadata": (
        {MANAGED_RUNTIME_COMMAND_ID_KEY: "managed-command"}
        if managed_marker
        else {}
      ),
    }
  )
  plan.status = ExitPlanStatus.ERROR
  plan.error_message = sticky_error
  record.plan_state = plan.to_dict()
  record.status = ExitPlanStatus.ERROR.value
  record.enabled = False
  record.last_error = plan.error_message
  async with migration_database() as db:
    db.add(record)
    await db.commit()
    expected_state_version = int(record.state_version or 0)

  with pytest.raises(ValueError, match="EXIT_PLAN_RECONCILIATION_REQUIRED"):
    await AutoExitPlanService().set_enabled(plan_id, True)

  async with migration_database() as db:
    stored = await db.get(AutoExitPlanRecord, plan_id)
    assert stored is not None and not stored.enabled and stored.status == "ERROR"
    unchanged = ExitPlan.from_dict(dict(stored.plan_state or {}))
    assert unchanged.status == ExitPlanStatus.ERROR
    assert unchanged.error_message == plan.error_message
    assert int(stored.state_version or 0) == expected_state_version


@pytest.mark.asyncio
async def test_set_enabled_rechecks_sticky_error_after_initial_owner_read(
  migration_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  plan_id = "plan-sticky-race-monitor"
  record = _legacy_manual_record(
    plan_id=plan_id,
    source_type="MANUAL_POSITION",
    run_id="",
    status=ExitPlanStatus.PAUSED,
    enabled=False,
    exited_volume=20,
  )
  initial_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  initial_plan.template = ExitPlanTemplate.from_dict(
    {
      **initial_plan.template.to_dict(),
      "metadata": {},
    }
  )
  record.plan_state = initial_plan.to_dict()
  record.last_error = None
  async with migration_database() as db:
    db.add(record)
    await db.commit()

  session_number = 0
  invalidation_error = "EXIT_FILL_INTENT_MISMATCH:intent-old:CURRENT_PENDING:NONE"

  @asynccontextmanager
  async def racing_session_factory():
    nonlocal session_number
    session_number += 1
    current_session = session_number
    async with migration_database() as db:
      yield db
    if current_session == 1:
      # Simulate broker-proof invalidation committing after the public method's
      # owner projection read but before its authoritative owner boundary.
      async with migration_database() as invalidation_db:
        current = await invalidation_db.get(AutoExitPlanRecord, plan_id)
        assert current is not None
        invalidated = ExitPlan.from_dict(dict(current.plan_state or {}))
        invalidated.status = ExitPlanStatus.ERROR
        invalidated.error_message = invalidation_error
        AutoExitPlanService._sync_record(current, invalidated)
        current.enabled = False
        current.last_error = invalidation_error
        await invalidation_db.commit()

  monkeypatch.setattr(auto_module, "AsyncSessionLocal", racing_session_factory)
  service = AutoExitPlanService()

  with pytest.raises(ValueError, match="EXIT_PLAN_RECONCILIATION_REQUIRED"):
    await service.set_enabled(
      plan_id,
      True,
      command_id="",
    )

  assert session_number >= 2
  async with migration_database() as db:
    stored = await db.get(AutoExitPlanRecord, plan_id)
    assert stored is not None
    assert not stored.enabled
    assert stored.status == ExitPlanStatus.ERROR.value
    sticky = ExitPlan.from_dict(dict(stored.plan_state or {}))
    assert sticky.status == ExitPlanStatus.ERROR
    assert sticky.error_message == invalidation_error
    assert stored.last_error == invalidation_error


@pytest.mark.asyncio
async def test_monitor_manual_create_update_and_exact_replay(
  migration_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def locked_scope(db, **kwargs):
    target_plan_id = str(kwargs.get("target_plan_id") or "")
    target = (
      await db.get(AutoExitPlanRecord, target_plan_id)
      if target_plan_id
      else None
    )
    return LockedExitPlanScope(
      position=SimpleNamespace(
        volume=100,
        can_use_volume=100,
        created_at=None,
      ),
      plans=[target] if target is not None else [],
      target_plan=target,
    )

  monkeypatch.setattr(auto_module, "lock_exit_plan_scope", locked_scope)
  manager = _RuntimeManager()
  managed = _ManagedRuntime()
  service = AutoExitPlanService(manager)
  service._managed_runtime = managed
  monkeypatch.setattr(service, "_strategy_template_id", AsyncMock(return_value=7))
  create_payload = {
    "account_id": "account-1",
    "instrument_code": "600000.SH",
    "protected_volume": 100,
    "enabled": True,
    "execution_mode": "paper",
    "rules": [
      {
        "rule_id": "stop",
        "strategy": "STOP_PRICE",
        "parameters": {"stop_price": 9.8},
      }
    ],
    "cost_basis": {
      "mode": "MANUAL_UNIT_COST",
      "unit_cost_cny": 10.0,
    },
  }
  created = await service.create_manual_exit_plan(
    create_payload,
    command_id="command-create",
  )
  assert created.strategy_run_id is None
  assert created.enabled is True
  assert created.status == ExitPlanStatus.ACTIVE.value
  assert created.last_error is None
  created_template = ExitPlan.from_dict(dict(created.plan_state or {})).template
  assert created_template.run_id == ""
  created_metadata = dict(created_template.metadata)
  assert MANAGED_RUNTIME_COMMAND_ID_KEY not in created_metadata
  assert MANAGED_RUNTIME_COMMAND_KIND_KEY not in created_metadata
  assert managed.create_calls == 0

  update_payload = {
    "account_id": "account-1",
    "plan_id": created.plan_id,
    "config_version": 1,
    "protected_volume": 100,
    "execution_mode": "paper",
    "rules": [
      {
        "rule_id": "stop",
        "strategy": "STOP_PRICE",
        "parameters": {"stop_price": 9.7},
      }
    ],
    "remark": "revised",
  }
  updated = await service.update_manual_exit_plan(
    update_payload,
    command_id="command-update",
  )
  assert updated.strategy_run_id is None
  assert updated.config_version == 2
  assert updated.enabled is True
  assert updated.status == ExitPlanStatus.ACTIVE.value
  assert updated.last_error is None
  updated_template = ExitPlan.from_dict(dict(updated.plan_state or {})).template
  assert updated_template.run_id == ""
  updated_metadata = dict(updated_template.metadata)
  assert MANAGED_RUNTIME_COMMAND_ID_KEY not in updated_metadata
  assert MANAGED_RUNTIME_COMMAND_KIND_KEY not in updated_metadata
  assert managed.revise_calls == 0

  replayed = await service.update_manual_exit_plan(
    update_payload,
    command_id="command-update",
  )
  assert replayed.strategy_run_id is None
  assert replayed.config_version == 2
  assert managed.revise_calls == 0


@pytest.mark.asyncio
async def test_monitor_owned_manual_update_is_committed_without_runtime_mutation(
  migration_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  legacy = _legacy_manual_record(
    plan_id="legacy-monitor-plan",
    source_type="MANUAL_POSITION",
    run_id="",
    status=ExitPlanStatus.ACTIVE,
    enabled=True,
    exited_volume=0,
  )
  legacy_plan = ExitPlan.from_dict(dict(legacy.plan_state or {}))
  legacy_plan.template = ExitPlanTemplate.from_dict(
    {**legacy_plan.template.to_dict(), "metadata": {}}
  )
  legacy.plan_state = legacy_plan.to_dict()
  legacy.strategy_run_id = None
  async with migration_database() as db:
    db.add(legacy)
    await db.commit()

  async def locked_scope(db, **_kwargs):
    current = await db.get(AutoExitPlanRecord, legacy.plan_id)
    return LockedExitPlanScope(
      position=SimpleNamespace(volume=100, can_use_volume=100),
      plans=[current],
      target_plan=current,
    )

  monkeypatch.setattr(auto_module, "lock_exit_plan_scope", locked_scope)
  service = AutoExitPlanService(_RuntimeManager())
  updated = await service.update_manual_exit_plan(
    {
      "account_id": "account-1",
      "plan_id": legacy.plan_id,
      "config_version": 1,
      "rules": [
        {
          "rule_id": "stop",
          "strategy": "STOP_PRICE",
          "parameters": {"stop_price": 9.7},
        }
      ],
    },
    command_id="command-update-legacy",
  )
  assert updated.config_version == 2
  assert updated.strategy_run_id is None
  async with migration_database() as db:
    stored = await db.get(AutoExitPlanRecord, legacy.plan_id)
    assert stored is not None
    assert stored.config_version == 2
    assert stored.strategy_run_id is None
    assert stored.status == ExitPlanStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_monitor_owned_sticky_plan_cannot_be_disguised_by_rule_update(
  migration_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  record = _legacy_manual_record(
    plan_id="monitor-repaired-plan",
    source_type="MANUAL_POSITION",
    run_id="",
    status=ExitPlanStatus.ERROR,
    enabled=False,
    exited_volume=0,
  )
  plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  plan.error_message = "QUARANTINE_REPAIRED:intent-old"
  plan.status = ExitPlanStatus.ERROR
  record.plan_state = plan.to_dict()
  record.last_error = "QUARANTINE_REPAIRED:intent-old"
  record.strategy_run_id = None
  async with migration_database() as db:
    db.add(record)
    await db.commit()

  async def locked_scope(db, **_kwargs):
    current = await db.get(AutoExitPlanRecord, record.plan_id)
    return LockedExitPlanScope(
      position=SimpleNamespace(volume=100, can_use_volume=100),
      plans=[current],
      target_plan=current,
    )

  monkeypatch.setattr(auto_module, "lock_exit_plan_scope", locked_scope)
  service = AutoExitPlanService(_RuntimeManager())

  with pytest.raises(ValueError, match="EXIT_PLAN_REBUILD_REQUIRED"):
    await service.update_manual_exit_plan(
      {
        "account_id": "account-1",
        "plan_id": record.plan_id,
        "config_version": 1,
        "protected_volume": 100,
        "execution_mode": "paper",
        "rules": [
          {
            "rule_id": "stop",
            "strategy": "STOP_PRICE",
            "parameters": {"stop_price": 9.7},
          }
        ],
      },
      command_id="command-update-repaired",
    )

  async with migration_database() as db:
    stored = await db.get(AutoExitPlanRecord, record.plan_id)
    assert stored is not None
    assert stored.config_version == 1
    assert stored.status == ExitPlanStatus.ERROR.value
    stored_plan = ExitPlan.from_dict(dict(stored.plan_state or {}))
    assert stored_plan.status == ExitPlanStatus.ERROR
    assert stored_plan.error_message == "QUARANTINE_REPAIRED:intent-old"


@pytest.mark.asyncio
async def test_manual_plan_migration_detaches_pending_plan_without_losing_links(
  migration_database,
) -> None:
  plan_id = "manual-plan-1"
  run_id = "00000000-0000-0000-0000-000000000101"
  intent_id = "00000000-0000-0000-0000-000000000201"
  record = _legacy_manual_record(
    plan_id=plan_id,
    source_type="MANUAL_POSITION",
    run_id=run_id,
    status=ExitPlanStatus.EXIT_PENDING,
    enabled=True,
    exited_volume=40,
    pending_intent_id=intent_id,
  )
  async with migration_database() as db:
    db.add(
      StrategyRun(
        id=run_id,
        name="legacy-managed-exit",
        strategy_id=7,
        parameters={},
        status=StrategyRunStatus.RUNNING,
        mode=StrategyRunMode.LIVE,
        instruments=["600000.SH"],
        plan_id=plan_id,
        plan_kind="EXIT",
        plan_config_version=1,
        frozen_config_snapshot={"plan_id": plan_id},
        frozen_config_fingerprint="a" * 64,
      )
    )
    db.add(
      ManagedPlanRecord(
        plan_id=plan_id,
        plan_kind="EXIT",
        account_id="account-1",
        instrument_code="600000.SH",
        status="RUNNING",
        current_config_version=1,
        current_run_id=run_id,
      )
    )
    db.add(record)
    db.add(
      TradeIntentRecord(
        id=intent_id,
        strategy_run_id=None,
        owner_type="EXIT_PLAN",
        owner_id=plan_id,
        environment="LIVE",
        idempotency_key=f"exit-plan:{intent_id}",
        account_id="account-1",
        instrument_code="600000.SH",
        direction="SELL",
        bucket="manual",
        reason="legacy exit",
        target_volume=60,
        status="AWAITING_APPROVAL",
        intent_metadata={"exit_plan_id": plan_id},
      )
    )
    await db.commit()

  service = AutoExitPlanService()
  first = await service.migrate_manual_plans_to_monitor()
  second = await service.migrate_manual_plans_to_monitor()

  assert first == {"migrated": 1, "stopped_runs": 1}
  assert second == {"migrated": 0, "stopped_runs": 0}
  async with migration_database() as db:
    preserved = await db.get(AutoExitPlanRecord, plan_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    run = await db.get(StrategyRun, run_id)
    managed = await db.get(ManagedPlanRecord, plan_id)
    assert preserved is not None
    preserved_plan = ExitPlan.from_dict(dict(preserved.plan_state or {}))
    assert preserved.strategy_run_id is None
    assert preserved.enabled is True
    assert preserved.status == ExitPlanStatus.EXIT_PENDING.value
    assert preserved.config_version == 2
    assert preserved.state_version == 8
    assert preserved.exited_volume == 40
    assert preserved.remaining_volume == 60
    assert preserved_plan.pending_intent_id == intent_id
    assert preserved_plan.template.run_id == ""
    assert MANAGED_RUNTIME_COMMAND_ID_KEY not in preserved_plan.template.metadata
    assert preserved.auto_exit_authorized is False
    assert preserved.auto_exit_authorization_fingerprint is None
    assert intent is not None
    assert intent.owner_type == "EXIT_PLAN"
    assert intent.owner_id == plan_id
    assert intent.strategy_run_id is None
    assert run is not None and run.status == StrategyRunStatus.STOPPED
    assert managed is not None and managed.current_run_id is None
    assert managed.status == "MIGRATED_TO_MONITOR"
    assert (
      await db.scalar(
        select(func.count())
        .select_from(AutoExitPlanEvent)
        .where(AutoExitPlanEvent.plan_id == plan_id)
      )
      == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("source_type", "status", "enabled"),
  [
    ("MANUAL_POSITION", ExitPlanStatus.ACTIVE, True),
    ("MANUAL_POSITION", ExitPlanStatus.PAUSED, False),
    ("MANUAL_LIQUIDATION", ExitPlanStatus.ACTIVE, True),
  ],
)
async def test_manual_plan_migration_preserves_enabled_and_status(
  migration_database,
  source_type: str,
  status: ExitPlanStatus,
  enabled: bool,
) -> None:
  suffix = f"{source_type.lower()}-{status.value.lower()}"
  plan_id = f"plan-{suffix}"
  run_id = f"00000000-0000-0000-0000-{abs(hash(suffix)) % 10**12:012d}"
  async with migration_database() as db:
    db.add(
      _legacy_manual_record(
        plan_id=plan_id,
        source_type=source_type,
        run_id=run_id,
        status=status,
        enabled=enabled,
        exited_volume=0,
      )
    )
    await db.commit()

  result = await AutoExitPlanService().migrate_manual_plans_to_monitor()

  assert result == {"migrated": 1, "stopped_runs": 0}
  async with migration_database() as db:
    migrated = await db.get(AutoExitPlanRecord, plan_id)
    assert migrated is not None
    assert migrated.strategy_run_id is None
    assert migrated.enabled is enabled
    assert migrated.status == status.value
