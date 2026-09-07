"""P4 constraints on a real, transaction-isolated PostgreSQL schema."""

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from tests.infrastructure.test_p2_p3_postgresql_migration_gate import _run_gate

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate opt-in required",
)


def _p4_gate(connection, schema):
  _run_gate(connection, schema)
  scripts = ScriptDirectory(
    str(Path(__file__).parents[2] / "packages/infrastructure/alembic")
  )
  with Operations.context(MigrationContext.configure(connection)):
    scripts.get_revision("20260907_0051").module.upgrade()
  connection.exec_driver_sql("""
    INSERT INTO t_assistant_executions
    SELECT (jsonb_populate_record(NULL::t_assistant_executions, to_jsonb(e) ||
      '{"execution_id":"paper-execution","environment":"PAPER"}')).*
    FROM t_assistant_executions e WHERE execution_id='gate-execution'
  """)
  connection.exec_driver_sql("""
    INSERT INTO t_assistant_decision_cycles
    SELECT (jsonb_populate_record(NULL::t_assistant_decision_cycles, to_jsonb(c) ||
      '{"cycle_id":"paper-cycle","execution_id":"paper-execution"}')).*
    FROM t_assistant_decision_cycles c WHERE cycle_id='cycle'
  """)
  connection.exec_driver_sql("""
    UPDATE t_assistant_decision_cycles SET status='PROPOSALS_COMMITTED',
      committed_at=now(), output_manifest='{}', output_manifest_hash=repeat('a',64)
    WHERE cycle_id='paper-cycle'
  """)
  for name in ("one", "two"):
    connection.execute(
      sa.text("""
      INSERT INTO trade_intents
      (id,owner_type,owner_id,environment,idempotency_key,account_id,
       instrument_code,direction,bucket,reason,priority,confidence,status,
       metadata,created_at,updated_at,allocation_cycle_id)
      VALUES (:id,'T_ASSISTANT_EXECUTION','paper-execution','PAPER',:id,
        'gate-account','600000.SH','BUY','swing','gate','NORMAL',1,
        'ALLOCATION_PENDING','{}',now(),now(),'paper-cycle')
    """),
      {"id": name},
    )

  def reject(statement, reason):
    with connection.begin_nested() as savepoint:
      with pytest.raises(sa.exc.DBAPIError, match=reason):
        connection.exec_driver_sql(statement)
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
      savepoint.rollback()

  def prepare():
    connection.exec_driver_sql("""
      INSERT INTO t_allocation_batches
      (allocation_batch_id,execution_id,cycle_id,environment,allocation_attempt,
       portfolio_input_fingerprint,portfolio_snapshot,intent_manifest_hash,
       intent_manifest,intent_count,status,created_at,expires_at)
      VALUES ('batch','paper-execution','paper-cycle','PAPER',1,repeat('a',64),'{}',
        repeat('b',64),'[{"intent_id":"one","intent_version":0,"candidate_id":"c1"},
        {"intent_id":"two","intent_version":0,"candidate_id":"c2"}]',2,
        'PREPARED',now(),now()+interval '15 seconds')
    """)

  prepare()
  reject(
    "UPDATE trade_intents SET status='EXECUTION_READY' WHERE id='one'",
    "T_ALLOCATION_INTENT_VERSION_CONFLICT",
  )
  reject(
    """
    INSERT INTO t_allocation_batches
    SELECT (jsonb_populate_record(NULL::t_allocation_batches,to_jsonb(b) ||
      '{"allocation_batch_id":"duplicate"}')).* FROM t_allocation_batches b
  """,
    "uq_t_allocation",
  )
  reject(
    "UPDATE t_allocation_batches SET processing_owner='half-claim'",
    "ck_t_allocation_claim",
  )
  reject(
    "UPDATE t_allocation_batches SET environment='LIVE'",
    "T_ALLOCATION_HISTORY_IMMUTABLE",
  )
  reject(
    """
    UPDATE t_allocation_batches SET status='COMMITTED', committed_at=now(),
      decision_manifest_hash=repeat('c',64)
  """,
    "T_ALLOCATION_HALF_BATCH",
  )
  assert connection.scalar(sa.text("SELECT count(*) FROM t_allocation_decisions")) == 0
  assert (
    connection.scalar(sa.text("SELECT status FROM t_allocation_batches")) == "PREPARED"
  )

  def decision(name, rank):
    connection.execute(
      sa.text("""
      INSERT INTO t_allocation_decisions
      (decision_id,allocation_batch_id,intent_id,intent_version,candidate_id,
       instrument_code,rank,action,requested_amount_ceiling,allocated_amount_cap,
       evidence,created_at,expires_at)
      VALUES (:decision,'batch',:id,0,:candidate,'600000.SH',:rank,'ALLOW',1000,1000,
        '{}',now(),now()+interval '15 seconds')
    """),
      {
        "decision": "decision-" + name,
        "id": name,
        "rank": rank,
        "candidate": "c" + str(rank),
      },
    )
    connection.execute(
      sa.text("""
      UPDATE trade_intents SET allocation_decision_id=:decision,
        allocation_version=1,status='AWAITING_APPROVAL' WHERE id=:id
    """),
      {"decision": "decision-" + name, "id": name},
    )

  with connection.begin_nested() as half:
    decision("one", 1)
    with pytest.raises(sa.exc.DBAPIError, match="T_ALLOCATION_UNCOMMITTED_DECISIONS"):
      connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    half.rollback()
  assert (
    connection.scalar(
      sa.text("SELECT allocation_version FROM trade_intents WHERE id='one'")
    )
    == 0
  )
  decision("one", 1)
  decision("two", 2)
  connection.exec_driver_sql("""
    UPDATE t_allocation_batches SET status='COMMITTED',committed_at=now(),
      decision_manifest_hash=repeat('c',64)
  """)
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  reject(
    "UPDATE t_allocation_decisions SET allocated_amount_cap=999",
    "T_ALLOCATION_DECISION_IMMUTABLE",
  )
  reject("DELETE FROM t_allocation_decisions", "T_ALLOCATION_DECISION_IMMUTABLE")
  reject(
    "UPDATE t_allocation_batches SET status='PREPARED'",
    "T_ALLOCATION_HISTORY_IMMUTABLE",
  )
  reject(
    "UPDATE trade_intents SET allocation_version=0 WHERE id='one'",
    "T_ALLOCATION_INTENT_VERSION_CONFLICT",
  )
  reject(
    "UPDATE trade_intents SET allocation_cycle_id='cycle' WHERE id='one'",
    "T_ALLOCATION_INTENT_CYCLE_IMMUTABLE",
  )
  reject(
    "UPDATE trade_intents SET instrument_code='000001.SZ' WHERE id='one'",
    "T_ALLOCATION_INTENT_MATERIAL_IMMUTABLE",
  )
  reject(
    "UPDATE trade_intents SET metadata='{\"candidate_id\":\"different\"}' WHERE id='one'",
    "T_ALLOCATION_INTENT_MATERIAL_IMMUTABLE",
  )
  reject(
    """
    INSERT INTO trade_intents
    SELECT (jsonb_populate_record(NULL::trade_intents, to_jsonb(i) ||
      '{"id":"forged","idempotency_key":"forged","allocation_decision_id":"decision-one"}')).*
    FROM trade_intents i WHERE id='two'
  """,
    "T_ALLOCATION_INITIAL_INTENT_INVALID",
  )
  assert connection.scalar(sa.text("SELECT count(*) FROM t_allocation_decisions")) == 2


@pytest.mark.asyncio
async def test_p4_actual_postgresql_migration_gate():
  engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
  schema = "quantx_p4_gate_" + uuid.uuid4().hex
  try:
    async with engine.connect() as connection:
      transaction = await connection.begin()
      try:
        await connection.run_sync(_p4_gate, schema)
      finally:
        await transaction.rollback()
        assert not await connection.scalar(
          sa.text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
          {"schema": schema},
        )
  finally:
    await engine.dispose()
