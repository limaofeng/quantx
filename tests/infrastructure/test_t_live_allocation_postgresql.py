"""Full migration chain and LIVE allocation/drain in disposable test schemas."""

import os
from datetime import UTC, datetime

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_engine.t_assistant_live_drain import drain_live_entry_work
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.infrastructure.test_p4_allocation_postgresql import _sessions
from tests.infrastructure.test_t_allocation_repository import (
  NOW,
  _claim,
  _prepared,
  _seed,
)

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate required",
)


@pytest.mark.parametrize(
  "environment", [ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE]
)
async def test_full_migration_chain_allocation_and_live_drain(environment):
  async with _sessions(head="20260909_0061") as sessions:
    snapshot, candidates = await _seed(sessions, environment=environment)
    async with sessions() as db, db.begin():
      repo = TAllocationRepository(db)
      batch = await _prepared(repo, snapshot, candidates)
      claim = await _claim(repo, batch, snapshot, candidates)
      await repo.commit(claim=claim, snapshot=snapshot, candidates=candidates, now=NOW)
      assert batch.environment == environment.value
    async with sessions() as db:
      assert (await db.get(TradeIntentRecord, "intent-0")).status == "AWAITING_APPROVAL"
    if environment is ExecutionEnvironment.LIVE:
      async with sessions() as db, db.begin():
        result = await drain_live_entry_work(
          db,
          execution_id=snapshot.cut.execution_ref.owner_id,
          now=datetime.now(UTC),
          reason="ISOLATED_CUTOVER",
        )
        assert result.cancelled_intent_ids == ("intent-0",)
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
      async with sessions() as db:
        assert (await db.get(TradeIntentRecord, "intent-0")).status == "CANCELLED"


async def test_live_drain_missing_audit_rolls_back_source_and_intent(monkeypatch):
  async with _sessions(head="20260909_0061") as sessions:
    snapshot, _ = await _seed(sessions, environment=ExecutionEnvironment.LIVE)
    original = TAssistantExecutionRepository.append_event

    async def omit(self, event):
      if event.event_type != "LIVE_ENTRY_DRAINED":
        return await original(self, event)

    monkeypatch.setattr(TAssistantExecutionRepository, "append_event", omit)
    with pytest.raises(DBAPIError, match="T_ALLOCATION_LIVE_DRAIN_AUDIT_REQUIRED"):
      async with sessions() as db, db.begin():
        await drain_live_entry_work(
          db,
          execution_id=snapshot.cut.execution_ref.owner_id,
          now=datetime.now(UTC),
          reason="INJECT_MISSING_AUDIT",
        )
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with sessions() as db:
      assert (
        await db.get(TradeIntentRecord, "intent-0")
      ).status == "ALLOCATION_PENDING"
      assert (
        await db.get(TAssistantExecutionRecord, "live-fixture")
      ).status == "RUNNING"


async def test_live_intent_cannot_skip_allocation_or_rewrite_material():
  async with _sessions(head="20260909_0061") as sessions:
    await _seed(sessions, environment=ExecutionEnvironment.LIVE)
    for assignment, reason in (
      ("status='EXECUTION_READY'", "T_ALLOCATION_INTENT_VERSION_CONFLICT"),
      ("target_amount=2000", "T_ALLOCATION_INTENT_MATERIAL_IMMUTABLE"),
      ("status='CANCELLED'", "T_ALLOCATION_LIVE_DRAIN_SCOPE_INVALID"),
    ):
      with pytest.raises(DBAPIError, match=reason):
        async with sessions() as db, db.begin():
          await db.execute(
            text(f"UPDATE trade_intents SET {assignment} WHERE id='intent-0'")
          )
