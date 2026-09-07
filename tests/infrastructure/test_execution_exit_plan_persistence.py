"""Execution-owned PAPER plans use the same durable ExitPlan aggregate."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import Position
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanConcurrencyError,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from sqlalchemy import event, func, select

from tests.infrastructure.test_t_assistant_runtime_repository import (
  NOW,
  _seed_execution,
)
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _base_sessions,
)

base_sessions = _base_sessions


@pytest.fixture
async def sessions(base_sessions):
  event.listen(
    base_sessions.kw["bind"].sync_engine,
    "begin",
    lambda connection: connection.exec_driver_sql("BEGIN"),
  )
  async with base_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          AutoExitPlanRecord.__table__,
          AutoExitPlanEvent.__table__,
          PaperExecutionAccountRecord.__table__,
        ],
      )
    )
  return base_sessions


async def seed(sessions):
  _, execution_id = await _seed_execution(sessions)

  async def unused_sink(db, scope, result):
    raise AssertionError("initialization cannot publish receipts")

  async with sessions() as db:
    async with db.begin():
      await PaperExecutionLedger(db, receipt_sink=unused_sink).initialize(
        execution_id=execution_id,
        account_id="account-1",
        cash=100000.0,
        non_trading_asset_value=0.0,
        positions={
          "600000.SH": Position(
            "600000.SH",
            long_volume=1200,
            available_volume=1000,
            today_buy_volume=200,
            long_avg_price=10.0,
            last_price=10.0,
            market_value=12000.0,
          )
        },
        bucket_checkpoint={
          "instruments": {
            "600000.SH": {
              "swing": {
                "bucket": "swing",
                "total_volume": 1200,
                "available_volume": 1000,
                "today_buy_volume": 200,
                "frozen_volume": 0,
              }
            }
          }
        },
        seed_as_of=NOW,
        seed_snapshot_id="seed",
        seed_snapshot_hash="a" * 64,
      )
  return ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id)


def template(owner):
  return ExitPlanTemplate(
    plan_id="t-plan",
    source_type="T_TRADE_BATCH",
    source_id="actual-t-batch",
    account_id="account-1",
    instrument_code="600000.SH",
    bucket="swing",
    run_id="",
    metadata={"source_execution_ref": owner.to_dict()},
    rules=[
      ExitRuleSpec(
        rule_id="stop", strategy=ExitRuleType.HARD_STOP, parameters={"stop_price": 9.0}
      )
    ],
  )


async def fill(service, db, owner, *, key="fill-1", volume=100, at=NOW, source=None):
  return await service.register_execution_entry_fill(
    execution_ref=owner,
    environment=ExecutionEnvironment.PAPER,
    exit_plan_template=(source or template(owner)).to_dict(),
    volume=volume,
    price=10.0,
    trade_time=at,
    event_business_key=key,
    db=db,
    commit=False,
  )


async def test_paper_entry_creation_append_and_exact_idempotency_without_live_tables(
  sessions,
):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  async with sessions() as db:
    async with db.begin():
      first, version = await fill(service, db, owner)
      again, same_version = await fill(service, db, owner)
      assert again == first and same_version == version
      with pytest.raises(ValueError, match="IDEMPOTENCY"):
        await fill(service, db, owner, volume=101)
      final, new_version = await fill(
        service, db, owner, key="fill-2", volume=50, at=NOW + timedelta(seconds=1)
      )
      assert final["entry_filled_volume"] == 150 and new_version > version
    row = await db.get(AutoExitPlanRecord, "t-plan")
    assert (
      row.strategy_run_id is None
      and row.source_type == "T_TRADE_BATCH"
      and row.source_id == "actual-t-batch"
    )
    assert (
      row.source_execution_owner_id == owner.owner_id and row.environment == "PAPER"
    )
    assert row.plan_state["template"]["run_id"] == ""
    metadata = row.plan_state["template"]["metadata"]
    assert metadata["source_execution_owner_type"] == "T_ASSISTANT_EXECUTION"
    assert metadata["source_execution_owner_id"] == owner.owner_id
    assert metadata["source_execution_environment"] == "PAPER"
    assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 2


async def test_entry_instant_idempotency_and_shanghai_trade_day(sessions):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  instant = datetime(2026, 9, 7, 16, 30, tzinfo=UTC)
  async with sessions() as db:
    async with db.begin():
      first, version = await fill(service, db, owner, at=instant)
      assert first["entry_trade_date"] == "2026-09-08"
      assert first["last_holding_trade_date"] == "2026-09-08"
    # A fresh read may carry a naive UTC database timestamp; both forms must
    # identify the exact same receipt without appending another entry fill.
  async with sessions() as db:
    async with db.begin():
      for same_instant in (
        instant.astimezone(ZoneInfo("Asia/Shanghai")),
        instant.replace(tzinfo=None),
      ):
        again, same_version = await fill(service, db, owner, at=same_instant)
        assert again == first and same_version == version
      with pytest.raises(ValueError, match="IDEMPOTENCY"):
        await fill(service, db, owner, at=instant + timedelta(microseconds=1))
      assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 1


@pytest.mark.parametrize("status", ["EXIT_PENDING", "PAUSED", "ERROR"])
async def test_partial_entry_fill_preserves_existing_operational_state(
  sessions, status
):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  async with sessions() as db:
    async with db.begin():
      state, version = await fill(service, db, owner)
      plan = ExitPlan.from_dict(state)
      plan.status = ExitPlanStatus(status)
      plan.pending_exit_intent_id = "pending" if status == "EXIT_PENDING" else None
      state, _ = await service.persist_execution_plan_state(
        execution_ref=owner,
        environment=ExecutionEnvironment.PAPER,
        plan_state=plan.to_dict(),
        expected_state_version=version,
        event_business_key="operational-state",
        db=db,
        commit=False,
      )
      state, _ = await fill(service, db, owner, key="fill-2", volume=50)
      assert state["status"] == status and state["entry_filled_volume"] == 150


async def test_persist_cas_rejects_stale_transition(sessions):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  async with sessions() as db:
    async with db.begin():
      state, version = await fill(service, db, owner)
      plan = ExitPlan.from_dict(state)
      plan.status = ExitPlanStatus.PAUSED
      await service.persist_execution_plan_state(
        execution_ref=owner,
        environment=ExecutionEnvironment.PAPER,
        plan_state=plan.to_dict(),
        expected_state_version=version,
        event_business_key="pause",
        db=db,
        commit=False,
      )
      plan.status = ExitPlanStatus.ERROR
      with pytest.raises(AutoExitPlanConcurrencyError):
        await service.persist_execution_plan_state(
          execution_ref=owner,
          environment=ExecutionEnvironment.PAPER,
          plan_state=plan.to_dict(),
          expected_state_version=version,
          event_business_key="stale",
          db=db,
          commit=False,
        )
    assert (await db.get(AutoExitPlanRecord, "t-plan")).status == "PAUSED"


async def test_source_scope_conflicts_and_outer_rollback(sessions):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  async with sessions() as db:
    with pytest.raises(RuntimeError, match="outer"):
      async with db.begin():
        await fill(service, db, owner)
        raise RuntimeError("outer")
  async with sessions() as db:
    assert await db.get(AutoExitPlanRecord, "t-plan") is None
    assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 0
    await db.rollback()
    async with db.begin():
      source = template(owner)
      source.metadata["source_execution_owner_id"] = "wrong"
      with pytest.raises(ValueError, match="source binding"):
        await fill(service, db, owner, source=source)
      await fill(service, db, owner)
      other = ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "other")
      with pytest.raises(ValueError):
        await fill(service, db, other, key="cross", source=template(other))


async def test_stopped_source_does_not_prevent_expanding_existing_protection(sessions):
  owner = await seed(sessions)
  service = AutoExitPlanService()
  async with sessions() as db:
    async with db.begin():
      await fill(service, db, owner)
      repository = TAssistantExecutionRepository(db)
      current = await repository.get_domain(owner.owner_id)
      for status in ("DRAINING", "STOPPED"):
        revised = current.transition(status, at=NOW, has_unsettled_buy_work=False)
        await repository.save_transition_with_event(
          revised,
          expected_state_version=current.state_version,
          event=TAssistantExecutionEvent(owner.owner_id, status, status, NOW, {}),
        )
        current = revised
      state, _ = await fill(service, db, owner, key="late-entry", volume=50)
      assert state["entry_filled_volume"] == 150
