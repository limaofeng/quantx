"""Atomic completion with SQLite and synthetic broker/account evidence."""

import json
from datetime import timedelta
from hashlib import sha256

import pytest
from quantx_engine.report_processor import _event_payload
from quantx_engine.t_assistant_legacy_completion import complete_legacy_t_drain
from quantx_engine.t_assistant_legacy_drain import begin_legacy_t_drain
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import (
  OrderPriceType,
  OrderStatus,
  OrderType,
  StrategyRunStatus,
)
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _snapshot_report,
)
from tests.engine.unit.test_t_assistant_legacy_drain import seed_legacy_drain


async def seed_completion(monkeypatch):
  engine, sessions, drained_at, digest = await seed_legacy_drain(monkeypatch)
  async with engine.begin() as connection:
    await connection.run_sync(lambda sync: AgentReportInbox.__table__.create(sync))
  now = drained_at + timedelta(seconds=5)
  stamp = (now - timedelta(seconds=1)).replace(tzinfo=None)
  async with sessions() as db, db.begin():
    await begin_legacy_t_drain(
      db,
      config_id="head",
      run_id="plan-1",
      expected_head_version=1,
      inventory_operation_id="inventory",
      expected_inventory_hash=digest,
      actor_id="user-1",
      now=drained_at,
    )
    snapshot = _snapshot_report(terminal_status="CANCELLED", snapshot_id="completion")
    snapshot.payload["source_event_at"] = (now - timedelta(seconds=1)).isoformat()
    snapshot.payload["orders"][0]["traded_volume"] = 40
    snapshot.payload["trades"] = [
      dict(
        account_id="account-1",
        order_id=9001,
        stock_code="605499.SH",
        order_type=23,
        traded_id="trade-1",
        traded_volume=40,
        traded_price=10,
      )
    ]
    snapshot.payload["snapshot_hash"] = sha256(
      json.dumps(
        {
          key: value
          for key, value in snapshot.payload.items()
          if key != "snapshot_hash"
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
      ).encode()
    ).hexdigest()
    snapshot.received_at = stamp
    snapshot.processed_at = stamp
    snapshot.processing_status = "PROCESSED"
    db.add(snapshot)
    control = await db.get(AccountExecutionControl, "account-1")
    control.last_snapshot_id = snapshot.payload["snapshot_id"]
    control.last_snapshot_hash = snapshot.payload["snapshot_hash"]
    control.last_snapshot_at = stamp
    intent = await db.get(TradeIntentRecord, "intent-1")
    intent.executed_volume = 40
    intent.executed_price = 10
    intent.executed_time = stamp
    db.add(
      Order(
        id=9001,
        account_id="account-1",
        stock_code="605499.SH",
        sysid="broker-sys",
        time=stamp,
        type=OrderType.BUY,
        volume=100,
        price_type=OrderPriceType.LIMIT,
        price=10,
        traded_volume=40,
        traded_price=10,
        status=OrderStatus.PART_CANCEL,
      )
    )
    db.add(
      Trade(
        id="trade-1",
        account_id="account-1",
        stock_code="605499.SH",
        order_id=9001,
        order_sysid="broker-sys",
        order_type=23,
        time=stamp,
        price=10,
        volume=40,
        amount=400,
      )
    )
    correlation = await db.get(OrderCorrelation, "correlation-1")
    for kind, report in (
      ("ORDER", dict(order_status="CANCELLED", traded_volume=40)),
      ("TRADE", dict(execution_id="trade-1", traded_volume=40)),
    ):
      report.update(
        account_id="account-1", order_id=9001, stock_code="605499.SH", order_type=23
      )
      db.add(
        StrategyRuntimeEvent(
          event_id=kind,
          business_key=kind,
          owner_type="STRATEGY_RUN",
          owner_id="plan-1",
          environment="LIVE",
          strategy_run_id="plan-1",
          client_order_id="client-1",
          broker_order_id="9001",
          event_type=kind,
          payload=_event_payload(correlation, report, business_key=kind),
          application_status="APPLIED",
          created_at=stamp,
          applied_at=stamp,
        )
      )
    db.add(
      TradeCommandOutbox(
        message_id="place",
        client_order_id="client-1",
        idempotency_key="place",
        device_id="device-1",
        account_id="account-1",
        owner_type="STRATEGY_RUN",
        owner_id="plan-1",
        environment="LIVE",
        payload=dict(
          command_kind="PLACE_ORDER",
          execution_mode="live",
          client_order_id="client-1",
          account_id="account-1",
          instrument_code="605499.SH",
          side="BUY",
          volume=100,
        ),
        delivery_status="ACKNOWLEDGED",
        expires_at=stamp,
      )
    )
    db.add(
      TTradeBatch(
        batch_id="batch-1",
        account_id="account-1",
        instrument_code="605499.SH",
        environment="LIVE",
        source_execution_owner_type="STRATEGY_RUN",
        source_execution_owner_id="plan-1",
        source_execution_environment="LIVE",
        entry_intent_id="intent-1",
        entry_filled_volume=40,
      )
    )
    db.add(
      AutoExitPlanRecord(
        plan_id="exit-1",
        account_id="account-1",
        instrument_code="605499.SH",
        environment="LIVE",
        source_type="T_TRADE_BATCH",
        source_id="batch-1",
        strategy_run_id="plan-1",
        source_execution_owner_type="STRATEGY_RUN",
        source_execution_owner_id="plan-1",
        source_execution_environment="LIVE",
        protected_volume=40,
        remaining_volume=40,
        entry_avg_price=10,
        status="ACTIVE",
      )
    )
  return engine, sessions, now


async def complete(db, now, **overrides):
  return await complete_legacy_t_drain(
    db,
    **{
      "config_id": "head",
      "run_id": "plan-1",
      "expected_head_version": 2,
      "actor_id": "user-1",
      "now": now,
      **overrides,
    },
  )


@pytest.mark.asyncio
async def test_completion_unbinds_once_and_preserves_active_exit_owner(monkeypatch):
  engine, sessions, now = await seed_completion(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      result = await complete(db, now)
      assert result["evidence"]["retained_exit_plan_ids"] == ["exit-1"]
      assert result["evidence"]["settlements"][0]["filled_volume"] == 40
    async with sessions() as db, db.begin():
      # Replay is the committed cut, including after its snapshot has aged.
      assert await complete(db, now + timedelta(days=1)) == result
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.strategy_run_id is None and head.state_version == 3
      assert (await db.get(StrategyRun, "plan-1")).status == StrategyRunStatus.STOPPED
      plan = await db.get(AutoExitPlanRecord, "exit-1")
      assert (
        plan.source_execution_owner_type,
        plan.source_execution_owner_id,
        plan.source_id,
      ) == ("STRATEGY_RUN", "plan-1", "batch-1")
      assert plan.status == "ACTIVE" and plan.remaining_volume == 40
      assert (await db.get(PendingTradeOrder, "client-1")).owner_id == "plan-1"
      assert (await db.get(TradeIntentRecord, "intent-1")).executed_volume == 40
      with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        await complete(db, now, actor_id="other")
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage,error",
  [
    ("stale", "ACCOUNT_PROOF_REQUIRED"),
    ("snapshot_hash", "ACCOUNT_PROOF_REQUIRED"),
    ("unprocessed_snapshot", "ACCOUNT_PROOF_REQUIRED"),
    ("inbox", "INBOX_BACKLOG"),
    ("missing_command", "COMMAND_BINDING_REQUIRED"),
    ("unknown_order", "ORDER_NOT_TERMINAL"),
    ("intent", "INTENT_UNSETTLED"),
    ("orphan", "ORPHAN_COMMAND"),
    ("audit", "audit failure"),
    ("head", "SOURCE_CONFLICT"),
    ("volume", "INTENT_UNSETTLED"),
    ("runtime_orphan", "ORPHAN_OBLIGATION"),
    ("parent_orphan", "PARENT_CONFLICT"),
    ("incomplete", "ACCOUNT_PROOF_REQUIRED"),
  ],
)
async def test_incomplete_proof_or_audit_failure_preserves_source(
  monkeypatch, damage, error
):
  engine, sessions, now = await seed_completion(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      if damage == "stale":
        now += timedelta(seconds=90)
      elif damage == "snapshot_hash":
        (await db.get(AccountExecutionControl, "account-1")).last_snapshot_hash = (
          "0" * 64
        )
      elif damage == "unprocessed_snapshot":
        (await db.scalar(select(AgentReportInbox))).processing_status = "PROCESSING"
      elif damage == "inbox":
        db.add(
          AgentReportInbox(
            message_id="late",
            device_id="device-1",
            message_type="execution_report",
            raw_payload_hash="a" * 64,
            business_idempotency_key="late",
            payload={},
            received_at=now.replace(tzinfo=None),
            processing_status="PENDING",
          )
        )
      elif damage == "missing_command":
        await db.delete(await db.get(TradeCommandOutbox, "place"))
      elif damage == "unknown_order":
        (await db.get(PendingTradeOrder, "client-1")).status = "UNKNOWN"
      elif damage == "intent":
        (await db.get(TradeIntentRecord, "unsubmitted")).status = "AWAITING_APPROVAL"
      elif damage == "orphan":
        (await db.get(TradeCommandOutbox, "place")).client_order_id = "orphan"
      elif damage == "volume":
        (await db.get(TradeIntentRecord, "intent-1")).executed_volume = 30
      elif damage == "runtime_orphan":
        (await db.get(StrategyRuntimeEvent, "ORDER")).client_order_id = "orphan"
      elif damage == "parent_orphan":
        pending = await db.get(PendingTradeOrder, "client-1")
        pending.t_order_attempt = 1
        pending.t_order_parent_client_id = "missing-parent"
      elif damage == "incomplete":
        snapshot = await db.scalar(select(AgentReportInbox))
        payload = dict(snapshot.payload)
        sections = dict(payload["section_completeness_by_account"])
        sections["account-1"] = {**sections["account-1"], "trades": False}
        payload["section_completeness_by_account"] = sections
        payload["snapshot_hash"] = sha256(
          json.dumps(
            {key: value for key, value in payload.items() if key != "snapshot_hash"},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
          ).encode()
        ).hexdigest()
        snapshot.payload = payload
        (
          await db.get(AccountExecutionControl, "account-1")
        ).last_snapshot_hash = payload["snapshot_hash"]
    async with sessions() as db, db.begin():
      if damage == "audit":
        flush = db.flush

        async def fail(*args, **kwargs):
          if any(
            isinstance(row, TTradeRolloutEvent)
            and row.event_type == "LEGACY_T_DRAIN_COMPLETED"
            for row in db.new
          ):
            raise RuntimeError("synthetic audit failure")
          await flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", fail)
      with pytest.raises((ValueError, RuntimeError), match=error):
        await complete(db, now, expected_head_version=999 if damage == "head" else 2)
    async with sessions() as db, db.begin():
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.strategy_run_id == "plan-1" and head.state_version == 2
      assert (await db.get(StrategyRun, "plan-1")).status == StrategyRunStatus.RUNNING
      assert await db.get(TTradeRolloutEvent, "legacy-t-completed:plan-1") is None
      assert (await db.get(AutoExitPlanRecord, "exit-1")).remaining_volume == 40
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ACKNOWLEDGED", "QUEUED", "wrong_target"])
async def test_cancel_command_requires_settled_original_target(monkeypatch, state):
  engine, sessions, now = await seed_completion(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      db.add(
        TradeCommandOutbox(
          message_id="cancel",
          client_order_id="cancel-client",
          idempotency_key="cancel",
          device_id="device-1",
          account_id="account-1",
          owner_type="STRATEGY_RUN",
          owner_id="plan-1",
          environment="LIVE",
          payload=dict(
            command_kind="CANCEL_ORDER",
            execution_mode="live",
            client_order_id="cancel-client",
            account_id="account-1",
            broker_order_id="other" if state == "wrong_target" else "9001",
          ),
          delivery_status="ACKNOWLEDGED" if state == "wrong_target" else state,
          expires_at=now.replace(tzinfo=None),
        )
      )
    async with sessions() as db, db.begin():
      if state == "ACKNOWLEDGED":
        result = await complete(db, now)
        assert result["evidence"]["command_ids"] == ["cancel", "place"]
      else:
        with pytest.raises(ValueError, match="ORPHAN_COMMAND"):
          await complete(db, now)
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_completion_requires_serializable_transaction(monkeypatch):
  from unittest.mock import AsyncMock

  engine, sessions, now = await seed_completion(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      connection = await db.connection()
      monkeypatch.setattr(
        type(connection),
        "get_isolation_level",
        AsyncMock(return_value="READ COMMITTED"),
      )
      with pytest.raises(ValueError, match="SERIALIZABLE_REQUIRED"):
        await complete(db, now)
      assert (await db.get(TTradeGlobalConfig, "head")).strategy_run_id == "plan-1"
  finally:
    await engine.dispose()
