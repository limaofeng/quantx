from datetime import datetime

import pytest
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  HISTORY_DELETED_EVENT,
  AutoExitPlanRepository,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _record(plan_id="plan-1", status="CANCELLED", **overrides):
  values = dict(
    plan_id=plan_id,
    account_id="account-1",
    instrument_code="600000.SH",
    source_type="T_TRADE_BATCH",
    source_id=plan_id,
    strategy_run_id="run-1",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id="run-1",
    source_execution_environment="LIVE",
    environment="LIVE",
    status=status,
    enabled=False,
    protected_volume=100,
    remaining_volume=100,
    entry_avg_price=10,
    plan_state={"status": status, "pending_order_id": ""},
  )
  return AutoExitPlanRecord(**(values | overrides))


@pytest.fixture
async def db():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(AutoExitPlanRecord.__table__.create)
    await connection.run_sync(AutoExitPlanEvent.__table__.create)
  try:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
      yield session
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_delete_history_preserves_plan_and_audit_and_is_idempotent(db, status):
  record = _record(status=status)
  db.add(record)
  db.add(
    AutoExitPlanEvent(
      event_id="fill-event",
      business_key="fill-1",
      plan_id=record.plan_id,
      event_type="ORDER_FILLED",
      payload={"volume": 100},
      created_at=datetime(2026, 8, 30),
    )
  )
  await db.commit()
  repository = AutoExitPlanRepository(db)

  await repository.delete_history(plan_id=record.plan_id, account_id="account-1")
  await repository.delete_history(plan_id=record.plan_id, account_id="account-1")

  assert await repository.find_all(exclude_deleted_history=True) == []
  assert await repository.find_by_id(record.plan_id) is record
  assert len(await repository.find_for_strategy_run("run-1")) == 1
  assert len(await repository.find_all()) == 1
  events = await repository.find_events(plan_id=record.plan_id)
  assert {event.event_type for event in events} == {
    "ORDER_FILLED",
    HISTORY_DELETED_EVENT,
  }
  assert record.status == status
  assert record.plan_state == {"status": status, "pending_order_id": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "overrides",
  [
    {"status": status}
    for status in [
      "ACTIVE",
      "PAUSED",
      "ERROR",
      "EXIT_PENDING",
      "PARTIALLY_EXITED",
      "PENDING_ENTRY",
    ]
  ]
  + [
    {"pending_client_order_id": "pending-1"},
    {"plan_state": {"status": "CANCELLED", "pending_order_id": "pending-1"}},
    {"plan_state": {"status": "ACTIVE"}},
  ],
)
async def test_delete_rejects_unfinished_or_inconsistent_plan(db, overrides):
  db.add(_record(**overrides))
  await db.commit()
  with pytest.raises(ValueError, match="仅可删除"):
    await AutoExitPlanRepository(db).delete_history(
      plan_id="plan-1", account_id="account-1"
    )
  assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "plan_id,account_id", [("missing", "account-1"), ("plan-1", "other")]
)
async def test_delete_rejects_missing_or_unauthorized_plan(db, plan_id, account_id):
  db.add(_record())
  await db.commit()
  with pytest.raises(ValueError, match="不存在或无权访问"):
    await AutoExitPlanRepository(db).delete_history(
      plan_id=plan_id, account_id=account_id
    )
  assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 0


@pytest.mark.asyncio
async def test_visibility_filter_precedes_limit_and_never_hides_active_plans(db):
  deleted = _record(updated_at=datetime(2026, 8, 31))
  remaining = _record("plan-2", updated_at=datetime(2026, 8, 30))
  db.add_all([deleted, remaining])
  await db.commit()
  repository = AutoExitPlanRepository(db)
  await repository.delete_history(plan_id="plan-1", account_id="account-1")
  rows = await repository.find_all(exclude_deleted_history=True, limit=1)
  assert [row.plan_id for row in rows] == ["plan-2"]
  deleted.status = "ACTIVE"
  await db.commit()
  rows = await repository.find_all(exclude_deleted_history=True)
  assert {row.plan_id for row in rows} == {"plan-1", "plan-2"}
