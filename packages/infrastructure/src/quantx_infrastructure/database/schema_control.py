"""Read-only production schema gate and pre-stamp schema doctor."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from quantx_infrastructure.database.relational_connection import engine

REPOSITORY_ROOT = (
  Path(os.environ["QUANTX_ROOT"]).expanduser().resolve()
  if os.environ.get("QUANTX_ROOT")
  else Path(__file__).resolve().parents[5]
)
ALEMBIC_CONFIG = REPOSITORY_ROOT / "alembic.ini"

REQUIRED_BASELINE: dict[str, set[str]] = {
  "auth_users": {"id"},
  "agent_devices": {"id", "authorized_account_ids", "capabilities"},
  "agent_report_inbox": {
    "message_id",
    "business_idempotency_key",
    "processing_status",
  },
  "trade_command_outbox": {
    "message_id",
    "client_order_id",
    "idempotency_key",
    "delivery_status",
  },
  "pending_trade_orders": {
    "client_order_id",
    "account_id",
    "status",
    "execution_mode",
  },
  "runtime_component_heartbeats": {"component", "status", "updated_at"},
  "account_trading_rollouts": {
    "account_id",
    "stage",
    "enabled",
    "kill_switch",
    "reconcile_status",
  },
}


def alembic_config() -> Config:
  return Config(str(ALEMBIC_CONFIG))


def expected_heads() -> tuple[str, ...]:
  scripts = ScriptDirectory.from_config(alembic_config())
  return tuple(sorted(scripts.get_heads()))


def _revision_relation(current_heads: list[str]) -> str:
  expected = expected_heads()
  if not current_heads:
    return "unversioned"
  if tuple(sorted(current_heads)) == expected:
    return "current"
  scripts = ScriptDirectory.from_config(alembic_config())
  known = {revision.revision for revision in scripts.walk_revisions()}
  if any(revision not in known for revision in current_heads):
    return "incompatible"
  expected_ancestors: set[str] = set()
  for head in expected:
    expected_ancestors.update(
      revision.revision for revision in scripts.iterate_revisions(head, "base")
    )
  return (
    "behind"
    if set(current_heads).issubset(expected_ancestors)
    else "incompatible"
  )


def _inspect_schema(connection) -> dict[str, Any]:
  inspector = inspect(connection)
  tables = set(inspector.get_table_names())
  missing_tables = sorted(set(REQUIRED_BASELINE) - tables)
  missing_columns: dict[str, list[str]] = {}
  for table_name, required in REQUIRED_BASELINE.items():
    if table_name not in tables:
      continue
    actual = {
      str(column["name"]) for column in inspector.get_columns(table_name)
    }
    missing = sorted(required - actual)
    if missing:
      missing_columns[table_name] = missing
  context = MigrationContext.configure(connection)
  current_heads = sorted(context.get_current_heads())
  return {
    "ok": not missing_tables and not missing_columns,
    "missing_tables": missing_tables,
    "missing_columns": missing_columns,
    "current_heads": current_heads,
    "expected_heads": list(expected_heads()),
    "revision_relation": _revision_relation(current_heads),
    "table_count": len(tables),
  }


async def schema_status() -> dict[str, Any]:
  async with engine.connect() as connection:
    return await connection.run_sync(_inspect_schema)


async def assert_schema_current() -> None:
  status = await schema_status()
  if tuple(status["current_heads"]) != expected_heads():
    raise RuntimeError(
      "数据库 revision 与应用不一致："
      f"current={status['current_heads'] or ['unversioned']} "
      f"expected={list(expected_heads())}；请先执行 ops/quantx.ps1 migrate"
    )


async def _main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("command", choices=("doctor", "check", "status"))
  args = parser.parse_args()
  status = await schema_status()
  if args.command == "doctor":
    ok = bool(status["ok"])
  elif args.command == "check":
    ok = tuple(status["current_heads"]) == expected_heads()
  else:
    ok = True
  print(json.dumps({**status, "ok": ok}, ensure_ascii=False, default=str))
  return 0 if ok else 2


if __name__ == "__main__":
  raise SystemExit(asyncio.run(_main()))
