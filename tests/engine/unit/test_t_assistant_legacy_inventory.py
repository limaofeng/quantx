from datetime import UTC, datetime, timedelta

import pytest
from quantx_engine.t_assistant_legacy_inventory import (
  freeze_legacy_t_obligation_inventory,
)
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import func, select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _database,
  _seed_managed_order,
  _snapshot_report,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "head", "fact", "replay", "orphan", "plan"])
async def test_freeze_preserves_legacy_ownership_and_rejects_changed_evidence(
  monkeypatch, damage
):
  engine, sessions = await _database(monkeypatch)
  async with engine.begin() as connection:
    await connection.run_sync(lambda sync: TTradeGlobalConfig.__table__.create(sync))
    await connection.run_sync(lambda sync: TTradeRolloutEvent.__table__.create(sync))
  snapshot = _snapshot_report(terminal_status="CANCELLED", snapshot_id="inventory")
  await _seed_managed_order(sessions, terminal_status="CANCELLED", snapshot=snapshot)
  now = datetime.now(UTC) + timedelta(seconds=1)
  async with sessions() as db, db.begin():
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
        strategy_run_id="plan-1",
        environment="LIVE",
        account_id="account-1",
        instrument_code="605499.SH",
        direction="BUY",
        bucket="core",
        status="AWAITING_APPROVAL",
        target_volume=100,
        intent_metadata={"t_trade_role": "entry"},
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id="legacy-exit",
        account_id="account-1",
        instrument_code="605499.SH",
        source_type="T_TRADE_BATCH",
        source_id="legacy-batch",
        environment="LIVE",
        source_execution_owner_type="STRATEGY_RUN",
        source_execution_owner_id="plan-1",
        source_execution_environment="LIVE",
        strategy_run_id="plan-1",
        protected_volume=100,
        remaining_volume=100,
        entry_avg_price=10,
        plan_state={"source_id": "legacy-batch", "remaining_volume": 100},
      )
    )
    if damage == "head":
      (await db.get(TTradeGlobalConfig, "head")).strategy_run_id = "other"
    elif damage == "fact":
      (await db.get(PendingTradeOrder, "client-1")).updated_at = now + timedelta(
        seconds=10
      )
    elif damage == "orphan":
      db.add(
        TradeCommandOutbox(
          message_id="orphan",
          client_order_id="orphan-client",
          idempotency_key="orphan",
          device_id="device-1",
          account_id="account-1",
          owner_type="STRATEGY_RUN",
          owner_id="plan-1",
          environment="LIVE",
          payload={"command_kind": "place_order"},
          delivery_status="UNKNOWN",
          expires_at=now + timedelta(seconds=30),
        )
      )

  async def freeze(db):
    return await freeze_legacy_t_obligation_inventory(
      db,
      config_id="head",
      run_id="plan-1",
      expected_head_version=1,
      operation_id="inventory-1",
      actor_id="user-1",
      now=now,
    )

  try:
    if damage in {"head", "fact"}:
      async with sessions() as db, db.begin():
        with pytest.raises(ValueError, match="LEGACY_T_INVENTORY_"):
          await freeze(db)
      async with sessions() as db:
        assert (
          await db.scalar(select(func.count()).select_from(TTradeRolloutEvent)) == 0
        )
      return
    async with sessions() as db, db.begin():
      digest = await freeze(db)
    if damage in {"replay", "plan"}:
      async with sessions() as db, db.begin():
        if damage == "plan":
          (await db.get(AutoExitPlanRecord, "legacy-exit")).remaining_volume = 99
        else:
          (await db.get(PendingTradeOrder, "client-1")).status = "UNKNOWN"
      async with sessions() as db, db.begin():
        with pytest.raises(ValueError, match="LEGACY_T_INVENTORY_CHANGED"):
          await freeze(db)
    else:
      async with sessions() as db, db.begin():
        assert await freeze(db) == digest
    async with sessions() as db:
      audit = await db.get(TTradeRolloutEvent, "inventory-1")
      assert audit.details["manifest_hash"] == digest
      manifest = audit.details["manifest"]
      plan = await db.get(AutoExitPlanRecord, "legacy-exit")
      assert (
        plan.source_execution_owner_id == "plan-1" and plan.source_id == "legacy-batch"
      )
      assert plan.protected_volume == 100
      assert plan.remaining_volume == (99 if damage == "plan" else 100)
      assert manifest["exit_plans"][0]["plan_id"] == "legacy-exit"
      assert manifest["retained_client_order_ids"] == (
        ["client-1", "orphan-client"] if damage == "orphan" else ["client-1"]
      )
      assert manifest["unsubmitted_intent_ids_for_review"] == (
        [] if damage == "orphan" else ["unsubmitted"]
      )
      assert (await db.get(TradeIntentRecord, "intent-1")).status == "CANCELLED"
      assert (await db.get(PendingTradeOrder, "client-1")).owner_id == "plan-1"
      assert (await db.get(TTradeGlobalConfig, "head")).strategy_run_id == "plan-1"
      assert await db.scalar(select(func.count()).select_from(TTradeRolloutEvent)) == 1
  finally:
    await engine.dispose()
