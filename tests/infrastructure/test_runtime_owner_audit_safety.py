from __future__ import annotations

from types import SimpleNamespace

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.services import (
  account_execution_safety_service as safety_module,
)
from quantx_infrastructure.services import auto_exit_plan_service as plan_module
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.auto_exit_plan_service import (
  ActiveRuntimeExitPlanOwnerAuditError,
  AutoExitPlanService,
)
from sqlalchemy import JSON, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def owner_audit_database(monkeypatch: pytest.MonkeyPatch):
  instruments_column = StrategyRun.__table__.c.instruments
  original_instruments_type = instruments_column.type
  instruments_column.type = JSON()
  database = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with database.begin() as connection:
      await connection.run_sync(
        lambda sync_connection: Base.metadata.create_all(
          sync_connection,
          tables=[
            StrategyRun.__table__,
            AutoExitPlanRecord.__table__,
            AccountExecutionControl.__table__,
            AccountExecutionControlEvent.__table__,
          ],
        )
      )
    sessions = async_sessionmaker(database, expire_on_commit=False)
    monkeypatch.setattr(plan_module, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(safety_module, "AsyncSessionLocal", sessions)
    yield sessions
  finally:
    await database.dispose()
    instruments_column.type = original_instruments_type


def _runtime_plan(*, plan_id: str, run_id: str) -> AutoExitPlanRecord:
  account_id = "account-live"
  instrument_code = "600000.SH"
  source_type = "T_TRADE_BATCH"
  return AutoExitPlanRecord(
    plan_id=plan_id,
    account_id=account_id,
    instrument_code=instrument_code,
    bucket="swing",
    source_type=source_type,
    source_id=plan_id,
    strategy_run_id=run_id,
    enabled=True,
    status="ACTIVE",
    execution_mode="live",
    auto_exit_authorized=False,
    config_version=1,
    state_version=1,
    protected_volume=100,
    exited_volume=0,
    remaining_volume=100,
    entry_avg_price=10.0,
    plan_state={
      "template": {
        "plan_id": plan_id,
        "account_id": account_id,
        "instrument_code": instrument_code,
        "source_type": source_type,
        "run_id": run_id,
        "metadata": {},
      }
    },
  )


@pytest.mark.asyncio
async def test_preflight_reports_missing_strategy_run_structurally(
  owner_audit_database,
) -> None:
  run_id = "00000000-0000-0000-0000-000000000101"
  async with owner_audit_database() as db:
    db.add(_runtime_plan(plan_id="plan-orphan", run_id=run_id))
    await db.commit()

  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))
  with pytest.raises(ActiveRuntimeExitPlanOwnerAuditError) as captured:
    await service.preflight_active_runtime_owned_plans()

  assert captured.value.to_dict() == {
    "reasonCode": "ACTIVE_RUNTIME_EXIT_PLAN_OWNER_AUDIT_FAILED",
    "accountIds": ["account-live"],
    "failures": [
      {
        "planId": "plan-orphan",
        "strategyRunId": run_id,
        "accountId": "account-live",
        "ownerKind": "RUNTIME_BOOK",
        "reasonCode": "STRATEGY_RUN_MISSING",
        "message": "退出计划绑定的 StrategyRun 持久化记录不存在",
        "stage": "preflight",
      }
    ],
  }


@pytest.mark.asyncio
async def test_owner_audit_pause_is_durable_and_idempotent(
  owner_audit_database,
) -> None:
  run_id = "00000000-0000-0000-0000-000000000102"
  async with owner_audit_database() as db:
    db.add(_runtime_plan(plan_id="plan-unsafe", run_id=run_id))
    db.add(
      AccountExecutionControl(
        account_id="account-live",
        authorization_state="ENABLED",
        state_version=9,
        reconcile_status="READY",
        controlled_window_active=True,
        controlled_window_snapshot_id="snapshot-1",
      )
    )
    await db.commit()

  plan_service = AutoExitPlanService(
    SimpleNamespace(get_run=lambda _run_id: None)
  )
  with pytest.raises(ActiveRuntimeExitPlanOwnerAuditError) as captured:
    await plan_service.preflight_active_runtime_owned_plans()
  failures = [item.to_dict() for item in captured.value.failures]

  safety = AccountExecutionSafetyService()
  assert await safety.pause_for_runtime_owner_audit(
    "account-live",
    failures=failures,
  )
  assert not await safety.pause_for_runtime_owner_audit(
    "account-live",
    failures=failures,
  )

  async with owner_audit_database() as db:
    control = await db.get(AccountExecutionControl, "account-live")
    event_count = await db.scalar(
      select(func.count(AccountExecutionControlEvent.event_id))
    )
  assert control is not None
  assert control.authorization_state == "PAUSED"
  assert control.reconcile_status == "RECONCILE_REQUIRED"
  assert control.controlled_window_active is False
  assert control.controlled_window_snapshot_id is None
  assert control.state_version == 10
  assert "ACTIVE_RUNTIME_EXIT_PLAN_OWNER_AUDIT_FAILED" in str(
    control.paused_reason
  )
  assert event_count == 1


@pytest.mark.asyncio
async def test_preflight_recovers_after_durable_run_is_repaired(
  owner_audit_database,
) -> None:
  run_id = "00000000-0000-0000-0000-000000000103"
  async with owner_audit_database() as db:
    db.add(_runtime_plan(plan_id="plan-repaired", run_id=run_id))
    db.add(
      StrategyRun(
        id=run_id,
        name="repaired runtime",
        strategy_id=1,
        parameters={},
        status=StrategyRunStatus.RUNNING,
        mode=StrategyRunMode.LIVE,
        instruments=["600000.SH"],
      )
    )
    await db.commit()

  service = AutoExitPlanService(SimpleNamespace(get_run=lambda _run_id: None))

  assert await service.preflight_active_runtime_owned_plans() == {
    "examined": 1,
    "verified": ["plan-repaired"],
  }
