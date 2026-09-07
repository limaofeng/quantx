"""Original TTL and revoked-source controls against real isolated PostgreSQL guards."""

import os
from datetime import timedelta

import pytest
from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.infrastructure.test_p4_allocation_postgresql import _sessions
from tests.infrastructure.test_t_allocation_repository import NOW, _seed
from tests.infrastructure.test_t_candidate_evidence import frozen_config

_FIXTURES = (frozen_config,)

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate opt-in required",
)


async def test_postgresql_original_ttl_and_revoked_source_controls():
  async with _sessions(head="20260907_0056") as sessions:
    snapshot, _ = await _seed(sessions, count=3)
    execution_id = snapshot.cut.execution_ref.owner_id
    # _seed creates historical producer facts; align mutable fixture availability
    # with that cut, using this column's naive-UTC storage contract.
    async with sessions() as db, db.begin():
      await db.execute(
        text("UPDATE trade_intents SET updated_at=:now"),
        {"now": NOW.replace(tzinfo=None)},
      )

    async def rejected(sql, params, error):
      with pytest.raises(DBAPIError, match=error):
        async with sessions() as db, db.begin():
          await db.execute(text(sql), params)

    statement = (
      "UPDATE trade_intents SET status=:status, updated_at=:now WHERE id='intent-0'"
    )
    await rejected(
      statement,
      {"status": "EXPIRED", "now": NOW.replace(tzinfo=None)},
      "T_ALLOCATION_INTENT_NOT_EXPIRED",
    )
    await rejected(
      statement,
      {"status": "CANCELLED", "now": NOW.replace(tzinfo=None)},
      "T_ALLOCATION_SOURCE_NOT_REVOKED",
    )
    expired_at = NOW + timedelta(seconds=60)
    await rejected(
      "UPDATE trade_intents SET status='EXPIRED', allocation_version=1, updated_at=:now WHERE id='intent-0'",
      {"now": expired_at.replace(tzinfo=None)},
      "T_ALLOCATION_INTENT_VERSION_CONFLICT",
    )
    # Repository status and its durable audit roll back as a single transaction.
    with pytest.raises(RuntimeError, match="rollback"):
      async with sessions() as db, db.begin():
        assert (
          len(
            await TAllocationRepository(db).expire_pending_intents(
              execution_id=execution_id, now=expired_at
            )
          )
          == 3
        )
        raise RuntimeError("rollback")
    async with sessions() as db:
      assert set((await db.scalars(select(TradeIntentRecord.status))).all()) == {
        "ALLOCATION_PENDING"
      }
      assert (
        await db.scalar(
          text(
            "SELECT count(*) FROM t_assistant_execution_events WHERE event_key LIKE 'intent-expired:%'"
          )
        )
        == 0
      )
    async with sessions() as db, db.begin():
      repository = TAssistantExecutionRepository(db)
      execution = await repository.get_domain(execution_id)
      await repository.save_transition_with_event(
        execution.transition("DRAINING", at=NOW, has_unsettled_buy_work=True),
        expected_state_version=execution.state_version,
        event=TAssistantExecutionEvent(
          execution_id, "test-drain", "EXECUTION_DRAINING", NOW, {}
        ),
      )
      await db.execute(
        text(
          "UPDATE trade_intents SET status='CANCELLED', updated_at=:now WHERE id='intent-2'"
        ),
        {"now": NOW.replace(tzinfo=None)},
      )
      assert await TAllocationRepository(db).expire_pending_intents(
        execution_id=execution_id, now=expired_at
      ) == ("intent-0", "intent-1")
    async with sessions() as db, db.begin():
      assert (
        await TAllocationRepository(db).expire_pending_intents(
          execution_id=execution_id, now=expired_at + timedelta(seconds=1)
        )
        == ()
      )
      rows = list((await db.scalars(select(TradeIntentRecord))).all())
      assert {row.status for row in rows} == {"EXPIRED", "CANCELLED"}
      assert all(
        row.allocation_version == 0 and row.allocation_decision_id is None
        for row in rows
      )
      assert (
        await db.scalar(
          text(
            "SELECT count(*) FROM t_assistant_execution_events WHERE event_key LIKE 'intent-expired:%'"
          )
        )
        == 2
      )


async def test_postgresql_same_frame_fake_routing_cannot_complete_allocation():
  from quantx_infrastructure.models.t_allocation import TAllocationBatchRecord

  from tests.infrastructure.test_t_allocation_repository import _claim, _prepared

  async with _sessions(head="20260907_0056") as sessions:
    snapshot, candidates = await _seed(sessions, count=2, authorization="AUTO")
    for status in (
      "ROUTED",
      "PARTIAL_FILLED",
      "FILLED",
      "CANCELLED",
      "EXPIRED",
      "REJECTED",
    ):
      with pytest.raises(DBAPIError, match="T_ALLOCATION_INTENT_BINDING_CONFLICT"):
        async with sessions() as db, db.begin():
          repository = TAllocationRepository(db)
          batch = await _prepared(repository, snapshot, candidates)
          claim = await _claim(repository, batch, snapshot, candidates)
          await repository.commit(
            claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
          )
          intent = await db.get(TradeIntentRecord, candidates[0].intent_id)
          # No real order/admission/receipt backs this projection. The deferred
          # guard must reject at COMMIT despite the valid complete allocation.
          intent.status = status
          await db.flush()
      async with sessions() as db:
        assert set((await db.scalars(select(TradeIntentRecord.status))).all()) == {
          "ALLOCATION_PENDING"
        }
        assert not list((await db.scalars(select(TAllocationBatchRecord))).all())


@pytest.mark.parametrize("scenario", ["missing_witness", "mid_frame_expiry"])
async def test_postgresql_real_no_order_gate_terminal_receipt(scenario, frozen_config):
  from tests.engine.unit import test_t_assistant_paper_entry_runtime as entry_tests

  async with _sessions(head="20260907_0056") as isolated:
    if scenario == "missing_witness":
      await entry_tests.test_missing_witness_revokes_grant_and_audits_rebuild(
        isolated, frozen_config
      )
    else:
      await entry_tests.test_second_ready_expires_at_its_own_review_clock(
        isolated, frozen_config
      )
