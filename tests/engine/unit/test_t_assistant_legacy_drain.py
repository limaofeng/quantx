from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

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
  legacy_t_config_is_draining,
  legacy_t_entry_is_draining,
)
from sqlalchemy import ARRAY, JSON, MetaData, insert

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _database,
  _seed_managed_order,
  _snapshot_report,
)


async def seed_legacy_drain(monkeypatch, damage=None):
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
  return engine, sessions, now, digest


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["bound", "unbound", "corrupt"])
async def test_durable_drain_prevents_monitor_recreation_without_sessions(
  monkeypatch, state
):
  from quantx_engine import t_trade_global_monitor as module

  engine, sessions, now, digest = await seed_legacy_drain(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      await begin_legacy_t_drain(
        db,
        config_id="head",
        run_id="plan-1",
        expected_head_version=1,
        inventory_operation_id="inventory",
        expected_inventory_hash=digest,
        actor_id="user-1",
        now=now,
      )
      head = await db.get(TTradeGlobalConfig, "head")
      if state == "unbound":
        head.strategy_run_id = None
      if state == "corrupt":
        marker = await db.get(TTradeRolloutEvent, "legacy-t-drain:plan-1")
        marker.details = {"run_id": "plan-1", "request": {}}

    async def database():
      async with sessions() as db:
        yield db

    monkeypatch.setattr(module, "get_async_db", database)
    service = module.TTradeGlobalMonitorService()
    service._load_config = AsyncMock(return_value=head)
    service.position_service.read_validated_snapshot_and_positions = AsyncMock(
      return_value=({}, [])
    )
    service.session_service.list_active_account_run_ids = AsyncMock(return_value=[])
    service.session_service.get_run_sessions = AsyncMock(return_value=[])
    service.session_service.block_account_strategy_entries = AsyncMock()
    service.session_service.start_account_strategy = AsyncMock()
    service.session_service.stop_account_strategy = AsyncMock()
    service._record_reconcile_result = AsyncMock()
    service.get_monitor = AsyncMock(return_value={})
    await service.reconcile_account("account-1")
    service.session_service.start_account_strategy.assert_not_awaited()
    service.session_service.stop_account_strategy.assert_not_awaited()
    service.session_service.get_run_sessions.assert_not_awaited()
    assert head.strategy_run_id == (None if state == "unbound" else "plan-1")
    assert any(
      "LEGACY_T_ENTRY_DRAINING" in error
      for error in service._record_reconcile_result.await_args.args[1]
    )
    if state != "unbound":
      service.session_service.block_account_strategy_entries.assert_awaited_once_with(
        "plan-1", reason="LEGACY_T_ENTRY_DRAINING"
      )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_strategy", [False, True])
async def test_replacement_identity_cannot_bypass_account_legacy_fence(
  monkeypatch, other_strategy
):
  engine, sessions, now, digest = await seed_legacy_drain(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      await begin_legacy_t_drain(
        db,
        config_id="head",
        run_id="plan-1",
        expected_head_version=1,
        inventory_operation_id="inventory",
        expected_inventory_hash=digest,
        actor_id="user-1",
        now=now,
      )
      # Simulate a racing recreation with a different durable run identity.
      metadata = MetaData()
      table = StrategyRun.__table__.to_metadata(metadata)
      for column in table.columns:
        if isinstance(column.type, ARRAY):
          column.type = JSON()
      await db.execute(
        insert(table).values(
          id="replacement",
          name="replacement",
          strategy_id=1,
          parameters={"account_id": "account-1"},
          mode=StrategyRunMode.LIVE,
          status=StrategyRunStatus.RUNNING,
        )
      )
      if other_strategy:
        (await db.get(Strategy, 1)).class_name = "UnrelatedStrategy"
        await db.flush()
      assert await legacy_t_config_is_draining(
        db, account_id="account-1", config_id="head"
      )
      assert await legacy_t_entry_is_draining(
        db, account_id="account-1", run_id="replacement", lock_head=True
      ) is (not other_strategy)
      if not other_strategy:
        from decimal import Decimal

        from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
        from quantx_infrastructure.services.trade_command_service import (
          AgentUnavailableError,
          TradeCommandService,
        )

        service = TradeCommandService(db)
        service._require_live_authorization = AsyncMock(
          side_effect=AssertionError("must block before device authorization")
        )
        with pytest.raises(AgentUnavailableError, match="LEGACY_T_ENTRY_DRAINING"):
          await service.enqueue_order_for_account(
            account_id="account-1",
            instrument_code="600000.SH",
            side="BUY",
            order_type="FIX_PRICE",
            limit_price=Decimal("10"),
            volume=100,
            execution_ref=ExecutionOwnerRef("STRATEGY_RUN", "replacement"),
            environment=ExecutionEnvironment.LIVE,
            idempotency_key="replacement-buy",
            strategy_run_id="replacement",
            strategy_order_id="new-order",
            intent_id="new-intent",
            batch_id="new-batch",
            t_trade_role="",
          )
        service._require_live_authorization.assert_not_awaited()
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage", [None, "class", "hash", "changed", "audit", "stopped"]
)
async def test_atomic_drain_uses_reviewed_inventory_and_retains_order_owner(
  monkeypatch, damage
):
  engine, sessions, now, digest = await seed_legacy_drain(monkeypatch, damage)
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
