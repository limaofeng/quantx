"""Tests for the read-only multi-instrument T assistant P0 audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from quantx_infrastructure.services.t_assistant_p0_audit import (
  AUDIT_CHECK_SPECS,
  REQUIRED_TABLES,
  audit_connection,
  exit_code_for_report,
  render_markdown,
  run_read_only_audit,
  scan_run_identity_assumptions,
)


class _FakeResult:
  def __init__(self, *, value: Any = None, rows: list[Any] | None = None):
    self.value = value
    self.rows = list(rows or [])

  def scalar_one_or_none(self) -> Any:
    return self.value

  def mappings(self) -> _FakeResult:
    return self

  def all(self) -> list[Any]:
    return list(self.rows)


class _FakeConnection:
  def __init__(
    self,
    *,
    existing_tables: list[str] | None = None,
    check_values: list[int] | None = None,
  ) -> None:
    self.statements: list[str] = []
    self.existing_tables = list(existing_tables or REQUIRED_TABLES)
    self.check_values = list(check_values or [0] * len(AUDIT_CHECK_SPECS))
    self.transaction = _FakeTransaction()

  async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _FakeResult:
    del args, kwargs
    sql = str(statement)
    self.statements.append(sql)
    if sql.lstrip().upper().startswith("SET TRANSACTION READ ONLY"):
      return _FakeResult()
    if "information_schema.tables" in sql:
      return _FakeResult(
        rows=[{"table_name": table_name} for table_name in self.existing_tables]
      )
    return _FakeResult(value=self.check_values.pop(0))

  async def begin(self) -> _FakeTransaction:
    return self.transaction


class _FakeTransaction:
  def __init__(self) -> None:
    self.rolled_back = False

  async def rollback(self) -> None:
    self.rolled_back = True


class _FakeConnectionContext:
  def __init__(self, connection: _FakeConnection) -> None:
    self.connection = connection

  async def __aenter__(self) -> _FakeConnection:
    return self.connection

  async def __aexit__(self, *args: Any) -> None:
    del args


class _FakeEngine:
  def __init__(self, connection: _FakeConnection) -> None:
    self.connection = connection
    self.transaction = connection.transaction

  def connect(self) -> _FakeConnectionContext:
    return _FakeConnectionContext(self.connection)


async def test_audit_uses_only_read_statements_and_rolls_back() -> None:
  connection = _FakeConnection()
  engine = _FakeEngine(connection)

  report = await run_read_only_audit(engine, Path.cwd())

  assert connection.statements[0].lstrip().upper().startswith("SET TRANSACTION READ ONLY")
  assert connection.statements[1].lstrip().upper().startswith("SELECT")
  assert all(
    statement.lstrip().upper().startswith(("SELECT", "SET", "WITH"))
    for statement in connection.statements
  )
  assert engine.transaction.rolled_back is True
  assert report["scope"] == "P0_LEGACY_T_ASSISTANT"


@pytest.mark.asyncio
async def test_missing_tables_fail_closed_and_skip_dependent_queries() -> None:
  connection = _FakeConnection(existing_tables=["strategies"])

  report = await audit_connection(connection)

  assert len(connection.statements) == 1
  assert report["readyForP1"] is False
  assert report["checks"][0]["code"] == "P0_REQUIRED_TABLES_MISSING"
  assert report["checks"][0]["count"] == len(REQUIRED_TABLES) - 1
  assert all(
    check["disposition"] == "SKIPPED_MISSING_REQUIRED_TABLE"
    for check in report["checks"][1:]
  )


@pytest.mark.asyncio
async def test_blocker_warning_and_ready_aggregation() -> None:
  values = [0] * len(AUDIT_CHECK_SPECS)
  values[
    next(
      index
      for index, spec in enumerate(AUDIT_CHECK_SPECS)
      if spec.code == "P0_NONTERMINAL_T_INTENT"
    )
  ] = 2
  values[
    next(
      index
      for index, spec in enumerate(AUDIT_CHECK_SPECS)
      if spec.code == "P0_LEGACY_T_INTENT_OWNER_INVALID_COUNT"
    )
  ] = 3
  report = await audit_connection(_FakeConnection(check_values=values))

  assert report["readyForP1"] is False
  assert report["summary"]["blockerCount"] == 1
  assert report["summary"]["warningCount"] == 1
  assert report["summary"]["checkCount"] == len(AUDIT_CHECK_SPECS)

  ready = await audit_connection(_FakeConnection())
  assert ready["readyForP1"] is True
  assert ready["summary"]["blockerCount"] == 0
  assert ready["summary"]["warningCount"] == 0


@pytest.mark.asyncio
async def test_error_exit_plan_is_active_and_fact_is_observed() -> None:
  values = [0] * len(AUDIT_CHECK_SPECS)
  enabled_config_index = next(
    index
    for index, spec in enumerate(AUDIT_CHECK_SPECS)
    if spec.code == "P0_ENABLED_LEGACY_CONFIG_COUNT"
  )
  active_count_index = next(
    index
    for index, spec in enumerate(AUDIT_CHECK_SPECS)
    if spec.code == "P0_OUTSTANDING_T_EXIT_PLAN_COUNT"
  )
  active_invalid_index = next(
    index
    for index, spec in enumerate(AUDIT_CHECK_SPECS)
    if spec.code == "P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID"
  )
  values[enabled_config_index] = 1
  values[active_count_index] = 1
  values[active_invalid_index] = 1
  connection = _FakeConnection(check_values=values)

  report = await audit_connection(connection)
  checks = {check["code"]: check for check in report["checks"]}

  assert checks["P0_ENABLED_LEGACY_CONFIG_COUNT"]["count"] == 1
  assert checks["P0_ENABLED_LEGACY_CONFIG_COUNT"]["disposition"] == "OBSERVED"
  assert checks["P0_OUTSTANDING_T_EXIT_PLAN_COUNT"]["count"] == 1
  assert checks["P0_OUTSTANDING_T_EXIT_PLAN_COUNT"]["disposition"] == "OBSERVED"
  assert checks["P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID"]["count"] == 1
  assert (
    checks["P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID"]["disposition"]
    == "BLOCK_P1_INVALID_OUTSTANDING_T_EXIT_PLAN_OWNER"
  )
  outstanding_sql = next(
    spec.sql
    for spec in AUDIT_CHECK_SPECS
    if spec.code == "P0_OUTSTANDING_T_EXIT_PLAN_COUNT"
  )
  assert "COMPLETED" in outstanding_sql
  assert "CANCELLED" in outstanding_sql
  assert "remaining_volume" in outstanding_sql


@pytest.mark.asyncio
async def test_legacy_t_intent_owner_reference_invalid_uses_frozen_total() -> None:
  values = [0] * len(AUDIT_CHECK_SPECS)
  legacy_index = next(
    index
    for index, spec in enumerate(AUDIT_CHECK_SPECS)
    if spec.code == "P0_LEGACY_T_INTENT_OWNER_INVALID_COUNT"
  )
  values[legacy_index] = 295
  report = await audit_connection(_FakeConnection(check_values=values))
  check = next(
    item
    for item in report["checks"]
    if item["code"] == "P0_LEGACY_T_INTENT_OWNER_INVALID_COUNT"
  )

  assert check["count"] == 295
  assert check["severity"] == "WARNING"
  assert check["blocksP1"] is False
  assert check["disposition"] == "REVIEW_LEGACY_T_INTENT_OWNER_INVALID"
  legacy_sql = next(
    spec.sql
    for spec in AUDIT_CHECK_SPECS
    if spec.code == "P0_LEGACY_T_INTENT_OWNER_INVALID_COUNT"
  )
  assert "owner_id" in legacy_sql
  assert "NOT IN" in legacy_sql


def test_exit_plan_sql_mirrors_durable_owner_matrix() -> None:
  sql = "\n".join(
    spec.sql
    for spec in AUDIT_CHECK_SPECS
    if spec.code
    in {
      "P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID",
      "P0_TERMINAL_T_EXIT_PLAN_OWNER_INVALID",
    }
  )
  for marker in (
    "plan_state::jsonb",
    "'template'",
    "'plan_id'",
    "'account_id'",
    "'instrument_code'",
    "'source_type'",
    "'run_id'",
    "managed_runtime_command_id",
    "T_TRADE_BATCH",
    "MANUAL_POSITION",
    "MANUAL_LIQUIDATION",
  ):
    assert marker in sql
  active_sql = next(
    spec.sql
    for spec in AUDIT_CHECK_SPECS
    if spec.code == "P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID"
  )
  assert "batch.batch_id = plan.source_id" in active_sql
  assert "batch.strategy_run_id = plan.strategy_run_id" in active_sql
  terminal_sql = next(
    spec.sql
    for spec in AUDIT_CHECK_SPECS
    if spec.code == "P0_TERMINAL_T_EXIT_PLAN_OWNER_INVALID"
  )
  assert "FROM strategy_runs AS run" in terminal_sql
  assert "run.id = plan.strategy_run_id" in terminal_sql
  assert "batch.strategy_run_id = plan.strategy_run_id" in active_sql


def test_report_does_not_leak_business_ids() -> None:
  fake_ids = ("account-SECRET", "run-SECRET", "plan-SECRET", "order-SECRET")
  rendered = render_markdown(
    {
      "scope": "P0_LEGACY_T_ASSISTANT",
      "currentProtocol": "1.1",
      "targetProtocol": "1.3",
      "readyForP1": True,
      "summary": {"blockerCount": 0, "warningCount": 0, "checkCount": 0},
      "checks": [],
      "sourceInventory": {"groups": {}},
    }
  )
  assert all(fake_id not in rendered for fake_id in fake_ids)


def test_source_inventory_groups_excludes_generated_and_sorts(tmp_path: Path) -> None:
  files = {
    "packages/contracts/src/z.ts": "const x = strategyRunId;\n",
    "packages/contracts/src/a.ts": "const x = ownerType;\n",
    "packages/domain/src/domain.py": "run_id = 'opaque'\n",
    "packages/application/src/app.py": "TradeCommandPayload\n",
    "packages/infrastructure/src/quantx_infrastructure/models/model.py": (
      "owner_type = 'STRATEGY_RUN'\n"
    ),
    "packages/infrastructure/src/quantx_infrastructure/services/service.py": (
      "strategy_run_id\n"
    ),
    "apps/engine/src/engine.py": "strategy_run_id\n",
    "apps/api/src/quantx_api/gqlapi/schema.graphql": "strategyRunId\n",
    "apps/api/src/quantx_api/routes.py": "owner_type\n",
    "apps/worker/src/worker.py": "run_id\n",
    "apps/qmt-agent/src/agent.py": "TradeCommandPayload\n",
    "apps/web/src/view.tsx": "ownerType\n",
    "apps/web/src/generated/generated.ts": "strategy_run_id\n",
    ".runtime/cache.py": "strategy_run_id\n",
    "node_modules/pkg/index.ts": "strategy_run_id\n",
    "README.md": "strategy_run_id\n",
  }
  for relative, contents in files.items():
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")

  inventory = scan_run_identity_assumptions(tmp_path)
  groups = inventory["groups"]
  assert list(groups) == [
    "DB",
    "contracts",
    "domain",
    "application",
    "infrastructure",
    "Engine",
    "API",
    "Worker",
    "QMT Agent",
    "GraphQL/Web",
  ]
  assert groups["contracts"]["fileCount"] == 2
  assert groups["contracts"]["files"][0]["path"] == "packages/contracts/src/a.ts"
  assert groups["GraphQL/Web"]["fileCount"] == 2
  all_paths = [
    item["path"]
    for group in groups.values()
    for item in group["files"]
  ]
  assert all("generated" not in path for path in all_paths)
  assert all(".runtime" not in path for path in all_paths)
  assert all("node_modules" not in path for path in all_paths)


def test_markdown_and_require_ready_status_are_deterministic() -> None:
  report = {
    "scope": "P0_LEGACY_T_ASSISTANT",
    "currentProtocol": "1.1",
    "targetProtocol": "1.3",
    "readyForP1": False,
    "summary": {"blockerCount": 1, "warningCount": 0, "checkCount": 1},
    "checks": [
      {
        "code": "P0_TEST",
        "category": "schema",
        "severity": "BLOCKER",
        "count": 1,
        "blocksP1": True,
        "description": "test",
        "disposition": "BLOCK_P1",
      }
    ],
    "sourceInventory": {"groups": {}},
  }
  markdown = render_markdown(report)
  assert "P0_TEST" in markdown
  assert "Ready for P1: `false`" in markdown
  assert exit_code_for_report(report, True) == 2
  assert exit_code_for_report(report, False) == 0
  report["readyForP1"] = True
  assert exit_code_for_report(report, True) == 0
