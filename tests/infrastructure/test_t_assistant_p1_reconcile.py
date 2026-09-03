"""Focused tests for the fail-closed P1 execution-owner maintenance tool."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from quantx_infrastructure.services.t_assistant_p1_reconcile import (
  APPROVAL_REASON,
  CONFIRMATION_WORD,
  EXIT_INTENT_REASON,
  PLAN_REASON,
  TERMINAL_EXIT_PLAN_UNROUTED_INTENT,
  apply_reconciliation,
  main,
  render_markdown,
  run_inspection,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
SENTINELS = ("acct-SENSITIVE", "run-SENSITIVE", "intent-SENSITIVE", "plan-SENSITIVE")

_DDL = (
  "CREATE TABLE strategies (id INTEGER PRIMARY KEY, class_name TEXT)",
  "CREATE TABLE strategy_runs (id TEXT PRIMARY KEY, strategy_id INTEGER, status TEXT, mode TEXT)",
  "CREATE TABLE trade_intents ("
  "id TEXT PRIMARY KEY, strategy_run_id TEXT, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, "
  "environment TEXT NOT NULL, idempotency_key TEXT NOT NULL, account_id TEXT, instrument_code TEXT, status TEXT, direction TEXT, "
  "executed_price REAL, executed_volume INTEGER, executed_time TEXT, order_id TEXT, "
  "metadata TEXT, notes TEXT, created_at TEXT, updated_at TEXT)",
  "CREATE TABLE pending_trade_orders ("
  "client_order_id TEXT PRIMARY KEY, strategy_run_id TEXT, intent_id TEXT, batch_id TEXT, owner_type TEXT, owner_id TEXT, account_id TEXT, instrument_code TEXT, request_metadata TEXT)",
  "CREATE TABLE order_correlations ("
  "client_order_id TEXT PRIMARY KEY, strategy_run_id TEXT, intent_id TEXT, batch_id TEXT, owner_type TEXT, owner_id TEXT, account_id TEXT, instrument_code TEXT, request_metadata TEXT)",
  "CREATE TABLE trade_command_outbox (client_order_id TEXT PRIMARY KEY, owner_type TEXT, owner_id TEXT, account_id TEXT, payload TEXT)",
  "CREATE TABLE strategy_runtime_events ("
  "event_id TEXT PRIMARY KEY, strategy_run_id TEXT, client_order_id TEXT, owner_type TEXT, owner_id TEXT, payload TEXT)",
  "CREATE TABLE t_trade_batches (batch_id TEXT PRIMARY KEY)",
  "CREATE TABLE auto_exit_plans ("
  "plan_id TEXT PRIMARY KEY, account_id TEXT, instrument_code TEXT, source_type TEXT, source_id TEXT, "
  "strategy_run_id TEXT, enabled INTEGER, status TEXT, environment TEXT, remaining_volume INTEGER, "
  "pending_client_order_id TEXT, auto_exit_authorized INTEGER, auto_exit_authorization_fingerprint TEXT, "
  "auto_exit_authorization_config_version INTEGER, auto_exit_authorized_at TEXT, "
  "auto_exit_authorization_expires_at TEXT, auto_exit_authorization_challenge_id TEXT, "
  "auto_exit_authorization_user_id TEXT, auto_exit_authorization_device_session_id TEXT, "
  "config_version INTEGER, state_version INTEGER, plan_state TEXT, last_error TEXT, updated_at TEXT)",
  "CREATE TABLE auto_exit_plan_events ("
  "event_id TEXT PRIMARY KEY, business_key TEXT UNIQUE, plan_id TEXT, event_type TEXT, payload TEXT, created_at TEXT)",
  "CREATE TABLE agent_report_inbox (message_id TEXT PRIMARY KEY, processing_status TEXT)",
)


def _plan_state(
  *,
  plan_id: str = "plan-1",
  source_id: str = "batch-missing",
  account_id: str = "acct-1",
  instrument_code: str = "000001.SZ",
  status: str = "ERROR",
  run_id: str = "",
  error: str = "legacy-error",
) -> dict[str, object]:
  return {
    "template": {
      "plan_id": plan_id,
      "source_type": "T_TRADE_BATCH",
      "source_id": source_id,
      "account_id": account_id,
      "instrument_code": instrument_code,
      "bucket": "swing",
      "rules": [{"strategy": "TARGET_PRICE", "parameters": {"target_price": 12}}],
      "run_id": run_id,
      "config_version": 7,
    },
    "status": status,
    "entry_filled_volume": 200,
    "exited_volume": 0,
    "error_message": error,
  }


@pytest.fixture
async def database() -> AsyncEngine:
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  async with engine.begin() as connection:
    for statement in _DDL:
      await connection.execute(text(statement))
  yield engine
  await engine.dispose()


@pytest.fixture
async def legacy_database() -> AsyncEngine:
  legacy_ddl = tuple(
    statement
    .replace("CREATE TABLE trade_intents", "CREATE TABLE strategy_trade_intents")
    .replace("CREATE TABLE order_correlations", "CREATE TABLE strategy_order_correlations")
    .replace(
      "environment TEXT NOT NULL, idempotency_key TEXT NOT NULL, account_id TEXT,",
      "idempotency_key TEXT NOT NULL, account_id TEXT,",
    )
    .replace(
      "status TEXT, direction TEXT,", "status TEXT, direction TEXT,"
    )
    .replace(
      "strategy_run_id TEXT, enabled INTEGER, status TEXT, environment TEXT, remaining_volume INTEGER,",
      "strategy_run_id TEXT, enabled INTEGER, status TEXT, execution_mode TEXT, remaining_volume INTEGER,",
    )
    .replace(
      ", owner_type TEXT, owner_id TEXT, account_id TEXT, instrument_code TEXT, request_metadata TEXT",
      ", request_metadata TEXT",
    )
    .replace(
      "client_order_id TEXT PRIMARY KEY, owner_type TEXT, owner_id TEXT, account_id TEXT, payload TEXT",
      "client_order_id TEXT PRIMARY KEY, payload TEXT",
    )
    .replace(
      "client_order_id TEXT, owner_type TEXT, owner_id TEXT, payload TEXT",
      "client_order_id TEXT, payload TEXT",
    )
  for statement in _DDL
  )
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  async with engine.begin() as connection:
    for statement in legacy_ddl:
      await connection.execute(text(statement))
  yield engine
  await engine.dispose()


async def _insert_approval(
  connection,
  *,
  intent_id: str,
  run_id: str,
  direction: str = "BUY",
  run_status: str = "ERROR",
  run_mode: str = "paper",
  created_at: datetime = NOW - timedelta(days=1),
  metadata: dict[str, object] | None = None,
  executed_price: float | None = None,
  executed_volume: int | None = None,
  executed_time: str | None = None,
  order_id: str | None = None,
) -> None:
  await connection.execute(
    text(
      "INSERT INTO strategy_runs (id, strategy_id, status, mode) "
      "VALUES (:id, 1, :status, :mode)"
    ),
    {"id": run_id, "status": run_status, "mode": run_mode},
  )
  await connection.execute(
    text(
      "INSERT INTO trade_intents "
      "(id, strategy_run_id, owner_type, owner_id, environment, idempotency_key, status, direction, executed_price, executed_volume, "
      "executed_time, order_id, metadata, created_at) "
      "VALUES (:id, :run_id, 'STRATEGY_RUN', :run_id, 'PAPER', :idempotency_key, 'AWAITING_APPROVAL', :direction, :executed_price, "
      ":executed_volume, :executed_time, :order_id, :metadata, :created_at)"
    ),
    {
      "id": intent_id,
      "run_id": run_id,
      "idempotency_key": f"legacy:{intent_id}",
      "direction": direction,
      "executed_price": executed_price,
      "executed_volume": executed_volume,
      "executed_time": executed_time,
      "order_id": order_id,
      "metadata": json.dumps(metadata or {"approval_ttl_ms": 30_000}),
      "created_at": created_at.replace(tzinfo=None).isoformat(),
    },
  )


async def _seed_associated_run(
  engine: AsyncEngine,
  *,
  run_id: str,
  status: str = "ERROR",
  mode: str = "paper",
  class_name: str = "AshareIntradayTAssistantStrategy",
) -> None:
  async with engine.begin() as connection:
    strategy_id = 2 if class_name != "AshareIntradayTAssistantStrategy" else 1
    if strategy_id == 2:
      await connection.execute(
        text("INSERT INTO strategies (id, class_name) VALUES (2, :class_name)"),
        {"class_name": class_name},
      )
    await connection.execute(
      text(
        "INSERT INTO strategy_runs (id, strategy_id, status, mode) "
        "VALUES (:id, :strategy_id, :status, :mode)"
      ),
      {"id": run_id, "strategy_id": strategy_id, "status": status, "mode": mode},
    )


async def _seed_base(
  engine: AsyncEngine,
  *,
  direction: str = "BUY",
  intent_id: str = "intent-1",
  run_id: str = "run-1",
  run_status: str = "ERROR",
  run_mode: str = "paper",
  created_at: datetime = NOW - timedelta(days=1),
  metadata: dict[str, object] | None = None,
) -> None:
  async with engine.begin() as connection:
    await connection.execute(
      text("INSERT INTO strategies (id, class_name) VALUES (1, :class_name)"),
      {"class_name": "AshareIntradayTAssistantStrategy"},
    )
    await _insert_approval(
      connection,
      intent_id=intent_id,
      run_id=run_id,
      direction=direction,
      run_status=run_status,
      run_mode=run_mode,
      created_at=created_at,
      metadata=metadata,
    )


async def _seed_plan(
  engine: AsyncEngine,
  *,
  plan_id: str = "plan-1",
  source_id: str = "batch-missing",
  enabled: int = 0,
  status: str = "ERROR",
  environment: str = "paper",
  strategy_run_id: str | None = "run-orphan",
  state: dict[str, object] | None = None,
  authorization: int = 0,
) -> None:
  plan_state = state or _plan_state(
    plan_id=plan_id,
    source_id=source_id,
    run_id=strategy_run_id or "",
  )
  async with engine.begin() as connection:
    await connection.execute(
      text(
        "INSERT INTO auto_exit_plans "
        "(plan_id, account_id, instrument_code, source_type, source_id, strategy_run_id, enabled, status, "
        "environment, remaining_volume, pending_client_order_id, auto_exit_authorized, "
        "config_version, state_version, plan_state, last_error) "
        "VALUES (:plan_id, 'acct-1', '000001.SZ', 'T_TRADE_BATCH', :source_id, :run_id, :enabled, :status, "
        ":environment, 200, NULL, :authorization, 7, 4, :state, 'legacy-error')"
      ),
      {
        "plan_id": plan_id,
        "source_id": source_id,
        "run_id": strategy_run_id,
        "enabled": enabled,
        "status": status,
        "environment": environment,
        "authorization": authorization,
        "state": json.dumps(plan_state),
      },
    )


async def _seed_exit_intent(
  engine: AsyncEngine,
  *,
  plan_id: str = "plan-exit",
  intent_id: str = "intent-exit",
  plan_status: str = "CANCELLED",
  plan_environment: str = "paper",
  intent_environment: str | None = None,
  intent_status: str = "PENDING",
  direction: str = "SELL",
  account_id: str = "acct-1",
  instrument_code: str = "000001.SZ",
  intent_account_id: str | None = None,
  intent_instrument_code: str | None = None,
  order_id: str | None = None,
  executed_volume: int | None = None,
  pending_intent_id: str | None = None,
  metadata: dict[str, object] | None = None,
) -> None:
  state = _plan_state(
    plan_id=plan_id,
    account_id=account_id,
    instrument_code=instrument_code,
    status=plan_status,
  )
  if pending_intent_id is not None:
    state["pending_intent_id"] = pending_intent_id
  await _seed_plan(
    engine,
    plan_id=plan_id,
    status=plan_status,
    environment=plan_environment,
    state=state,
  )
  async with engine.begin() as connection:
    await connection.execute(
      text(
        "INSERT INTO trade_intents "
        "(id, strategy_run_id, owner_type, owner_id, environment, idempotency_key, "
        "account_id, instrument_code, status, direction, executed_volume, order_id, metadata) "
        "VALUES (:intent_id, NULL, 'EXIT_PLAN', :plan_id, :environment, :key, "
        ":account_id, :instrument_code, :status, :direction, :executed_volume, :order_id, :metadata)"
      ),
      {
        "intent_id": intent_id,
        "plan_id": plan_id,
        "environment": intent_environment or plan_environment.upper(),
        "key": f"strategy-exit:{plan_id}:{intent_id}",
        "account_id": intent_account_id or account_id,
        "instrument_code": intent_instrument_code or instrument_code,
        "status": intent_status,
        "direction": direction,
        "executed_volume": executed_volume,
        "order_id": order_id,
        "metadata": json.dumps(metadata or {}),
      },
    )


async def _seed_legacy_exit_intent(engine: AsyncEngine) -> None:
  state = _plan_state(
    plan_id="plan-legacy-exit",
    status="CANCELLED",
  )
  async with engine.begin() as connection:
    await connection.execute(
      text(
        "INSERT INTO auto_exit_plans "
        "(plan_id, account_id, instrument_code, source_type, source_id, strategy_run_id, "
        "enabled, status, execution_mode, remaining_volume, pending_client_order_id, "
        "auto_exit_authorized, config_version, state_version, plan_state, last_error) "
        "VALUES ('plan-legacy-exit', 'acct-1', '000001.SZ', 'T_TRADE_BATCH', 'batch-missing', NULL, "
        "0, 'CANCELLED', 'paper', 200, NULL, 0, 7, 4, :state, 'legacy-error')"
      ),
      {"state": json.dumps(state)},
    )
    await connection.execute(
      text(
        "INSERT INTO strategy_trade_intents "
        "(id, strategy_run_id, owner_type, owner_id, idempotency_key, account_id, "
        "instrument_code, status, direction, executed_volume, order_id, metadata) "
        "VALUES ('intent-legacy-exit', NULL, 'EXIT_PLAN', 'plan-legacy-exit', "
        "'strategy-exit:plan-legacy-exit:intent-legacy-exit', 'acct-1', '000001.SZ', "
        "'PENDING', 'SELL', NULL, NULL, '{}')"
      )
    )


async def _insert_approval_durable_link(
  connection,
  *,
  kind: str,
  intent_id: str,
  run_id: str,
) -> None:
  if kind in {"pending", "correlation"}:
    table = "pending_trade_orders" if kind == "pending" else "order_correlations"
    await connection.execute(
      text(
        f"INSERT INTO {table} "
        "(client_order_id, strategy_run_id, intent_id, batch_id, request_metadata) "
        "VALUES ('client-blocked', :run_id, :intent_id, NULL, NULL)"
      ),
      {"run_id": run_id, "intent_id": intent_id},
    )
  elif kind == "outbox":
    await connection.execute(
      text("INSERT INTO trade_command_outbox (client_order_id, payload) VALUES (NULL, :payload)"),
      {"payload": json.dumps({"intent_id": intent_id})},
    )
  else:
    await connection.execute(
      text(
        "INSERT INTO strategy_runtime_events "
        "(event_id, strategy_run_id, client_order_id, payload) "
        "VALUES ('event-blocked', :run_id, NULL, :payload)"
      ),
      {"run_id": run_id, "payload": json.dumps({"metadata": {"intent_id": intent_id}})},
    )


async def _insert_exit_durable_link(
  connection,
  *,
  kind: str,
  intent_id: str,
) -> None:
  if kind == "pending":
    await connection.execute(
      text(
        "INSERT INTO pending_trade_orders "
        "(client_order_id, intent_id, request_metadata) "
        "VALUES ('exit-client', :intent_id, NULL)"
      ),
      {"intent_id": intent_id},
    )
  elif kind == "correlation":
    await connection.execute(
      text(
        "INSERT INTO order_correlations "
        "(client_order_id, intent_id, request_metadata) "
        "VALUES ('exit-client', :intent_id, NULL)"
      ),
      {"intent_id": intent_id},
    )
  elif kind == "outbox":
    await connection.execute(
      text(
        "INSERT INTO trade_command_outbox (client_order_id, payload) "
        "VALUES ('exit-client', :payload)"
      ),
      {"payload": json.dumps({"intent_id": intent_id})},
    )
  elif kind == "runtime_client":
    await connection.execute(
      text(
        "INSERT INTO strategy_runtime_events "
        "(event_id, client_order_id, payload) "
        "VALUES ('exit-event', :intent_id, :payload)"
      ),
      {"intent_id": intent_id, "payload": json.dumps({})},
    )
  elif kind == "runtime_payload":
    await connection.execute(
      text(
        "INSERT INTO strategy_runtime_events "
        "(event_id, client_order_id, payload) "
        "VALUES ('exit-event', 'unrelated-client', :payload)"
      ),
      {"payload": json.dumps({"intent_id": intent_id})},
    )
  elif kind == "nested_metadata":
    await connection.execute(
      text(
        "INSERT INTO trade_command_outbox (client_order_id, payload) "
        "VALUES ('exit-client', :payload)"
      ),
      {"payload": json.dumps({"metadata": {"intent_id": intent_id}})},
    )
  elif kind == "owner_metadata":
    await connection.execute(
      text(
        "INSERT INTO trade_command_outbox (client_order_id, payload) "
        "VALUES ('exit-client', :payload)"
      ),
      {"payload": json.dumps({"owner_type": "EXIT_PLAN", "owner_id": "plan-exit"})},
    )
  else:
    raise AssertionError(f"unsupported link kind: {kind}")


async def _assert_blocked_apply(
  database: AsyncEngine,
  *,
  kind: str,
  reason: str,
) -> None:
  report = await run_inspection(database, now=NOW)
  assert report["safeApprovalCount"] == 1
  assert report["safePlanCount"] == 1
  assert report[f"blocked{kind.title()}Count"] == 1
  assert reason in report["reasonCodes"]
  assert report["readyToApply"] is False

  result = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=1,
    expected_plan_count=1,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert result["applied"] is False
  assert reason in result["reasonCodes"]
  assert result["readyToApply"] is False
  async with database.connect() as connection:
    expected_intent_count = 2 if kind == "approval" else 1
    assert (
      await connection.execute(
        text("SELECT COUNT(*) FROM trade_intents WHERE status = 'AWAITING_APPROVAL'")
      )
    ).scalar_one() == expected_intent_count
    assert (
      await connection.execute(
        text("SELECT status FROM auto_exit_plans WHERE plan_id = 'plan-safe'")
      )
    ).scalar_one() == "ERROR"
    assert (
      await connection.execute(text("SELECT COUNT(*) FROM auto_exit_plan_events"))
    ).scalar_one() == 0


@pytest.mark.parametrize(
  ("case", "expected_reason"),
  (
    ("non_paper", "APPROVAL_NOT_PAPER"),
    ("not_expired", "APPROVAL_NOT_EXPIRED"),
    ("executed", "APPROVAL_EXECUTION_PRESENT"),
    ("order", "APPROVAL_ORDER_PRESENT"),
    ("durable_pending", "APPROVAL_DURABLE_CHAIN_PRESENT"),
    ("durable_correlation", "APPROVAL_DURABLE_CHAIN_PRESENT"),
    ("durable_outbox", "APPROVAL_DURABLE_CHAIN_PRESENT"),
    ("durable_runtime", "APPROVAL_DURABLE_CHAIN_PRESENT"),
  ),
)
async def test_blocked_approval_proofs_are_atomic(
  database: AsyncEngine,
  case: str,
  expected_reason: str,
) -> None:
  await _seed_base(database, intent_id="intent-safe")
  await _seed_plan(database, plan_id="plan-safe")
  kwargs: dict[str, object] = {}
  if case == "non_paper":
    kwargs["run_mode"] = "live"
  elif case == "not_expired":
    kwargs["created_at"] = NOW
  elif case == "executed":
    kwargs["executed_volume"] = 1
  elif case == "order":
    kwargs["order_id"] = "order-blocked"
  durable_kind = case.removeprefix("durable_") if case.startswith("durable_") else ""
  async with database.begin() as connection:
    await _insert_approval(
      connection,
      intent_id="intent-blocked",
      run_id="run-blocked",
      **kwargs,
    )
    if durable_kind:
      await _insert_approval_durable_link(
        connection,
        kind=durable_kind,
        intent_id="intent-blocked",
        run_id="run-blocked",
      )
  await _assert_blocked_apply(database, kind="approval", reason=expected_reason)


@pytest.mark.parametrize(
  ("case", "expected_reason"),
  (
    ("non_paper", "PLAN_NOT_PAPER"),
    ("enabled", "PLAN_ENABLED"),
    ("missing_run", "PLAN_STRATEGY_RUN_MISSING"),
    ("existing_error_run", "PLAN_STRATEGY_RUN_PRESENT"),
    ("existing_active_run", "PLAN_STRATEGY_RUN_PRESENT"),
    ("existing_live_run", "PLAN_STRATEGY_RUN_PRESENT"),
    ("existing_wrong_class_run", "PLAN_STRATEGY_RUN_PRESENT"),
    ("batch", "PLAN_SOURCE_BATCH_PRESENT"),
    ("pending", "PLAN_PENDING_IDENTITY_PRESENT"),
    ("authorization", "PLAN_AUTHORIZATION_PRESENT"),
    ("identity", "PLAN_IDENTITY_CONFLICT"),
  ),
)
async def test_blocked_plan_proofs_are_atomic(
  database: AsyncEngine,
  case: str,
  expected_reason: str,
) -> None:
  await _seed_base(database, intent_id="intent-safe")
  await _seed_plan(database, plan_id="plan-safe")
  kwargs: dict[str, object] = {"plan_id": "plan-blocked"}
  if case == "non_paper":
    kwargs["environment"] = "live"
  elif case == "enabled":
    kwargs["enabled"] = 1
  elif case == "missing_run":
    kwargs["strategy_run_id"] = None
  elif case in {
    "existing_error_run",
    "existing_active_run",
    "existing_live_run",
    "existing_wrong_class_run",
  }:
    run_id = {
      "existing_error_run": "run-existing-error",
      "existing_active_run": "run-existing-active",
      "existing_live_run": "run-existing-live",
      "existing_wrong_class_run": "run-existing-wrong-class",
    }[case]
    kwargs["strategy_run_id"] = run_id
    kwargs["state"] = _plan_state(plan_id="plan-blocked", run_id=run_id)
  elif case == "batch":
    kwargs["source_id"] = "batch-present"
  elif case == "pending":
    pending_state = _plan_state(plan_id="plan-blocked", run_id="run-orphan")
    pending_state["pending_intent_id"] = "pending-intent"
    kwargs["state"] = pending_state
  elif case == "authorization":
    kwargs["authorization"] = 1
  else:
    kwargs["state"] = _plan_state(plan_id="different-plan", run_id="run-orphan")
  if case == "existing_error_run":
    await _seed_associated_run(database, run_id="run-existing-error")
  elif case == "existing_active_run":
    await _seed_associated_run(database, run_id="run-existing-active", status="RUNNING")
  elif case == "existing_live_run":
    await _seed_associated_run(database, run_id="run-existing-live", mode="LIVE")
  elif case == "existing_wrong_class_run":
    await _seed_associated_run(
      database,
      run_id="run-existing-wrong-class",
      class_name="OtherStrategy",
    )
  await _seed_plan(database, **kwargs)
  if case == "batch":
    async with database.begin() as connection:
      await connection.execute(
        text("INSERT INTO t_trade_batches (batch_id) VALUES ('batch-present')")
      )
  await _assert_blocked_apply(database, kind="plan", reason=expected_reason)


async def test_dry_run_is_aggregate_read_only_and_ready(database: AsyncEngine) -> None:
  await _seed_base(database, run_mode="PAPER")
  await _seed_plan(database)

  report = await run_inspection(database, now=NOW)

  assert report["safeApprovalCount"] == 1
  assert report["safePlanCount"] == 1
  assert report["blockedApprovalCount"] == 0
  assert report["blockedPlanCount"] == 0
  assert report["readyToApply"] is True
  assert report["applied"] is False
  rendered = json.dumps(report, ensure_ascii=False) + render_markdown(report)
  assert all(sentinel not in rendered for sentinel in SENTINELS)
  async with database.connect() as connection:
    assert (await connection.execute(text("SELECT status FROM trade_intents"))).scalar_one() == (
      "AWAITING_APPROVAL"
    )
    assert (await connection.execute(text("SELECT COUNT(*) FROM auto_exit_plan_events"))).scalar_one() == 0


async def test_apply_requires_confirmation_and_exact_counts(database: AsyncEngine) -> None:
  with pytest.raises(Exception) as error:
    await apply_reconciliation(
      database,
      confirmation="wrong",
      expected_approval_count=0,
      expected_plan_count=0,
      expected_exit_intent_count=0,
      now=NOW,
    )
  assert getattr(error.value, "code", "") == "CONFIRMATION_REQUIRED"

  await _seed_base(database)
  report = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert report["applied"] is False
  assert "EXPECTED_APPROVAL_COUNT_MISMATCH" in report["reasonCodes"]


async def test_apply_mutates_both_records_and_is_idempotent(database: AsyncEngine) -> None:
  await _seed_base(database)
  await _seed_plan(database)

  first = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=1,
    expected_plan_count=1,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert first["applied"] is True
  async with database.connect() as connection:
    intent = (
      await connection.execute(
        text("SELECT status, owner_type, owner_id, metadata, notes FROM trade_intents")
      )
    ).one()
    plan = (
      await connection.execute(
        text("SELECT status, enabled, state_version, remaining_volume, plan_state, last_error FROM auto_exit_plans")
      )
    ).one()
    event = (
      await connection.execute(text("SELECT event_type, payload FROM auto_exit_plan_events"))
    ).one()
  assert intent[0] == "RECONCILED_ZERO_FILL"
  assert intent[1:3] == ("STRATEGY_RUN", "run-1")
  metadata = json.loads(intent[3])
  assert metadata["execution_terminal_source"] == "T_ASSISTANT_P1_MAINTENANCE"
  assert metadata["execution_terminal_reason"] == APPROVAL_REASON
  assert intent[4] == APPROVAL_REASON
  assert plan[0:4] == ("CANCELLED", 0, 5, 200)
  state = json.loads(plan[4])
  assert state["status"] == "CANCELLED"
  assert state["error_message"] == PLAN_REASON
  assert plan[5] == PLAN_REASON
  assert event[0] == "PLAN_CANCELLED"
  assert json.loads(event[1]) == {"reason": PLAN_REASON, "config_version": 7}

  second = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert second["applied"] is True
  async with database.connect() as connection:
    assert (await connection.execute(text("SELECT COUNT(*) FROM auto_exit_plan_events"))).scalar_one() == 1


async def test_terminal_non_buy_and_unsettled_inbox_block_without_partial_write(
  database: AsyncEngine,
) -> None:
  await _seed_base(database, direction="SELL", intent_id="intent-sell")
  await _seed_plan(database)
  async with database.begin() as connection:
    await connection.execute(
      text("INSERT INTO agent_report_inbox (message_id, processing_status) VALUES ('msg-1', 'PENDING')")
    )

  report = await run_inspection(database, now=NOW)
  assert report["safeApprovalCount"] == 0
  assert report["blockedApprovalCount"] == 1
  assert report["safePlanCount"] == 1
  assert report["unsettledInboxCount"] == 1
  assert report["readyToApply"] is False

  result = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=1,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert result["applied"] is False
  async with database.connect() as connection:
    assert (await connection.execute(text("SELECT status FROM trade_intents"))).scalar_one() == (
      "AWAITING_APPROVAL"
    )
    assert (await connection.execute(text("SELECT status FROM auto_exit_plans"))).scalar_one() == "ERROR"
    assert (await connection.execute(text("SELECT COUNT(*) FROM auto_exit_plan_events"))).scalar_one() == 0


@pytest.mark.asyncio
async def test_terminal_exit_unrouted_intent_is_safe_and_reports_id(
  database: AsyncEngine,
) -> None:
  await _seed_exit_intent(database)

  report = await run_inspection(database, now=NOW)

  assert report["safeExitIntentCount"] == 1
  assert report["blockedExitIntentCount"] == 0
  assert report["safeExitIntentIds"] == ["intent-exit"]
  assert report["terminalExitPlanUnroutedIntentAction"] == (
    TERMINAL_EXIT_PLAN_UNROUTED_INTENT
  )
  assert report["readyToApply"] is True
  assert report["applied"] is False

  async with database.connect() as connection:
    status = (
      await connection.execute(
        text("SELECT status, owner_type, owner_id FROM trade_intents")
      )
    ).one()
  assert status == ("PENDING", "EXIT_PLAN", "plan-exit")


@pytest.mark.asyncio
async def test_legacy_0045_terminal_exit_detector_is_safe(
  legacy_database: AsyncEngine,
) -> None:
  await _seed_legacy_exit_intent(legacy_database)

  report = await run_inspection(legacy_database, now=NOW)

  assert report["safeExitIntentCount"] == 1
  assert report["blockedExitIntentCount"] == 0
  assert report["safeExitIntentIds"] == ["intent-legacy-exit"]
  assert report["readyToApply"] is True


@pytest.mark.asyncio
async def test_legacy_0045_terminal_exit_apply_uses_selected_legacy_table(
  legacy_database: AsyncEngine,
) -> None:
  await _seed_legacy_exit_intent(legacy_database)

  report = await apply_reconciliation(
    legacy_database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=1,
    now=NOW,
  )

  assert report["applied"] is True
  assert report["safeExitIntentCount"] == 0
  async with legacy_database.connect() as connection:
    stored = (
      await connection.execute(
        text(
          "SELECT status, owner_type, owner_id, notes FROM strategy_trade_intents"
        )
      )
    ).one()
  assert stored == (
    "RECONCILED_ZERO_FILL",
    "EXIT_PLAN",
    "plan-legacy-exit",
    EXIT_INTENT_REASON,
  )


@pytest.mark.asyncio
async def test_terminal_exit_unrouted_intent_apply_is_atomic_and_idempotent(
  database: AsyncEngine,
) -> None:
  await _seed_exit_intent(database, metadata={"owner_id": "metadata-is-not-authority"})

  first = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=1,
    now=NOW,
  )

  assert first["applied"] is True
  assert first["safeExitIntentCount"] == 0
  assert first["blockedExitIntentCount"] == 0
  assert first["readyToApply"] is True
  async with database.connect() as connection:
    stored = (
      await connection.execute(
        text("SELECT status, owner_type, owner_id, notes, metadata FROM trade_intents")
      )
    ).one()
  assert stored[0:4] == (
    "RECONCILED_ZERO_FILL",
    "EXIT_PLAN",
    "plan-exit",
    EXIT_INTENT_REASON,
  )
  metadata = json.loads(stored[4])
  assert metadata["execution_terminal_action"] == TERMINAL_EXIT_PLAN_UNROUTED_INTENT
  assert metadata["execution_terminal_reason"] == EXIT_INTENT_REASON
  assert metadata["owner_id"] == "metadata-is-not-authority"

  second = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert second["applied"] is True
  assert second["safeExitIntentCount"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("case", "expected_reason"),
  (
    ("pending", "EXIT_INTENT_DURABLE_CHAIN_PRESENT"),
    ("correlation", "EXIT_INTENT_DURABLE_CHAIN_PRESENT"),
    ("outbox", "EXIT_INTENT_DURABLE_CHAIN_PRESENT"),
    ("runtime_client", "EXIT_INTENT_DURABLE_CHAIN_PRESENT"),
    ("runtime_payload", "EXIT_INTENT_DURABLE_CHAIN_PRESENT"),
    ("nested_metadata", "EXIT_INTENT_DURABLE_EVIDENCE_AMBIGUOUS"),
  ),
)
async def test_terminal_exit_evidence_blocks_repair(
  database: AsyncEngine,
  case: str,
  expected_reason: str,
) -> None:
  await _seed_exit_intent(database)
  async with database.begin() as connection:
    await _insert_exit_durable_link(connection, kind=case, intent_id="intent-exit")

  report = await run_inspection(database, now=NOW)
  assert report["safeExitIntentCount"] == 0
  assert report["blockedExitIntentCount"] == 1
  assert report["blockedExitIntentIds"] == ["intent-exit"]
  assert expected_reason in report["reasonCodes"]
  assert report["readyToApply"] is False

  result = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=0,
    now=NOW,
  )
  assert result["applied"] is False
  assert expected_reason in result["reasonCodes"]
  async with database.connect() as connection:
    assert (
      await connection.execute(text("SELECT status FROM trade_intents"))
    ).scalar_one() == "PENDING"


@pytest.mark.asyncio
async def test_terminal_exit_owner_metadata_is_not_an_authority(
  database: AsyncEngine,
) -> None:
  await _seed_exit_intent(database)
  async with database.begin() as connection:
    await _insert_exit_durable_link(
      connection,
      kind="owner_metadata",
      intent_id="intent-exit",
    )
  report = await run_inspection(database, now=NOW)
  assert report["safeExitIntentCount"] == 1
  assert report["blockedExitIntentCount"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("kwargs", "expected_reason"),
  (
    ({"intent_account_id": "acct-other"}, "EXIT_INTENT_ACCOUNT_CONFLICT"),
    ({"intent_instrument_code": "000002.SZ"}, "EXIT_INTENT_INSTRUMENT_CONFLICT"),
    ({"intent_environment": "LIVE"}, "EXIT_INTENT_ENVIRONMENT_CONFLICT"),
    ({"pending_intent_id": "intent-exit"}, "EXIT_PLAN_PENDING_INTENT_MATCH"),
    ({"order_id": "broker-order"}, "EXIT_INTENT_ORDER_PRESENT"),
    ({"executed_volume": 1}, "EXIT_INTENT_EXECUTION_PRESENT"),
    ({"intent_status": "ROUTED"}, "EXIT_INTENT_STATUS_AMBIGUOUS"),
  ),
)
async def test_terminal_exit_identity_or_state_mismatch_blocks_repair(
  database: AsyncEngine,
  kwargs: dict[str, object],
  expected_reason: str,
) -> None:
  await _seed_exit_intent(database, **kwargs)
  report = await run_inspection(database, now=NOW)
  assert report["safeExitIntentCount"] == 0
  assert report["blockedExitIntentCount"] == 1
  assert expected_reason in report["reasonCodes"]
  assert report["readyToApply"] is False


@pytest.mark.asyncio
async def test_terminal_exit_expected_count_mismatch_rolls_back(
  database: AsyncEngine,
) -> None:
  await _seed_exit_intent(database)
  await _seed_exit_intent(
    database,
    plan_id="plan-exit-2",
    intent_id="intent-exit-2",
  )

  result = await apply_reconciliation(
    database,
    confirmation=CONFIRMATION_WORD,
    expected_approval_count=0,
    expected_plan_count=0,
    expected_exit_intent_count=1,
    now=NOW,
  )
  assert result["applied"] is False
  assert "EXPECTED_EXIT_INTENT_COUNT_MISMATCH" in result["reasonCodes"]
  async with database.connect() as connection:
    statuses = (
      await connection.execute(
        text("SELECT status FROM trade_intents ORDER BY id")
      )
    ).scalars().all()
  assert statuses == ["PENDING", "PENDING"]


@pytest.mark.asyncio
async def test_live_terminal_exit_unrouted_intent_is_explicitly_safe(
  database: AsyncEngine,
) -> None:
  await _seed_exit_intent(
    database,
    plan_environment="LIVE",
    intent_environment="LIVE",
  )
  report = await run_inspection(database, now=NOW)
  assert report["safeExitIntentCount"] == 1
  assert report["safeExitIntentIds"] == ["intent-exit"]
  assert report["readyToApply"] is True


def test_cli_gate_and_markdown_are_deidentified(capsys: pytest.CaptureFixture[str]) -> None:
  assert main(["--apply", "--confirm", "wrong"]) == 2
  output = capsys.readouterr().out
  report = json.loads(output)
  assert report["readyToApply"] is False
  assert "CONFIRMATION_REQUIRED" in report["reasonCodes"]
  assert all(sentinel not in output for sentinel in SENTINELS)


def test_cli_apply_gate_uses_canonical_expected_count_dest(
  capsys: pytest.CaptureFixture[str],
) -> None:
  assert main(["--apply", "--confirm", CONFIRMATION_WORD]) == 2
  report = json.loads(capsys.readouterr().out)
  assert report["readyToApply"] is False
  assert "EXPECTED_APPROVAL_COUNT_REQUIRED" in report["reasonCodes"]


def test_cli_apply_gate_requires_expected_exit_intent_count(
  capsys: pytest.CaptureFixture[str],
) -> None:
  assert main(
    [
      "--apply",
      "--confirm",
      CONFIRMATION_WORD,
      "--expect-approval-count",
      "0",
      "--expect-plan-count",
      "0",
    ]
  ) == 2
  report = json.loads(capsys.readouterr().out)
  assert report["readyToApply"] is False
  assert "EXPECTED_EXIT_INTENT_COUNT_REQUIRED" in report["reasonCodes"]
