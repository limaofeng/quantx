"""Isolated durable source drain; no QMT connection or real order execution."""

from datetime import timedelta

import pytest
from quantx_engine.t_assistant_live_drain import drain_live_entry_work
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import func, select

from tests.infrastructure.test_t_assistant_runtime_repository import NOW, _version
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _base_sessions,
)

base_sessions = _base_sessions


@pytest.fixture
async def sessions(base_sessions):
  async with base_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          PendingTradeOrder.__table__,
          OrderCorrelation.__table__,
          TradeCommandOutbox.__table__,
        ],
      )
    )
  async with base_sessions() as db, db.begin():
    db.add(TTradeGlobalConfig(id="config-1", account_id="account-1", mode="live"))
    version = _version("config-1")
    await TAssistantConfigRepository(db).append_version(version)
    db.add(
      TAssistantExecutionRecord(
        execution_id="live-1",
        config_id="config-1",
        config_version_id=version.config_version_id,
        frozen_config_version=version.version,
        config_snapshot_hash=version.config_snapshot_hash,
        account_id="account-1",
        environment="LIVE",
        entry_authorization="MANUAL_CONFIRM",
        rollout_stage="CANARY",
        status="RUNNING",
        entry_readiness="READY",
        entry_readiness_reasons=[],
        entry_readiness_as_of=NOW,
        policy_version=version.policy_version,
        feature_schema_version=version.feature_schema_version,
        scorer_mode="RULE_ONLY",
        started_at=NOW,
        created_at=NOW,
        updated_at=NOW,
      )
    )
    for identity, status in [
      ("unsubmitted", "AWAITING_APPROVAL"),
      ("submitted", "EXECUTION_READY"),
    ]:
      db.add(
        TradeIntentRecord(
          id=identity,
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id="live-1",
          environment="LIVE",
          idempotency_key=identity,
          account_id="account-1",
          instrument_code="600000.SH",
          direction="BUY",
          bucket="swing",
          status=status,
          created_at=NOW,
          updated_at=NOW,
        )
      )
    db.add(
      PendingTradeOrder(
        client_order_id="client-1",
        user_id="user-1",
        account_id="account-1",
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id="live-1",
        environment="LIVE",
        instrument_code="600000.SH",
        side="BUY",
        order_type="LIMIT",
        limit_price="10",
        volume=100,
        status="UNKNOWN",
        intent_id="submitted",
        created_at=NOW,
        updated_at=NOW,
      )
    )
    db.add(
      TradeCommandOutbox(
        message_id="message-1",
        client_order_id="client-1",
        idempotency_key="command-1",
        device_id="device-1",
        account_id="account-1",
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id="live-1",
        environment="LIVE",
        payload={"immutable": "original"},
        delivery_status="DELIVERED",
        expires_at=NOW + timedelta(seconds=30),
        created_at=NOW,
        updated_at=NOW,
      )
    )
  return base_sessions


async def _drain(db):
  return await drain_live_entry_work(
    db,
    execution_id="live-1",
    now=NOW + timedelta(seconds=2),
    reason="SUCCESSOR_REQUESTED",
  )


async def test_drain_cancels_only_unsubmitted_and_keeps_unknown_order(sessions):
  async with sessions() as db, db.begin():
    result = await _drain(db)
    assert result.cancelled_intent_ids == ("unsubmitted",)
    assert result.retained_intent_ids == ("submitted",)
    assert result.retained_client_order_ids == ("client-1",)
    assert (await db.get(TradeIntentRecord, "unsubmitted")).status == "CANCELLED"
    assert (await db.get(TradeIntentRecord, "submitted")).status == "EXECUTION_READY"
    assert not (
      await TAssistantExecutionRepository(db).get_domain("live-1")
    ).can_produce_entry
    pending = await db.get(PendingTradeOrder, "client-1")
    assert (pending.status, pending.owner_id) == ("UNKNOWN", "live-1")
    outbox = await db.get(TradeCommandOutbox, "message-1")
    assert outbox.payload == {"immutable": "original"}
    assert outbox.delivery_status == "DELIVERED"


async def test_restart_retries_without_duplicate_events_or_reopening_source(sessions):
  async with sessions() as db, db.begin():
    await _drain(db)
  async with sessions() as db, db.begin():
    result = await _drain(db)
    assert result.cancelled_intent_ids == ()
    assert result.retained_intent_ids == ("submitted",)
    assert (await db.get(TAssistantExecutionRecord, "live-1")).status == "DRAINING"
    assert (
      await db.scalar(select(func.count()).select_from(TAssistantExecutionEventRecord))
      == 2
    )


@pytest.mark.parametrize(
  "failure_event", ["LIVE_ENTRY_DRAINED", "LIVE_EXECUTION_DRAIN_REQUESTED"]
)
async def test_event_write_failure_rolls_back_intent_and_lifecycle_even_if_caught(
  sessions, monkeypatch, failure_event
):
  original = TAssistantExecutionRepository.append_event

  async def fail(self, event):
    if event.event_type == failure_event:
      raise RuntimeError("injected event failure")
    return await original(self, event)

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="injected"):
      await _drain(db)
  async with sessions() as db:
    assert (
      await db.get(TradeIntentRecord, "unsubmitted")
    ).status == "AWAITING_APPROVAL"
    assert (await db.get(TAssistantExecutionRecord, "live-1")).status == "RUNNING"


@pytest.mark.parametrize(
  "damage,code",
  [
    ("orphan_outbox", "LIVE_DRAIN_OUTBOX_BINDING_MISSING"),
    ("account", "LIVE_DRAIN_FACT_SCOPE_INVALID"),
    ("future", "LIVE_DRAIN_FUTURE_EVIDENCE"),
  ],
)
async def test_inconsistent_facts_never_cancel_local_work(sessions, damage, code):
  async with sessions() as db, db.begin():
    outbox = await db.get(TradeCommandOutbox, "message-1")
    if damage == "orphan_outbox":
      outbox.client_order_id = "missing-client"
    elif damage == "account":
      outbox.account_id = "other-account"
    outbox.updated_at = NOW + timedelta(seconds=3 if damage == "future" else 1)
    await db.flush()
    with pytest.raises(ValueError, match=code):
      await _drain(db)
    assert (
      await db.get(TradeIntentRecord, "unsubmitted")
    ).status == "AWAITING_APPROVAL"


async def test_reconcile_required_source_stays_blocked(sessions):
  async with sessions() as db, db.begin():
    execution = await db.get(TAssistantExecutionRecord, "live-1")
    execution.status = "RECONCILE_REQUIRED"
    execution.entry_readiness = "RECONCILE_REQUIRED"
    execution.entry_readiness_reasons = ["UNKNOWN_BUY"]
    execution.updated_at = NOW
  async with sessions() as db, db.begin():
    await _drain(db)
    assert (
      await db.get(TAssistantExecutionRecord, "live-1")
    ).status == "RECONCILE_REQUIRED"


async def test_engine_command_commits_drain_through_real_service(sessions, monkeypatch):
  import quantx_engine.command_processor as processor

  monkeypatch.setattr(processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(processor, "utcnow", lambda: NOW + timedelta(seconds=2))
  result = await processor._dispatch(
    "T_ASSISTANT_DRAIN_ENTRY",
    {"execution_id": "live-1", "reason": "OPERATOR_BLOCK_ENTRY"},
    command_id="drain-command-1",
  )
  assert result["success"] is True
  assert result["retained_client_order_ids"] == ["client-1"]
  async with sessions() as db:
    assert (await db.get(TAssistantExecutionRecord, "live-1")).status == "DRAINING"
    assert (await db.get(TradeIntentRecord, "unsubmitted")).status == "CANCELLED"


@pytest.mark.parametrize(
  "field,value", [("order_id", "reported-order"), ("executed_volume", 100)]
)
async def test_order_projection_without_pending_is_retained(sessions, field, value):
  async with sessions() as db, db.begin():
    intent = await db.get(TradeIntentRecord, "unsubmitted")
    setattr(intent, field, value)
    intent.updated_at = NOW + timedelta(seconds=1)
  async with sessions() as db, db.begin():
    result = await _drain(db)
    assert result.cancelled_intent_ids == ()
    assert "unsubmitted" in result.retained_intent_ids
