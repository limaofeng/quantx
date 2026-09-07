from __future__ import annotations

from datetime import datetime, timezone

import pytest
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanConcurrencyError,
  AutoExitPlanRepository,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _plan(*, plan_id: str = "plan-1", config_version: int = 1) -> ExitPlan:
  plan = ExitPlan(
    template=ExitPlanTemplate(
      plan_id=plan_id,
      source_type="T_TRADE_BATCH",
      source_id=f"batch-{plan_id}",
      account_id="account-1",
      instrument_code="600000.SH",
      bucket="swing",
      run_id="run-1",
      config_version=config_version,
      rules=[
        ExitRuleSpec(
          rule_id=f"{plan_id}:adaptive-volume-price",
          strategy=ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
        )
      ],
    )
  )
  plan.register_entry_fill(volume=100, price=10.0)
  return plan


def _record(plan: ExitPlan, *, strategy_run_id: str = "run-1") -> AutoExitPlanRecord:
  return AutoExitPlanRecord(
    plan_id=plan.plan_id,
    account_id=plan.template.account_id,
    instrument_code=plan.template.instrument_code,
    bucket=plan.template.bucket,
    source_type=plan.template.source_type,
    source_id=plan.template.source_id,
    strategy_run_id=strategy_run_id,
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id=strategy_run_id,
    source_execution_environment="PAPER",
    enabled=True,
    status=plan.status.value,
    environment="PAPER",
    auto_exit_authorized=False,
    config_version=plan.template.config_version,
    protected_volume=plan.entry_filled_volume,
    exited_volume=plan.exited_volume,
    remaining_volume=plan.remaining_volume,
    entry_avg_price=plan.entry_avg_price,
    plan_state=plan.to_dict(),
  )


async def _sessions():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(AutoExitPlanRecord.__table__.create)
  return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_compare_and_swap_records_utc_availability_from_shanghai_clock(
  monkeypatch,
):
  monkeypatch.setattr(time_utils, "now", lambda: datetime(2026, 9, 3, 10, 30))
  engine, sessions = await _sessions()
  try:
    plan = _plan()
    async with sessions() as db:
      row = _record(plan)
      row.created_at = row.updated_at = datetime(2026, 9, 3, 1)
      db.add(row)
      await db.commit()
      plan.peak_price = 11
      await AutoExitPlanRepository(db).compare_and_swap_state(
        plan_id=plan.plan_id, expected_state_version=1, plan_state=plan.to_dict()
      )
    async with sessions() as db:
      row = await db.get(AutoExitPlanRecord, plan.plan_id)
      assert row.updated_at == datetime(2026, 9, 3, 2, 30)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_loads_authoritative_states_for_strategy_run() -> None:
  engine, sessions = await _sessions()
  try:
    async with sessions() as db:
      db.add_all(
        [
          _record(_plan(plan_id="plan-b")),
          _record(_plan(plan_id="plan-a")),
          _record(_plan(plan_id="plan-other"), strategy_run_id="run-2"),
        ]
      )
      await db.commit()

      rows = await AutoExitPlanRepository(db).find_for_strategy_run("run-1")
      assert [row.plan_id for row in rows] == ["plan-a", "plan-b"]
      assert [ExitPlan.from_dict(row.plan_state).plan_id for row in rows] == [
        "plan-a",
        "plan-b",
      ]
      assert all(row.state_version == 1 for row in rows)
      assert (
        await AutoExitPlanRepository(db).find_for_strategy_run("run-1", statuses=[])
        == []
      )
      with pytest.raises(ValueError, match="策略运行标识"):
        await AutoExitPlanRepository(db).find_for_strategy_run("")
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_and_swap_advances_only_state_version_and_projections() -> None:
  engine, sessions = await _sessions()
  try:
    async with sessions() as db:
      original = _plan()
      db.add(_record(original))
      await db.commit()

      changed = ExitPlan.from_dict(original.to_dict())
      changed.peak_price = 10.8
      changed.trailing_floor_pct = 1.25
      changed.pending_order_id = "exit-order-1"
      changed.last_evaluated_at = "2026-08-29T10:01:02+08:00"
      changed.rule_state[f"{changed.plan_id}:adaptive-volume-price"] = {
        "phase": "TRAILING",
        "data_quality": "READY",
        "last_decision": "HOLD",
        "peak_drawdown_pct": 0.7,
        "volume_velocity": -0.25,
        "weak_score": 3,
      }

      updated = await AutoExitPlanRepository(db).compare_and_swap_state(
        plan_id=changed.plan_id,
        expected_state_version=1,
        plan_state=changed.to_dict(),
      )

      assert updated.state_version == 2
      assert updated.config_version == 1
      assert updated.peak_price == pytest.approx(10.8)
      assert updated.trailing_floor_pct == pytest.approx(1.25)
      assert updated.pending_client_order_id == "exit-order-1"
      assert updated.phase == "TRAILING"
      assert updated.data_quality == "READY"
      assert updated.last_decision == "HOLD"
      assert updated.peak_drawdown_pct == pytest.approx(0.7)
      assert updated.volume_velocity == pytest.approx(-0.25)
      assert updated.weak_score == 3
      assert updated.last_evaluated_at == datetime(2026, 8, 29, 10, 1, 2)
      assert ExitPlan.from_dict(updated.plan_state).peak_price == pytest.approx(10.8)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_and_swap_is_idempotent_and_rejects_stale_state() -> None:
  engine, sessions = await _sessions()
  try:
    async with sessions() as db:
      original = _plan()
      db.add(_record(original))
      await db.commit()
      first = ExitPlan.from_dict(original.to_dict())
      first.peak_price = 10.5
      repository = AutoExitPlanRepository(db)
      updated = await repository.compare_and_swap_state(
        plan_id=first.plan_id,
        expected_state_version=1,
        plan_state=first.to_dict(),
      )
      repeated = await repository.compare_and_swap_state(
        plan_id=first.plan_id,
        expected_state_version=1,
        plan_state=first.to_dict(),
      )
      assert repeated.state_version == updated.state_version == 2

      stale = ExitPlan.from_dict(original.to_dict())
      stale.peak_price = 11.0
      with pytest.raises(AutoExitPlanConcurrencyError, match="重新装载"):
        await repository.compare_and_swap_state(
          plan_id=stale.plan_id,
          expected_state_version=1,
          plan_state=stale.to_dict(),
        )
      current = await repository.find_by_id(stale.plan_id)
      assert current is not None
      assert current.state_version == 2
      assert current.config_version == 1
      assert ExitPlan.from_dict(current.plan_state).peak_price == pytest.approx(10.5)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_and_swap_rejects_stale_configuration_without_mutating_it() -> (
  None
):
  engine, sessions = await _sessions()
  try:
    async with sessions() as db:
      db.add(_record(_plan(config_version=1)))
      await db.commit()
      incompatible = _plan(config_version=2)
      with pytest.raises(AutoExitPlanConcurrencyError, match="配置版本冲突"):
        await AutoExitPlanRepository(db).compare_and_swap_state(
          plan_id=incompatible.plan_id,
          expected_state_version=1,
          plan_state=incompatible.to_dict(),
        )
      current = await AutoExitPlanRepository(db).find_by_id(incompatible.plan_id)
      assert current is not None
      assert current.config_version == 1
      assert current.state_version == 1
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_compare_and_swap_accepts_explicit_aware_evaluation_time() -> None:
  engine, sessions = await _sessions()
  try:
    async with sessions() as db:
      plan = _plan()
      db.add(_record(plan))
      await db.commit()
      updated = await AutoExitPlanRepository(db).compare_and_swap_state(
        plan_id=plan.plan_id,
        expected_state_version=1,
        plan_state=plan.to_dict(),
        evaluated_at=datetime(2026, 8, 29, 9, 30, tzinfo=timezone.utc),
      )
      assert updated.last_evaluated_at == datetime(2026, 8, 29, 17, 30)
  finally:
    await engine.dispose()
