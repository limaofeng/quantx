from datetime import UTC, datetime, timedelta

import pytest
from quantx_engine.t_assistant_legacy_drain import begin_legacy_t_drain
from quantx_engine.t_assistant_legacy_inventory import (
  freeze_legacy_t_obligation_inventory,
)
from quantx_infrastructure.core.assistant_strategy_policy import (
  T_TRADE_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.t_legacy_drain_guard import (
  legacy_t_entry_is_draining,
)
from sqlalchemy import ARRAY, JSON, MetaData, insert

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _database,
  _seed_managed_order,
  _snapshot_report,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage", [None, "class", "hash", "changed", "audit", "stopped"]
)
async def test_atomic_drain_uses_reviewed_inventory_and_retains_order_owner(
  monkeypatch, damage
):
  engine, sessions = await _database(monkeypatch)
  # Portable SQLite copies for the two generic Strategy tables' ARRAY fields.
  # Production models and their PostgreSQL mappings are not modified.
  metadata = MetaData()
  strategy_table = Strategy.__table__.to_metadata(metadata)
  run_table = StrategyRun.__table__.to_metadata(metadata)
  for table in (strategy_table, run_table):
    for column in table.columns:
      if isinstance(column.type, ARRAY):
        column.type = JSON()
  async with engine.begin() as connection:
    await connection.run_sync(metadata.create_all)
    await connection.run_sync(lambda sync: TTradeGlobalConfig.__table__.create(sync))
    await connection.run_sync(lambda sync: TTradeRolloutEvent.__table__.create(sync))
  snapshot = _snapshot_report(terminal_status="CANCELLED", snapshot_id="drain")
  await _seed_managed_order(sessions, terminal_status="CANCELLED", snapshot=snapshot)
  now = datetime.now(UTC) + timedelta(seconds=10)
  async with sessions() as db, db.begin():
    await db.execute(
      insert(strategy_table).values(
        id=1,
        name="legacy",
        file_path="fixture",
        class_name="OtherStrategy"
        if damage == "class"
        else T_TRADE_STRATEGY_CLASS_NAME,
      )
    )
    await db.execute(
      insert(run_table).values(
        id="plan-1",
        name="legacy",
        strategy_id=1,
        parameters={"account_id": "account-1"},
        mode=StrategyRunMode.LIVE,
        status=StrategyRunStatus.STOPPED
        if damage == "stopped"
        else StrategyRunStatus.RUNNING,
      )
    )
    db.add(
      TTradeGlobalConfig(
        id="head",
        account_id="account-1",
        mode="live",
        strategy_run_id="plan-1",
        state_version=1,
      )
    )
    db.add(
      TradeIntentRecord(
        id="unsubmitted",
        idempotency_key="unsubmitted",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        strategy_run_id="plan-1",
        account_id="account-1",
        instrument_code="605499.SH",
        direction="BUY",
        bucket="core",
        status="AWAITING_APPROVAL",
        target_volume=100,
      )
    )
    await db.flush()
    digest = await freeze_legacy_t_obligation_inventory(
      db,
      config_id="head",
      run_id="plan-1",
      expected_head_version=1,
      operation_id="inventory",
      actor_id="user-1",
      now=now,
    )
  if damage == "changed":
    async with sessions() as db, db.begin():
      (await db.get(PendingTradeOrder, "client-1")).status = "UNKNOWN"

  async def drain(db):
    return await begin_legacy_t_drain(
      db,
      config_id="head",
      run_id="plan-1",
      expected_head_version=1,
      inventory_operation_id="inventory",
      expected_inventory_hash="0" * 64 if damage == "hash" else digest,
      actor_id="user-1",
      now=now,
    )

  try:
    async with sessions() as db, db.begin():
      if damage == "audit":
        original = db.flush

        async def fail(*args, **kwargs):
          if any(
            isinstance(row, TTradeRolloutEvent) and row.next_stage == "DRAINING"
            for row in db.new
          ):
            raise RuntimeError("synthetic audit failure")
          return await original(*args, **kwargs)

        monkeypatch.setattr(db, "flush", fail)
      if damage:
        with pytest.raises((ValueError, RuntimeError)):
          await drain(db)
      else:
        result = await drain(db)
        assert result["cancelled_intent_ids"] == ["unsubmitted"]
        assert result["retained_client_order_ids"] == ["client-1"]
    async with sessions() as db, db.begin():
      assert await legacy_t_entry_is_draining(
        db, account_id="account-1", run_id="plan-1"
      ) is (damage is None)
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.strategy_run_id == "plan-1" and head.state_version == (
        2 if damage is None else 1
      )
      assert (await db.get(TradeIntentRecord, "unsubmitted")).status == (
        "CANCELLED" if damage is None else "AWAITING_APPROVAL"
      )
      assert (await db.get(PendingTradeOrder, "client-1")).owner_id == "plan-1"
      if damage is None:
        assert await drain(db) == result
        assert (await db.get(StrategyRun, "plan-1")).status == StrategyRunStatus.RUNNING
  finally:
    await engine.dispose()
