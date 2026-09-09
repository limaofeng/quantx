"""Full PostgreSQL migration chain for confirmed reallocation and rollback."""

import os
from dataclasses import replace

import pytest
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.infrastructure.test_p4_allocation_postgresql import _sessions
from tests.infrastructure.test_t_allocation_repository import _claim, _prepared
from tests.infrastructure.test_t_entry_confirmation import (
  CONFIRMED,
  confirm,
  fresh,
  seed_confirmable,
)
from tests.infrastructure.test_t_entry_confirmation import (
  signing_key as _signing_key,
)

signing_key = _signing_key

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate required",
)


async def test_postgresql_confirm_reallocate_and_prevent_bypass(signing_key):
  async with _sessions(head="20260909_0062") as sessions:
    snapshot, candidates = await seed_confirmable(sessions)
    for status in ("ALLOCATION_PENDING", "EXECUTION_READY"):
      with pytest.raises(DBAPIError, match="T_ENTRY_CONFIRMED_REALLOCATION_REQUIRED"):
        async with sessions() as db, db.begin():
          await db.execute(
            text("UPDATE trade_intents SET status=:status WHERE id='intent-0'"),
            {"status": status},
          )
    async with sessions() as db, db.begin():
      await confirm(db)
      await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with sessions() as db, db.begin():
      newer = fresh(snapshot)
      candidates = tuple(replace(c, intent_version=1) for c in candidates)
      repo = TAllocationRepository(db)
      batch = await _prepared(repo, newer, candidates, now=CONFIRMED)
      claim = await _claim(repo, batch, newer, candidates, now=CONFIRMED)
      await repo.commit(
        claim=claim, snapshot=newer, candidates=candidates, now=CONFIRMED
      )
      await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
      row = await db.get(TradeIntentRecord, "intent-0")
      assert row.status == "EXECUTION_READY" and row.allocation_version == 2
      assert (await repo.list_decisions(batch.allocation_batch_id))[
        0
      ].allocated_amount_cap == 500


async def test_postgresql_rejects_stale_cut_even_if_application_check_is_bypassed(
  signing_key, monkeypatch
):
  import quantx_infrastructure.repositories.t_allocation_repository as module

  async def forged_proof(*args, **kwargs):
    return True

  async with _sessions(head="20260909_0062") as sessions:
    snapshot, candidates = await seed_confirmable(sessions)
    async with sessions() as db, db.begin():
      await confirm(db)
    monkeypatch.setattr(module, "confirmed_for_allocation", forged_proof)
    candidates = tuple(replace(c, intent_version=1) for c in candidates)
    with pytest.raises(DBAPIError, match="T_ENTRY_POST_CONFIRMATION_SNAPSHOT_REQUIRED"):
      async with sessions() as db, db.begin():
        repo = TAllocationRepository(db)
        batch = await _prepared(repo, snapshot, candidates, now=CONFIRMED)
        claim = await _claim(repo, batch, snapshot, candidates, now=CONFIRMED)
        await repo.commit(
          claim=claim, snapshot=snapshot, candidates=candidates, now=CONFIRMED
        )
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    async with sessions() as db:
      row = await db.get(TradeIntentRecord, "intent-0")
      assert row.status == "ALLOCATION_PENDING" and row.allocation_version == 1


async def test_postgresql_concurrent_confirmations_are_one_operation(signing_key):
  import asyncio

  async with _sessions(head="20260909_0062") as sessions:
    await seed_confirmable(sessions)

    async def execute():
      async with sessions() as db, db.begin():
        return await confirm(db)

    first, second = await asyncio.gather(execute(), execute())
    assert first == second
    async with sessions() as db:
      count = await db.scalar(
        text(
          "SELECT count(*) FROM t_assistant_execution_events WHERE event_type='LIVE_ENTRY_CONFIRMED'"
        )
      )
      assert count == 1
      row = await db.get(TradeIntentRecord, "intent-0")
      assert row.status == "ALLOCATION_PENDING" and row.allocation_version == 1


async def test_postgresql_confirmation_racing_drain_cannot_reopen_entry(signing_key):
  import asyncio
  from datetime import timedelta

  from quantx_engine.t_assistant_live_drain import drain_live_entry_work
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantExecutionRecord,
  )

  async with _sessions(head="20260909_0062") as sessions:
    await seed_confirmable(sessions)

    async def approve():
      try:
        async with sessions() as db, db.begin():
          return await confirm(db)
      except ValueError as exc:
        assert str(exc) == "T_ENTRY_SOURCE_NOT_READY"
        return None

    async def drain():
      async with sessions() as db, db.begin():
        return await drain_live_entry_work(
          db,
          execution_id="live-fixture",
          now=CONFIRMED + timedelta(seconds=1),
          reason="CONCURRENT_BLOCK",
        )

    _, drained = await asyncio.gather(approve(), drain())
    assert drained.cancelled_intent_ids == ("intent-0",)
    async with sessions() as db:
      assert (await db.get(TradeIntentRecord, "intent-0")).status == "CANCELLED"
      assert (
        await db.get(TAssistantExecutionRecord, "live-fixture")
      ).status == "DRAINING"
