"""Explicit seed and replay in an isolated account; no live configuration changes."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentReportInbox,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.services.live_position_attribution import (
  LivePositionAttributionService,
)
from sqlalchemy import select

from tests.infrastructure.test_account_capacity_service import (
  capacity_db as _capacity_db,
)
from tests.infrastructure.test_t_assistant_runtime_repository import _version

capacity_db = _capacity_db
NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
SEED_AT = NOW - timedelta(minutes=1)
CODE = "600000.SH"


def buckets():
  return {
    CODE: {
      name: dict(total_volume=qty, today_buy_volume=0)
      for name, qty in [("locked_core", 200), ("core", 600), ("swing", 200)]
    }
  }


async def publish_snapshot(db, stamp, *, total=1000, free=1000, orders=None):
  payload = dict(
    snapshot_id=stamp.isoformat(),
    source_event_at=stamp.isoformat(),
    is_complete=True,
    accounts=[dict(account_id="account", cash=10000, total_asset=20000)],
    positions_by_account={
      "account": [dict(stock_code=CODE, volume=total, can_use_volume=free)]
    },
    orders=orders or [],
    trades=[],
    section_completeness_by_account={
      "account": dict.fromkeys(("account", "positions", "orders", "trades"), True)
    },
    snapshot_authority_by_account={
      "account": dict(
        initial_status=0,
        final_status=0,
        stable=True,
        snapshot_eligible=True,
        status_name="OK",
        reason_code="AUTHORITATIVE",
      )
    },
  )
  digest = hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()
  payload["snapshot_hash"] = digest
  db.add(
    AgentReportInbox(
      message_id=stamp.isoformat(),
      device_id="fixture",
      message_type="delta_report",
      raw_payload_hash=digest,
      business_idempotency_key=stamp.isoformat(),
      payload=payload,
      processing_status="PROCESSED",
      received_at=stamp.replace(tzinfo=None),
      processed_at=stamp.replace(tzinfo=None),
    )
  )
  control = await db.get(AccountExecutionControl, "account")
  if control is None:
    control = AccountExecutionControl(
      account_id="account", created_at=stamp, updated_at=stamp
    )
    db.add(control)
  control.last_snapshot_id = payload["snapshot_id"]
  control.last_snapshot_hash = digest
  control.last_snapshot_at = stamp.replace(tzinfo=None)
  control.updated_at = stamp
  from sqlalchemy.orm.attributes import flag_modified

  flag_modified(control, "updated_at")
  await db.flush()
  return payload["snapshot_id"], digest


@pytest.fixture
async def db(capacity_db):
  for model in (
    TTradeGlobalConfig,
    TAssistantConfigVersionRecord,
    TAssistantExecutionRecord,
    TAssistantExecutionEventRecord,
    StrategyRuntimeEvent,
  ):
    await capacity_db.run_sync(
      lambda session: model.__table__.create(session.connection())
    )
  capacity_db.add(TTradeGlobalConfig(id="config-1", account_id="account"))
  version = _version("config-1")
  await TAssistantConfigRepository(capacity_db).append_version(version)
  capacity_db.add(
    TAssistantExecutionRecord(
      execution_id="source",
      config_id="config-1",
      config_version_id=version.config_version_id,
      frozen_config_version=1,
      config_snapshot_hash=version.config_snapshot_hash,
      account_id="account",
      environment="LIVE",
      entry_authorization="MANUAL_CONFIRM",
      rollout_stage="CANARY",
      status="WARMING",
      entry_readiness="WARMING",
      entry_readiness_reasons=[],
      entry_readiness_as_of=SEED_AT,
      policy_version=version.policy_version,
      feature_schema_version=1,
      scorer_mode="RULE_ONLY",
      created_at=SEED_AT,
      updated_at=SEED_AT,
    )
  )
  await publish_snapshot(capacity_db, SEED_AT)
  await capacity_db.commit()
  return capacity_db


async def approve(db, **changes):
  control = await db.get(AccountExecutionControl, "account")
  args = dict(
    execution_id="source",
    actor_id="fixture-operator",
    instruments=buckets(),
    expected_snapshot_id=control.last_snapshot_id,
    expected_snapshot_hash=control.last_snapshot_hash,
    as_of=SEED_AT,
    max_age_seconds=90,
  )
  args.update(changes)
  return await LivePositionAttributionService(db).approve_seed(**args)


async def read(db):
  return await LivePositionAttributionService(db).read(
    account_id="account", as_of=NOW, max_age_seconds=90
  )


async def add_buy(db, *, owner_id="source", requested_volume=100, status="FILLED", orders=None):
  at = NOW - timedelta(seconds=10)
  base = dict(
    account_id="account",
    owner_type="T_ASSISTANT_EXECUTION",
    owner_id=owner_id,
    environment="LIVE",
    broker_order_id="101",
    bucket="swing",
    intent_id="intent",
    batch_id="batch",
    t_trade_role="ENTRY",
    created_at=NOW,
    updated_at=NOW,
  )
  db.add(
    PendingTradeOrder(
      client_order_id="client",
      user_id="fixture",
      instrument_code=CODE,
      side="BUY",
      order_type="LIMIT",
      limit_price="10",
      volume=requested_volume,
      status=status,
      **base,
    )
  )
  db.add(
    OrderCorrelation(
      id="correlation", client_order_id="client", trace_id="trace", **base
    )
  )
  db.add(
    Trade(
      id="fill",
      time=(at + timedelta(hours=8)).replace(tzinfo=None),
      price=10,
      volume=100,
      amount=1000,
      account_id="account",
      stock_code=CODE,
      order_id=101,
      order_sysid="sys",
      order_type=23,
      created_at=NOW,
      updated_at=NOW,
    )
  )
  report = dict(
    execution_id="fill",
    account_id="account",
    order_id=101,
    stock_code=CODE,
    traded_volume=100,
    traded_price=10,
    traded_time=int(at.timestamp()),
  )
  db.add(
    StrategyRuntimeEvent(
      event_id="event",
      business_key="fill",
      client_order_id="client",
      broker_order_id="101",
      owner_type="T_ASSISTANT_EXECUTION",
      owner_id=owner_id,
      environment="LIVE",
      event_type="TRADE",
      application_status="APPLIED",
      payload=dict(
        report=report,
        metadata=dict(
          bucket="swing",
          substitution_plan=None,
          t_batch_id="batch",
          t_trade_role="entry",
        ),
      ),
      created_at=NOW,
      applied_at=NOW,
    )
  )
  await publish_snapshot(db, NOW, total=1100, orders=orders)


async def test_seed_is_explicit_append_only_idempotent_and_reusable_after_source_stops(
  db,
):
  async with db.begin():
    key = await approve(db)
    assert await approve(db) == key
  async with db.begin():
    source = await db.get(TAssistantExecutionRecord, "source")
    source.status = "STOPPED"
    source.completed_at = NOW
    source.entry_readiness = "BLOCKED"
    source.updated_at = NOW
    await db.flush()
    result = await read(db)
    assert result.seed_event_key == key
    assert result.projection.instruments[CODE]["core"]["total_volume"] == 600
    assert len((await db.scalars(select(TAssistantExecutionEventRecord))).all()) == 1


async def test_confirmed_broker_fill_replays_after_restart_without_changing_seed(db):
  async with db.begin():
    await approve(db)
  async with db.begin():
    await add_buy(db)
  db.expunge_all()
  async with db.begin():
    result = await read(db)
    assert result.projection.instruments[CODE]["swing"] == dict(
      total_volume=300, today_buy_volume=100, available_volume=200
    )
    assert (await read(db)).evidence_hash == result.evidence_hash


@pytest.mark.parametrize(
  "damage,reason",
  [
    ("no_seed", "APPROVED_SEED_REQUIRED"),
    ("wrong_snapshot", "SNAPSHOT_CHANGED"),
    ("wrong_total", "BROKER_TOTAL_CONFLICT"),
    ("no_actor", "EXPLICIT_ACTOR_REQUIRED"),
  ],
)
async def test_seed_rejects_unapproved_or_inconsistent_material(db, damage, reason):
  async with db.begin():
    with pytest.raises(ValueError, match=reason):
      if damage == "no_seed":
        await read(db)
      elif damage == "wrong_snapshot":
        await approve(db, expected_snapshot_hash="0" * 64)
      elif damage == "no_actor":
        await approve(db, actor_id="")
      else:
        values = buckets()
        values[CODE]["core"]["total_volume"] = 500
        await approve(db, instruments=values)


@pytest.mark.parametrize(
  "damage,reason",
  [
    ("unapplied", "FILL_NOT_CONVERGED"),
    ("unknown_owner", "FILL_ATTRIBUTION_REQUIRED"),
    ("stale_snapshot", "SNAPSHOT_UNCOVERED_FILL"),
    ("corrupt_report", "FILL_LINEAGE_CONFLICT"),
  ],
)
async def test_new_fills_require_complete_original_lineage_and_covering_snapshot(
  db, damage, reason
):
  async with db.begin():
    await approve(db)
  async with db.begin():
    await add_buy(db)
    if damage == "unapplied":
      (await db.get(StrategyRuntimeEvent, "event")).application_status = "PENDING"
    elif damage == "unknown_owner":
      await db.delete(await db.get(OrderCorrelation, "correlation"))
    elif damage == "stale_snapshot":
      row = await db.get(AccountExecutionControl, "account")
      old = await db.get(AgentReportInbox, SEED_AT.isoformat())
      row.last_snapshot_id = old.payload["snapshot_id"]
      row.last_snapshot_hash = old.payload["snapshot_hash"]
      row.last_snapshot_at = SEED_AT.replace(tzinfo=None)
    else:
      event = await db.get(StrategyRuntimeEvent, "event")
      event.payload = {
        **event.payload,
        "report": {**event.payload["report"], "traded_volume": 200},
      }
  async with db.begin():
    with pytest.raises(ValueError, match=reason):
      await read(db)


async def test_reconciliation_requires_exact_previous_seed_and_retains_history(db):
  async with db.begin():
    key = await approve(db)
  async with db.begin():
    await publish_snapshot(db, NOW, total=1100, free=1100)
    values = buckets()
    values[CODE]["core"]["total_volume"] = 700
    with pytest.raises(ValueError, match="SEED_PREDECESSOR_REQUIRED"):
      await approve(db, instruments=values, as_of=NOW)
    newer = await approve(
      db, instruments=values, as_of=NOW, expected_seed_event_key=key
    )
    assert newer != key
    assert (await read(db)).projection.instruments[CODE]["core"]["total_volume"] == 700
    assert len((await db.scalars(select(TAssistantExecutionEventRecord))).all()) == 2


async def test_seed_audit_failure_leaves_no_baseline_even_when_caller_catches(
  db, monkeypatch
):
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  async def fail(self, event):
    raise RuntimeError("injected")

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with db.begin():
    with pytest.raises(RuntimeError, match="injected"):
      await approve(db)
  async with db.begin():
    with pytest.raises(ValueError, match="APPROVED_SEED_REQUIRED"):
      await read(db)


async def test_account_snapshot_must_be_strictly_younger_than_ninety_seconds(db):
  async with db.begin():
    await approve(db)
    with pytest.raises(ValueError, match="SNAPSHOT_STALE_OR_FUTURE"):
      await LivePositionAttributionService(db).read(
        account_id="account", as_of=SEED_AT + timedelta(seconds=90), max_age_seconds=90
      )
