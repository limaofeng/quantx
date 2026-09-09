"""Independent LIVE exit inputs use persisted source scope and account inventory."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from quantx_engine import exit_plan_runtime as runtime_module
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)

from tests.engine.unit.test_t_assistant_live_admission import NOW, prepare, seed
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _sessions,
)

sessions = _sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "fault", [None, "stale", "account", "missing_source", "instrument"]
)
async def test_live_exit_reads_original_plan_after_source_stop(
  sessions, monkeypatch, fault
):
  source = await seed(sessions)
  async with sessions() as db, db.begin():
    execution_id = await prepare(db, source)
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          AutoExitPlanRecord.__table__,
          Position.__table__,
          AccountExecutionControl.__table__,
        ],
      )
    )
  async with sessions() as db, db.begin():
    repository = TAssistantExecutionRepository(db)
    current = await repository.get_domain(execution_id)
    for status in ("DRAINING", "STOPPED"):
      changed = current.transition(
        status, at=NOW + timedelta(seconds=2), has_unsettled_buy_work=False
      )
      await repository.save_transition_with_event(
        changed,
        expected_state_version=current.state_version,
        event=TAssistantExecutionEvent(
          execution_id, status, status, NOW + timedelta(seconds=2), {}
        ),
      )
      current = changed
    execution = await db.get(TAssistantExecutionRecord, execution_id)
    db.add(AccountExecutionControl(account_id="account-1"))
    db.add(
      Position(
        id="position",
        account_id="account-1",
        stock_code="600000.SH",
        volume=100,
        can_use_volume=0,
        frozen_volume=0,
        market_value=1000,
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id="carried-plan",
        account_id="account-1",
        instrument_code="600000.SH",
        bucket="swing",
        source_type="T_TRADE_BATCH",
        source_id="original-batch",
        source_execution_owner_type="T_ASSISTANT_EXECUTION",
        source_execution_owner_id=execution_id,
        source_execution_environment="LIVE",
        environment="LIVE",
        status="ACTIVE",
        enabled=True,
        protected_volume=100,
        remaining_volume=100,
        entry_avg_price=10,
        plan_state={
          "template": {
            "plan_id": "carried-plan",
            "account_id": "account-1",
            "instrument_code": "600000.SH",
            "source_type": "T_TRADE_BATCH",
            "source_id": "original-batch",
            "run_id": "",
            "metadata": {
              "source_execution_owner_type": "T_ASSISTANT_EXECUTION",
              "source_execution_owner_id": execution_id,
              "source_execution_environment": "LIVE",
            },
          }
        },
      )
    )
    if fault == "account":
      execution.account_id = "other-account"
    elif fault == "missing_source":
      await db.delete(execution)
  clock = SimpleNamespace(now=NOW.replace(tzinfo=None) + timedelta(hours=8))
  monkeypatch.setattr(runtime_module, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(runtime_module.time_utils, "now", lambda: clock.now)
  runtime = runtime_module.ExitPlanRuntime(
    scanner=SimpleNamespace(hub=SimpleNamespace(is_ready=fault != "stale"))
  )
  state = SimpleNamespace(
    updated_at=clock.now,
    current_price=9,
    bid_price=[8.99],
    ask_price=[9],
    bid_vol=[1000],
    ask_vol=[1000],
    up_stop_price=11,
    down_stop_price=9,
    price_tick=0.01,
    volume=10000,
    amount=90000,
  )
  for next_day in (False, True):
    if next_day:
      clock.now += timedelta(days=1)
      state.updated_at = clock.now
      async with sessions() as db, db.begin():
        (await db.get(Position, "position")).can_use_volume = 100
    async with sessions() as db:
      record = await db.get(AutoExitPlanRecord, "carried-plan")
    if fault == "instrument":
      async with sessions() as db, db.begin():
        changed = await db.get(AutoExitPlanRecord, "carried-plan")
        changed.instrument_code = "600000.SH" if next_day else "600001.SH"
        changed.plan_state = {
          "template": {
            **changed.plan_state["template"],
            "instrument_code": changed.instrument_code,
          },
        }
      with pytest.raises(ValueError, match="EXIT_PLAN_OWNER_CHANGED"):
        await runtime._evaluation_inputs(record, states={"600000.SH": state})
      continue
    if fault in {"account", "missing_source"}:
      with pytest.raises(ValueError, match="source/account scope conflict"):
        await runtime._evaluation_inputs(record, states={"600000.SH": state})
      continue
    position, context = await runtime._evaluation_inputs(
      record, states={"600000.SH": state}
    )
    assert position.can_use_volume == (100 if next_day else 0)
    assert context.source == (
      "WHOLE_QUOTE_UNAVAILABLE" if fault == "stale" else "QMT_WHOLE_QUOTE"
    )
    async with sessions() as db:
      persisted = await db.get(AutoExitPlanRecord, "carried-plan")
      assert persisted.source_execution_owner_id == execution_id
      assert (
        persisted.source_id == "original-batch" and persisted.remaining_volume == 100
      )
      assert persisted.strategy_run_id is None and persisted.state_version == 1
