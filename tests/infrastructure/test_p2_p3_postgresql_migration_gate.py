"""Opt-in real PostgreSQL migration gate; every object and row is rolled back.

Run with QUANTX_RUN_MIGRATION_GATE=true. The root conftest enforces a dedicated
test database. A random schema and schema-only search_path additionally isolate
this test from all existing tables, including the test database's public schema.
"""

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL migration gate opt-in required",
)


def _run_gate(connection, schema):
  connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
  connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
  connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
  assert connection.scalar(sa.text("SELECT current_schema()")) == schema
  script = ScriptDirectory(
    str(Path(__file__).parents[2] / "packages/infrastructure/alembic")
  )
  revisions = list(
    reversed(list(script.walk_revisions(base="base", head="20260906_0050")))
  )
  with Operations.context(MigrationContext.configure(connection)):
    for revision in revisions:
      if revision.revision == "20260903_0049":
        _test_orphan_diagnostic_cleanup(connection, revision.module)
      revision.module.upgrade()
      if revision.revision == "20260903_0047":
        connection.exec_driver_sql("""
          INSERT INTO t_trade_global_configs
          (id, account_id, enabled, mode, auto_exit_acknowledged,
           ignored_stock_codes, settings, config_version, universe_revision,
           created_at, updated_at)
          VALUES ('gate-config', 'gate-account', false, 'paper', false,
            '["600000.SH"]', '{"signal_policy":{"policy_version":"gate-v1"}}',
            2, 0, now(), now())
        """)
        connection.exec_driver_sql("""
          INSERT INTO auth_users
          (id, username, display_name, password_hash, is_active, permissions,
           created_at, updated_at)
          VALUES ('gate-user', 'migration-gate', 'Migration gate', 'disabled',
            false, '[]', now(), now())
        """)
        connection.exec_driver_sql("""
          INSERT INTO pending_trade_orders
          (client_order_id, user_id, account_id, owner_type, owner_id, environment,
           instrument_code, side, order_type, limit_price, volume, status,
           intent_id, bucket, t_trade_role, request_metadata, last_source_sequence,
           created_at, updated_at)
          VALUES ('original-order', 'gate-user', 'gate-account', 'EXIT_PLAN',
            'gate-exit-plan', 'PAPER', '600000.SH', 'SELL', 'FIX_PRICE', '10',
            100, 'CANCELLED', 'gate-exit-intent', 'swing', 'EXIT', '{}', 0, now(), now())
        """)
  version = (
    connection.execute(sa.text("SELECT * FROM t_assistant_config_versions"))
    .mappings()
    .one()
  )
  assert version["canonical_payload"]["universe_policy"]["ignored_stock_codes"] == [
    "600000.SH"
  ]
  assert version["model_runtime_binding"] is None
  assert version["version"] == 2
  assert version["policy_version"] == "gate-v1"
  assert (
    connection.scalar(
      sa.text("SELECT active_config_version_id FROM t_trade_global_configs")
    )
    == version["config_version_id"]
  )

  def reject(sql, expected):
    with connection.begin_nested() as savepoint:
      with pytest.raises(sa.exc.DBAPIError, match=expected):
        for statement in (sql,) if isinstance(sql, str) else sql:
          connection.exec_driver_sql(statement)
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
      savepoint.rollback()

  original = connection.execute(
    sa.text(
      "SELECT t_order_attempt, t_order_original_created_at FROM pending_trade_orders"
    )
  ).one()
  assert original == (0, None)
  reject(
    "UPDATE pending_trade_orders SET t_order_attempt=-1",
    "ck_t_order_attempt_nonnegative",
  )
  reject(
    "UPDATE pending_trade_orders SET t_order_parent_client_id='missing'",
    "fk_t_order_parent",
  )
  reject(
    'INSERT INTO pending_trade_orders SELECT (jsonb_populate_record(NULL::pending_trade_orders, to_jsonb(p) || \'{"client_order_id":"duplicate"}\')).* FROM pending_trade_orders p',
    "uq_t_order_intent_attempt",
  )
  connection.exec_driver_sql(
    'INSERT INTO pending_trade_orders SELECT (jsonb_populate_record(NULL::pending_trade_orders, to_jsonb(p) || \'{"client_order_id":"replacement","t_order_attempt":1,"t_order_parent_client_id":"original-order"}\')).* FROM pending_trade_orders p'
  )
  reject(
    'INSERT INTO pending_trade_orders SELECT (jsonb_populate_record(NULL::pending_trade_orders, to_jsonb(p) || \'{"client_order_id":"duplicate-parent","intent_id":"another-intent"}\')).* FROM pending_trade_orders p WHERE client_order_id=\'replacement\'',
    "uq_t_order_parent_attempt",
  )

  connection.exec_driver_sql("""
    INSERT INTO account_risk_increase_admission_batches
    (admission_batch_id, account_id, environment, attempt, policy_version,
     account_snapshot_id, account_snapshot_hash, obligation_watermark,
     input_fingerprint, intent_manifest_hash, status, expires_at, created_at, updated_at)
    SELECT 'batch-' || n, 'gate-account', 'LIVE', n, 'gate-v1', 'snapshot',
      repeat('a',64), repeat('b',64), repeat('c',64), repeat('d',64),
      'PREPARED', now() + interval '1 hour', now(), now()
    FROM generate_series(1,3) n
  """)
  connection.exec_driver_sql("""
    INSERT INTO trade_intents
    (id, owner_type, owner_id, environment, idempotency_key, account_id,
     instrument_code, direction, bucket, reason, priority, confidence, status,
     metadata, created_at, updated_at)
    VALUES ('intent', 'MANUAL_COMMAND', 'gate-owner', 'LIVE', 'gate-key',
      'gate-account', '600000.SH', 'BUY', 'core', 'gate', 'NORMAL', 1,
      'PENDING', '{}', now(), now())
  """)
  connection.exec_driver_sql("""
    UPDATE trade_intents SET admission_batch_id='batch-1', admission_rank=1,
      admission_policy_version='gate-v1', admission_input_fingerprint=repeat('c',64)
    WHERE id='intent'
  """)
  connection.exec_driver_sql("""
    INSERT INTO account_risk_increase_admission_items
    (admission_item_id, admission_batch_id, intent_id, admission_rank,
     owner_type, owner_id, intent_created_at)
    SELECT 'item-1', 'batch-1', id, 1, owner_type, owner_id, created_at
    FROM trade_intents WHERE id='intent'
  """)
  # Deferred validation must inspect the current row, not INSERT's stale NEW.
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  reject(
    "UPDATE account_risk_increase_admission_items SET owner_id='wrong'",
    "RISK_ADMISSION_ITEM_IMMUTABLE",
  )
  reject(
    "UPDATE trade_intents SET admission_rank=2", "RISK_ADMISSION_INTENT_ITEM_CONFLICT"
  )
  reject(
    """INSERT INTO account_risk_increase_admission_items
    SELECT 'bad-owner', admission_batch_id, intent_id, admission_rank,
      owner_type, 'wrong-owner', intent_created_at
    FROM account_risk_increase_admission_items""",
    "RISK_ADMISSION_ITEM_INTENT_CONFLICT",
  )
  clear_admission = (
    "UPDATE trade_intents SET admission_batch_id=NULL, admission_rank=NULL, "
    "admission_policy_version=NULL, admission_input_fingerprint=NULL"
  )
  assign_second = (
    "UPDATE trade_intents SET admission_batch_id='batch-2', admission_rank=1, "
    "admission_policy_version='gate-v1', admission_input_fingerprint=repeat('c',64)"
  )
  insert_second = """
    INSERT INTO account_risk_increase_admission_items
    (admission_item_id, admission_batch_id, intent_id, admission_rank,
     owner_type, owner_id, intent_created_at)
    SELECT 'item-2', 'batch-2', id, 1, owner_type, owner_id, created_at
    FROM trade_intents WHERE id='intent'
  """
  reject(clear_admission, "RISK_ADMISSION_INTENT_ITEM_CONFLICT")
  reject((assign_second, insert_second), "RISK_ADMISSION_INTENT_ITEM_CONFLICT")
  connection.exec_driver_sql(
    "UPDATE account_risk_increase_admission_batches SET status='SUPERSEDED' WHERE admission_batch_id='batch-1'"
  )
  connection.exec_driver_sql(clear_admission)
  # Force all deferred checks at each application transaction boundary while
  # retaining the outer rollback that owns the entire isolated schema.
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  connection.exec_driver_sql(assign_second)
  connection.exec_driver_sql(insert_second)
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  connection.exec_driver_sql(
    "UPDATE account_risk_increase_admission_batches SET status='COMMITTED', committed_at=now() WHERE admission_batch_id='batch-2'"
  )
  connection.exec_driver_sql(clear_admission)
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  connection.exec_driver_sql(assign_second.replace("batch-2", "batch-3"))
  connection.exec_driver_sql(
    insert_second.replace("batch-2", "batch-3").replace("item-2", "item-3")
  )
  connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
  connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
  for assignment in ("owner_id='wrong'", "intent_id='other'", "admission_rank=2"):
    reject(
      f"UPDATE account_risk_increase_admission_items SET {assignment} WHERE admission_item_id='item-2'",
      "RISK_ADMISSION_ITEM_IMMUTABLE",
    )
  reject(
    "DELETE FROM account_risk_increase_admission_items WHERE admission_item_id='item-2'",
    "RISK_ADMISSION_ITEM_IMMUTABLE",
  )
  reject(
    "UPDATE account_risk_increase_admission_batches SET status='PREPARED' WHERE admission_batch_id='batch-2'",
    "RISK_ADMISSION_BATCH_TERMINAL_IMMUTABLE",
  )
  reject(
    """INSERT INTO account_risk_increase_admission_items
    SELECT 'late-history', admission_batch_id, intent_id, admission_rank,
      owner_type, owner_id, intent_created_at
    FROM account_risk_increase_admission_items WHERE admission_item_id='item-2'""",
    "RISK_ADMISSION_ITEM_BATCH_NOT_PREPARED",
  )
  assert (
    connection.scalar(
      sa.text(
        "SELECT count(*) FROM account_risk_increase_admission_items WHERE intent_id='intent'"
      )
    )
    == 3
  )

  for action in (
    "UPDATE t_assistant_config_versions SET version=3",
    "DELETE FROM t_assistant_config_versions",
  ):
    reject(action, "T_ASSISTANT_APPEND_ONLY_FACT_IMMUTABLE")

  connection.exec_driver_sql("""
    INSERT INTO t_assistant_executions
    (execution_id, config_id, config_version_id, frozen_config_version,
     config_snapshot_hash, account_id, environment, entry_authorization,
     rollout_stage, status, entry_readiness, entry_readiness_reasons,
     entry_readiness_as_of, policy_version, feature_schema_version, scorer_mode,
     model_runtime_binding, universe_revision, last_assigned_cycle_sequence,
     last_committed_cycle_sequence, checkpoint_revision, state_version)
    SELECT 'gate-execution', config_id, config_version_id, version,
      config_snapshot_hash, 'gate-account', 'LIVE', entry_authorization,
      rollout_stage, 'CREATED', 'BLOCKED', '[]', now(), policy_version,
      feature_schema_version, scorer_mode, model_runtime_binding, 0, 0, 0, 0, 1
    FROM t_assistant_config_versions
  """)
  reject(
    "UPDATE t_assistant_executions SET state_version=2, config_snapshot_hash=repeat('0',64)",
    "T_ASSISTANT_EXECUTION_CONFIG_BINDING_INVALID",
  )
  reject(
    "UPDATE t_assistant_executions SET status='WARMING'",
    "T_ASSISTANT_EXECUTION_STATE_VERSION_INVALID",
  )
  reject(
    "UPDATE t_assistant_executions SET state_version=2, status='RUNNING', entry_readiness='READY'",
    "T_ASSISTANT_EXECUTION_TRANSITION_INVALID",
  )
  connection.exec_driver_sql(
    "UPDATE t_assistant_executions SET state_version=2, status='WARMING'"
  )
  reject(
    'INSERT INTO t_assistant_executions SELECT (jsonb_populate_record(NULL::t_assistant_executions, to_jsonb(e) || \'{"execution_id":"second"}\')).* FROM t_assistant_executions e',
    "uq_t_assistant_execution_live_entry_producer",
  )
  connection.exec_driver_sql("""
    INSERT INTO t_assistant_execution_events
    (event_id, execution_id, event_key, event_type, occurred_at, payload)
    VALUES ('event', 'gate-execution', 'event', 'CREATED', now(), '{}')
  """)
  reject(
    "DELETE FROM t_assistant_execution_events", "T_ASSISTANT_APPEND_ONLY_FACT_IMMUTABLE"
  )
  reject(
    "UPDATE t_assistant_execution_events SET event_type='UPDATED'",
    "T_ASSISTANT_APPEND_ONLY_FACT_IMMUTABLE",
  )
  connection.exec_driver_sql("""
    INSERT INTO t_assistant_decision_cycles
    (cycle_id, execution_id, cycle_sequence, decision_key, attempt, snapshot_hash,
     fence_from, fence_to, market_delta_manifest_hash, reducer_cursor_manifest_hash,
     evaluated_symbol_count, material_symbol_count, proposed_intent_count, status,
     input_manifest_hash, input_manifest, prepared_at)
    VALUES ('cycle', 'gate-execution', 1, repeat('a',64), 1, repeat('b',64),
      0, 1, repeat('c',64), repeat('d',64), 1, 1, 0, 'PREPARED', repeat('e',64), '{}', now())
  """)
  for assignment, constraint in (
    ("fence_to=-1", "fence_shape"),
    ("decision_key='short'", "hash_shape"),
    ("processing_owner='worker'", "claim_shape"),
    ("status='PROPOSALS_COMMITTED'", "terminal_shape"),
    ("material_symbol_count=2", "material_count"),
  ):
    reject(f"UPDATE t_assistant_decision_cycles SET {assignment}", constraint)
  reject(
    'INSERT INTO t_assistant_executions SELECT (jsonb_populate_record(NULL::t_assistant_executions, to_jsonb(e) || \'{"execution_id":"wrong-account","account_id":"other"}\')).* FROM t_assistant_executions e',
    "T_ASSISTANT_EXECUTION_CONFIG_BINDING_INVALID",
  )


def _test_orphan_diagnostic_cleanup(connection, migration):
  # Exercise actual SQL without changing the surrounding migration fixture.
  with connection.begin_nested() as savepoint:
    for identifier, candidate, event, payload in (
      ("prunable", None, "STATE_INITIALIZED", "{}"),
      ("candidate", "candidate-1", "STATE_INITIALIZED", "{}"),
      ("unknown-event", None, "CANDIDATE_CREATED", "{}"),
      ("pending-intent", None, "STATE_INITIALIZED",
       '{"signal_snapshot":{"pending_entry_intent_id":"intent-1"}}'),
      ("trade-linked", None, "STATE_INITIALIZED", "{}"),
    ):
      connection.execute(sa.text("""
        INSERT INTO t_trade_opportunity_evaluations
        (id,event_key,account_id,strategy_run_id,instrument_code,candidate_id,
         evaluated_at,record_kind,event_type,coalesced_count,policy_version,
         schema_version,content_fingerprint,payload,metrics,created_at)
        VALUES (:id,:id,'gate-account',:run,'600000.SH',:candidate,
          now(),'MATERIAL',:event,1,'policy','1',repeat('a',64),
          CAST(:payload AS json),'{}',now())
      """), {"id": identifier, "run": "missing-" + identifier,
             "candidate": candidate, "event": event, "payload": payload})
    connection.exec_driver_sql("""
      INSERT INTO trade_intents
      (id,owner_type,owner_id,environment,idempotency_key,account_id,
       instrument_code,direction,bucket,reason,priority,confidence,status,
       metadata,created_at,updated_at)
      VALUES ('protected-intent','STRATEGY_RUN','missing-trade-linked','PAPER',
        'protected-key','gate-account','600000.SH','BUY','swing','gate','NORMAL',
        1,'PENDING','{}',now(),now())
    """)
    assert migration._prune_orphaned_opportunity_diagnostics() == 1
    assert set(connection.scalars(sa.text(
      "SELECT id FROM t_trade_opportunity_evaluations"
    ))) == {"candidate", "unknown-event", "pending-intent", "trade-linked"}
    # Remaining unproven facts must still stop the migration, not get guessed.
    with connection.begin_nested() as rejected:
      with pytest.raises(sa.exc.DBAPIError, match="opportunity_owner_unproven"):
        migration._preflight_legacy_evidence()
      rejected.rollback()
    assert migration._prune_orphaned_opportunity_diagnostics() == 0
    savepoint.rollback()
  assert connection.scalar(sa.text(
    "SELECT count(*) FROM t_trade_opportunity_evaluations"
  )) == 0


@pytest.mark.asyncio
async def test_p2_p3_actual_postgresql_migration_gate():
  engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
  schema = "quantx_migration_gate_" + uuid.uuid4().hex
  try:
    async with engine.connect() as connection:
      transaction = await connection.begin()
      try:
        await connection.run_sync(_run_gate, schema)
      finally:
        await transaction.rollback()
        assert not await connection.scalar(
          sa.text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=:schema)"),
          {"schema": schema},
        )
  finally:
    await engine.dispose()
