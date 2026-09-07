"""Real multi-connection allocation transactions in an isolated test schema."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.infrastructure.test_t_allocation_repository import NOW, _seed
from tests.infrastructure.test_t_assistant_runtime_repository import _snapshot

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate opt-in required",
)


def _install(connection, schema):
  connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
  connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
  connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
  scripts = ScriptDirectory(
    str(Path(__file__).parents[2] / "packages/infrastructure/alembic")
  )
  with Operations.context(MigrationContext.configure(connection)):
    for revision in reversed(
      list(scripts.walk_revisions(base="base", head="20260907_0052"))
    ):
      revision.module.upgrade()


@asynccontextmanager
async def _sessions():
  root = create_async_engine(os.environ["DATABASE_URL"], echo=False)
  database = root.url.database or ""
  assert database != "quantx" and (
    database.startswith("test_") or database.endswith("_test")
  )
  schema = "quantx_p4_alloc_" + uuid.uuid4().hex
  scoped = None
  installed = False
  try:
    async with root.begin() as connection:
      await connection.run_sync(_install, schema)
    installed = True
    scoped = create_async_engine(
      os.environ["DATABASE_URL"],
      echo=False,
      connect_args={
        "server_settings": {"search_path": schema, "statement_timeout": "10000"}
      },
    )
    async with scoped.connect() as connection:
      assert await connection.scalar(text("SELECT current_schema()")) == schema
    yield async_sessionmaker(scoped, expire_on_commit=False)
  finally:
    if scoped is not None:
      await scoped.dispose()
    if installed:
      async with root.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
      async with root.connect() as connection:
        assert not await connection.scalar(
          text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
          {"schema": schema},
        )
    await root.dispose()


async def _prepare(sessions, snapshot, candidates, *, now=NOW, seconds=15):
  async with sessions() as db, db.begin():
    batch = await TAllocationRepository(db).prepare(
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      expires_at=now + timedelta(seconds=seconds),
    )
    return batch.allocation_batch_id


async def _claim(
  sessions, batch_id, snapshot, candidates, *, owner, now=NOW, seconds=2
):
  async with sessions() as db, db.begin():
    return await TAllocationRepository(db).claim(
      allocation_batch_id=batch_id,
      processing_owner=owner,
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      lease_seconds=seconds,
    )


@pytest.mark.asyncio
async def test_postgresql_concurrent_prepare_claim_and_restart_fence():
  async with _sessions() as sessions:
    snapshot, candidates = await _seed(sessions, count=2)
    batch_ids = await asyncio.gather(
      *(_prepare(sessions, snapshot, candidates) for _ in range(3))
    )
    assert len(set(batch_ids)) == 1
    batch_id = batch_ids[0]
    results = await asyncio.gather(
      *(
        _claim(sessions, batch_id, snapshot, candidates, owner=f"worker-{i}")
        for i in range(3)
      ),
      return_exceptions=True,
    )
    winners = [result for result in results if not isinstance(result, BaseException)]
    losers = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == 1 and winners[0] is not None
    assert len(losers) == 2
    assert all(
      isinstance(error, TAllocationConflict) and "LEASE_CONFLICT" in str(error)
      for error in losers
    )
    old_claim = winners[0]
    later = NOW + timedelta(seconds=3)
    new_claim = await _claim(
      sessions, batch_id, snapshot, candidates, owner="restarted", now=later
    )
    assert new_claim.processing_fence_token != old_claim.processing_fence_token
    async with sessions() as db, db.begin():
      with pytest.raises(TAllocationConflict, match="CLAIM|LEASE"):
        await TAllocationRepository(db).commit(
          claim=old_claim,
          snapshot=snapshot,
          candidates=candidates,
          now=later,
        )
    async with sessions() as db, db.begin():
      result = await TAllocationRepository(db).commit(
        claim=new_claim,
        snapshot=snapshot,
        candidates=tuple(reversed(candidates)),
        now=later,
      )
      assert result.status == "COMMITTED"
    async with sessions() as db:
      decisions = await TAllocationRepository(db).list_decisions(batch_id)
      assert [row.intent_id for row in decisions] == [
        row.intent_id for row in candidates
      ]
      assert [row.action for row in decisions] == ["ALLOW", "REJECT"]
      assert (
        await db.scalar(select(func.count()).select_from(TAllocationBatchRecord)) == 1
      )
      assert (
        await db.scalar(select(func.count()).select_from(TAllocationDecisionRecord))
        == 2
      )


@pytest.mark.asyncio
async def test_postgresql_delay_next_attempt_preserves_history_and_caps():
  async with _sessions() as sessions:
    snapshot, candidates = await _seed(sessions, count=2)
    delayed = (candidates[0], replace(candidates[1], data_healthy=False))
    batch_id = await _prepare(sessions, snapshot, delayed)
    claim = await _claim(sessions, batch_id, snapshot, delayed, owner="first")
    async with sessions() as db, db.begin():
      await TAllocationRepository(db).commit(
        claim=claim, snapshot=snapshot, candidates=delayed, now=NOW
      )
    async with sessions() as db:
      original = (await TAllocationRepository(db).list_decisions(batch_id))[1]
      assert original.action == "DELAY"
      original_id, original_evidence = original.decision_id, original.evidence
      next_at = original.next_eligible_at
    async with sessions() as db, db.begin():
      sibling = await db.get(TradeIntentRecord, candidates[0].intent_id)
      sibling.intent_metadata = {
        **sibling.intent_metadata,
        "risk_trace": {"action": "ALLOW"},
        "order_trace": "submitted",
      }
      sibling.notes = "Execution annotations must not alter producer identity"
    async with sessions() as db, db.begin():
      with pytest.raises(DBAPIError, match="T_ALLOCATION_INTENT_MATERIAL_IMMUTABLE"):
        async with db.begin_nested():
          await db.execute(
            text("""
            UPDATE trade_intents SET metadata=jsonb_set(metadata::jsonb,
              '{opportunity_score}', '99')::json WHERE id=:id
          """),
            {"id": candidates[0].intent_id},
          )
    later = NOW + timedelta(seconds=2)
    new_cut = replace(
      snapshot.cut,
      as_of=later,
      obligations_as_of=later,
      local_obligation_watermark="new-watermark",
    )
    latest = replace(
      snapshot,
      cut=new_cut,
      envelopes=tuple(replace(item, cut=new_cut) for item in snapshot.envelopes),
      available_cash=Decimal(500),
    )
    retry = (replace(candidates[1], intent_version=1, next_eligible_at=next_at),)
    next_batch = await _prepare(sessions, latest, retry, now=later)
    next_claim = await _claim(
      sessions, next_batch, latest, retry, now=later, owner="second"
    )
    async with sessions() as db, db.begin():
      result = await TAllocationRepository(db).commit(
        claim=next_claim, snapshot=latest, candidates=retry, now=later
      )
      assert result.allocation_attempt == 2
    async with sessions() as db:
      repository = TAllocationRepository(db)
      original = (await repository.list_decisions(batch_id))[1]
      decision = (await repository.list_decisions(next_batch))[0]
      assert (
        original.decision_id == original_id and original.evidence == original_evidence
      )
      assert decision.action == "CAP" and decision.allocated_amount_cap == 500
      record = await db.get(TradeIntentRecord, retry[0].intent_id)
      assert record.allocation_version == 2
      assert record.status == "AWAITING_APPROVAL"
      assert record.allocation_decision_id == decision.decision_id


@pytest.mark.asyncio
async def test_postgresql_half_batch_rolls_back_after_second_decision_flush():
  async with _sessions() as sessions:
    snapshot, candidates = await _seed(sessions, count=2)
    batch_id = await _prepare(sessions, snapshot, candidates)
    claim = await _claim(sessions, batch_id, snapshot, candidates, owner="worker")

    def fail_after_insert(mapper, connection, target):
      if target.rank == 2:
        raise RuntimeError("injected second decision failure")

    event.listen(TAllocationDecisionRecord, "after_insert", fail_after_insert)
    try:
      async with sessions() as db, db.begin():
        with pytest.raises(RuntimeError, match="injected"):
          await TAllocationRepository(db).commit(
            claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
          )
    finally:
      event.remove(TAllocationDecisionRecord, "after_insert", fail_after_insert)
    async with sessions() as db:
      assert (
        await db.scalar(select(func.count()).select_from(TAllocationDecisionRecord))
        == 0
      )
      rows = (
        await db.scalars(
          select(TradeIntentRecord).where(
            TradeIntentRecord.allocation_cycle_id == snapshot.cycle_id
          )
        )
      ).all()
      assert all(
        row.status == "ALLOCATION_PENDING" and row.allocation_version == 0
        for row in rows
      )
    async with sessions() as db, db.begin():
      result = await TAllocationRepository(db).commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
      )
      assert result.status == "COMMITTED"


@pytest.mark.asyncio
async def test_postgresql_supersede_ttl_recovery_and_environment_rejection():
  async with _sessions() as sessions:
    snapshot, candidates = await _seed(sessions, authorization="AUTO")
    first = await _prepare(sessions, snapshot, candidates)
    claim = await _claim(sessions, first, snapshot, candidates, owner="first")
    changed = replace(snapshot, available_cash=Decimal(9000))
    async with sessions() as db, db.begin():
      assert (
        await TAllocationRepository(db).renew(
          claim=claim,
          snapshot=changed,
          candidates=candidates,
          now=NOW,
          lease_seconds=2,
        )
        is None
      )
    second = await _prepare(sessions, changed, candidates, seconds=1)
    later = NOW + timedelta(seconds=2)
    async with sessions() as db, db.begin():
      result = await TAllocationRepository(db).expire(
        allocation_batch_id=second, now=later
      )
      assert result.status == "EXPIRED"
    third = await _prepare(sessions, changed, candidates, now=later)
    async with sessions() as db:
      rows = await TAllocationRepository(db).list_recoverable(
        execution_id=snapshot.cut.execution_ref.owner_id
      )
      assert [row.allocation_batch_id for row in rows] == [third]
      assert rows[0].allocation_attempt == 3
      assert (
        await db.scalar(select(func.count()).select_from(TAllocationDecisionRecord))
        == 0
      )
    live_cut = replace(changed.cut, environment=ExecutionEnvironment.LIVE)
    live = replace(
      changed,
      cut=live_cut,
      envelopes=tuple(replace(item, cut=live_cut) for item in changed.envelopes),
    )
    with pytest.raises(TAllocationConflict, match="SCOPE_INVALID"):
      await _prepare(sessions, live, candidates, now=later)
    active = await _claim(
      sessions, third, changed, candidates, owner="recovered", now=later
    )
    async with sessions() as db, db.begin():
      await TAllocationRepository(db).commit(
        claim=active, snapshot=changed, candidates=candidates, now=later
      )
    async with sessions() as db:
      batches = (
        await db.scalars(
          select(TAllocationBatchRecord).order_by(
            TAllocationBatchRecord.allocation_attempt
          )
        )
      ).all()
      assert [row.status for row in batches] == ["SUPERSEDED", "EXPIRED", "COMMITTED"]
      intent = await db.get(TradeIntentRecord, candidates[0].intent_id)
      assert intent.status == "EXECUTION_READY" and intent.environment == "PAPER"


@pytest.mark.asyncio
async def test_postgresql_cycle_retry_and_allocation_share_execution_first_lock_order():
  async with _sessions() as sessions:
    snapshot, candidates = await _seed(sessions)
    started = asyncio.Event()
    waiting_pid = []

    async def allocate():
      async with sessions() as db:
        waiting_pid.append(await db.scalar(text("SELECT pg_backend_pid()")))
        await db.commit()
        started.set()
        async with db.begin():
          row = await TAllocationRepository(db).prepare(
            snapshot=snapshot,
            candidates=candidates,
            now=NOW,
            expires_at=NOW + timedelta(seconds=15),
          )
          return row.allocation_batch_id

    task = None
    try:
      async with sessions() as db, db.begin():
        await db.scalar(
          select(TAssistantExecutionRecord)
          .where(
            TAssistantExecutionRecord.execution_id
            == snapshot.cut.execution_ref.owner_id
          )
          .with_for_update()
        )
        task = asyncio.create_task(allocate())
        await asyncio.wait_for(started.wait(), 5)
        async with asyncio.timeout(5):
          while not await db.scalar(
            text(
              "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"
            ),
            {"pid": waiting_pid[0]},
          ):
            await asyncio.sleep(0.01)
        execution = await TAssistantExecutionRepository(db).get_domain(
          snapshot.cut.execution_ref.owner_id
        )
        cycle = await asyncio.wait_for(
          TAssistantDecisionCycleRepository(db).prepare_material_cycle(
            snapshot=_snapshot(execution),
            cycle_id="unused-retry-id",
            now=NOW,
          ),
          5,
        )
        assert cycle.cycle_id == snapshot.cycle_id
      assert await asyncio.wait_for(task, 5)
    finally:
      if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
