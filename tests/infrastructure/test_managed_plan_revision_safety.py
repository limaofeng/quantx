from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.ashare_managed_entry_plan import (
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import StrategyRunMode
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.managed_plan import (
  ManagedPlanConfigRevision,
  ManagedPlanRecord,
)
from quantx_infrastructure.repositories.managed_plan_repository import (
  ManagedPlanRepository,
)
from quantx_infrastructure.services.managed_plan_runtime_service import (
  ManagedPlanRuntimeService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def plans():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db, tables=[ManagedPlanRecord.__table__, ManagedPlanConfigRevision.__table__]
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    repo = ManagedPlanRepository(db)
    await repo.create_plan(
      plan_id="plan",
      plan_kind="ENTRY",
      account_id="account",
      instrument_code="600000.SH",
      config_snapshot={"price": 10},
      state_migration_policy="RESET",
    )
    await repo.bind_run(
      plan_id="plan", config_version=1, run_id="old", status="RUNNING"
    )
    await db.commit()
  yield sessions
  await engine.dispose()


def revision_request(command_id="revise-1"):
  return dict(
    plan_id="plan",
    expected_version=1,
    config_snapshot={"price": 9},
    parameters={"account_id": "account"},
    strategy_id=1,
    strategy_class=AshareManagedEntryPlanStrategy,
    mode=StrategyRunMode.LIVE,
    name="entry",
    start_immediately=False,
    state_migration_policy="RESET",
    initial_state=None,
    command_id=command_id,
  )


@pytest.mark.asyncio
async def test_refused_stop_preserves_running_plan_and_revision(plans):
  manager = SimpleNamespace(
    get_run=lambda _: object(), stop_strategy=AsyncMock(return_value=False)
  )
  service = ManagedPlanRuntimeService(manager, session_factory=plans)
  service._create_and_bind_run = AsyncMock()
  with pytest.raises(RuntimeError, match="未能确认停止"):
    await service.revise(**revision_request())
  async with plans() as db:
    plan = await ManagedPlanRepository(db).find("plan")
    assert (plan.current_config_version, plan.current_run_id, plan.status) == (
      1,
      "old",
      "RUNNING",
    )
    assert (
      await db.scalar(select(func.count()).select_from(ManagedPlanConfigRevision)) == 1
    )
    assert plan.last_error is None
  service._create_and_bind_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_creation_keeps_binding_and_same_command_can_retry(plans):
  manager = SimpleNamespace(
    get_run=lambda _: object(), stop_strategy=AsyncMock(return_value=True)
  )
  service = ManagedPlanRuntimeService(manager, session_factory=plans)
  service._create_and_bind_run = AsyncMock(side_effect=RuntimeError("creation failed"))
  with pytest.raises(RuntimeError, match="creation failed"):
    await service.revise(**revision_request())
  async with plans() as db:
    repo = ManagedPlanRepository(db)
    plan = await repo.find("plan")
    prepared = await repo.find_revision("plan", 2)
    assert (plan.current_config_version, plan.current_run_id, plan.status) == (
      1,
      "old",
      "PAUSED",
    )
    assert (await repo.current_revision("plan")).run_id == "old"
    prepared_run_id = prepared.run_id

  async def bind(**request):
    async with plans() as db:
      repo = ManagedPlanRepository(db)
      before = await repo.find("plan")
      assert (before.current_config_version, before.current_run_id) == (1, "old")
      await repo.bind_run(
        plan_id="plan",
        config_version=2,
        run_id=request["run_id"],
        status="PAUSED",
        command_id=request["command_id"],
      )
      await db.commit()

  service._create_and_bind_run = bind
  assert await service.revise(**revision_request()) == (prepared_run_id, 2, "old")
  async with plans() as db:
    plan = await ManagedPlanRepository(db).find("plan")
    assert (plan.current_config_version, plan.current_run_id) == (2, prepared_run_id)
    assert plan.last_error is None
    assert plan.last_command_id == "revise-1"
    assert (
      await db.scalar(select(func.count()).select_from(ManagedPlanConfigRevision)) == 2
    )


@pytest.mark.asyncio
async def test_prepared_revision_cannot_be_replaced_by_another_command(plans):
  manager = SimpleNamespace(
    get_run=lambda _: object(), stop_strategy=AsyncMock(return_value=True)
  )
  service = ManagedPlanRuntimeService(manager, session_factory=plans)
  service._create_and_bind_run = AsyncMock(side_effect=RuntimeError("creation failed"))
  with pytest.raises(RuntimeError):
    await service.revise(**revision_request())
  with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
    await service.revise(**revision_request("different-command"))
  async with plans() as db:
    plan = await ManagedPlanRepository(db).find("plan")
    assert (plan.current_config_version, plan.current_run_id) == (1, "old")
  assert service._create_and_bind_run.await_count == 1
