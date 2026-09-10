"""Read-only control-plane identity and effective privilege checks.

Never import shared environment settings here: all connection arguments originate
in the explicitly validated Trainer configuration. Catalog reads precede any
business-table reads, task claiming, deployment registration or capability writes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from quantx_trainer.config import TrainerConfig
from quantx_trainer.runtime_permissions import (
  RuntimePermissionsError,
  check_runtime_permissions,
)


class TrainerPreflightError(RuntimeError):
  """Stable public rejection code without connection information."""


# Execution roles do not create specs/runs, cancel user requests, delete history
# or publish models. Dataset insertion is for completed certification only.
REQUIRED_TABLE_GRANTS = {
  "stock_selection_dataset_versions": frozenset({"SELECT", "INSERT", "UPDATE"}),
  "stock_selection_training_specs": frozenset({"SELECT"}),
  "stock_selection_training_runs": frozenset({"SELECT", "UPDATE"}),
  "research_preparation_jobs": frozenset({"SELECT", "UPDATE"}),
  "runtime_component_heartbeats": frozenset({"SELECT", "INSERT", "UPDATE"}),
}

IDENTITY_SQL = """
SELECT current_database() AS database, current_user AS role,
       session_user AS session_role,
       r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
         OR r.rolbypassrls AS elevated,
       d.datdba = r.oid AS database_owner,
       has_database_privilege(d.oid, 'CREATE') OR
         has_database_privilege(d.oid, 'TEMP') AS database_ddl,
       EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid) AS memberships
FROM pg_roles r JOIN pg_database d ON d.datname = current_database()
WHERE r.rolname = current_user
"""

TABLES_SQL = """
SELECT n.nspname AS schema, c.relname AS name,
       has_schema_privilege(n.oid, 'USAGE') AS schema_usage,
       c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) AS owned,
       ARRAY(SELECT privilege FROM unnest(ARRAY[
         'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
       ]) AS privilege WHERE has_table_privilege(c.oid, privilege)) AS grants,
       ARRAY(SELECT privilege FROM unnest(ARRAY[
         'SELECT', 'INSERT', 'UPDATE', 'REFERENCES'
       ]) AS privilege WHERE has_any_column_privilege(c.oid, privilege)) AS column_grants,
       EXISTS (SELECT 1 FROM unnest(ARRAY[
         'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
       ]) AS privilege WHERE has_table_privilege(c.oid, privilege || ' WITH GRANT OPTION'))
       OR EXISTS (SELECT 1 FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES'])
                  AS privilege WHERE has_any_column_privilege(c.oid, privilege || ' WITH GRANT OPTION')) AS grant_option
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
"""

ESCAPES_SQL = """
SELECT
  EXISTS (SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_%'
          AND nspname <> 'information_schema'
          AND has_schema_privilege(oid, 'CREATE')) AS schema_create,
  EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND p.prosecdef AND has_function_privilege(p.oid, 'EXECUTE')) AS security_definer,
  EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE c.relkind = 'S' AND n.nspname NOT LIKE 'pg_%'
          AND (has_sequence_privilege(c.oid, 'USAGE') OR
               has_sequence_privilege(c.oid, 'SELECT') OR
               has_sequence_privilege(c.oid, 'UPDATE'))) AS sequence_access,
  EXISTS (SELECT 1 FROM pg_database WHERE NOT datistemplate AND datallowconn
          AND datname <> current_database()
          AND has_database_privilege(oid, 'CONNECT')) AS other_database
"""


def validate_database_snapshot(
  config: TrainerConfig,
  identity: Mapping[str, Any],
  tables: list[Mapping[str, Any]],
  escapes: Mapping[str, Any],
) -> None:
  target = urlsplit(config.database_url)
  if (
    identity.get("database") != unquote(target.path)[1:]
    or identity.get("role") != unquote(target.username or "")
    or identity.get("role") != identity.get("session_role")
  ):
    raise TrainerPreflightError("TRAINER_DATABASE_IDENTITY_MISMATCH")
  if any(
    identity.get(key) is not False
    for key in ("elevated", "database_owner", "database_ddl", "memberships")
  ):
    raise TrainerPreflightError("TRAINER_DATABASE_ROLE_TOO_POWERFUL")
  if set(escapes) != {
    "schema_create",
    "security_definer",
    "sequence_access",
    "other_database",
  } or any(value is not False for value in escapes.values()):
    raise TrainerPreflightError("TRAINER_DATABASE_PRIVILEGE_ESCAPE")
  seen = set()
  for table in tables:
    required = (
      REQUIRED_TABLE_GRANTS.get(table.get("name"))
      if table.get("schema") == "public"
      else None
    )
    grants = set(table.get("grants") or ())
    column_grants = set(table.get("column_grants") or ())
    if table.get("owned") is not False or table.get("grant_option") is not False:
      raise TrainerPreflightError("TRAINER_DATABASE_TABLE_OWNERSHIP_OR_GRANT_OPTION")
    if required is None:
      if grants or column_grants:
        raise TrainerPreflightError("TRAINER_DATABASE_UNRELATED_TABLE_ACCESS")
    else:
      if (
        grants != required
        or not column_grants <= required
        or table.get("schema_usage") is not True
      ):
        raise TrainerPreflightError("TRAINER_DATABASE_RESEARCH_GRANTS_MISMATCH")
      seen.add(table["name"])
  if seen != set(REQUIRED_TABLE_GRANTS):
    raise TrainerPreflightError("TRAINER_DATABASE_RESEARCH_SCHEMA_MISSING")


async def check_database(config: TrainerConfig) -> None:
  import asyncpg

  target = urlsplit(config.database_url)
  connection = None
  try:
    connection = await asyncpg.connect(
      host=target.hostname,
      port=target.port,
      user=unquote(target.username or ""),
      password=unquote(target.password or ""),
      database=unquote(target.path)[1:],
      timeout=10,
      command_timeout=10,
      server_settings={
        "application_name": "quantx-trainer-preflight",
        "statement_timeout": "5000",
      },
    )
    async with connection.transaction(readonly=True):
      identity = await connection.fetchrow(IDENTITY_SQL)
      tables = await connection.fetch(TABLES_SQL)
      escapes = await connection.fetchrow(ESCAPES_SQL)
      validate_database_snapshot(
        config, dict(identity or {}), list(tables), dict(escapes or {})
      )
  except TrainerPreflightError:
    raise
  except Exception:
    raise TrainerPreflightError("TRAINER_DATABASE_CHECK_UNAVAILABLE") from None
  finally:
    if connection is not None:
      try:
        await connection.close(timeout=5)
      except Exception:
        connection.terminate()


def validate_pool(config: TrainerConfig, pool: Mapping[str, Any]) -> None:
  if (
    pool.get("id") != config.prefect_pool_id
    or pool.get("name") != config.prefect_pool
    or pool.get("type") != "process"
    or pool.get("is_paused") is not False
  ):
    raise TrainerPreflightError("TRAINER_PREFECT_POOL_IDENTITY_OR_STATE_MISMATCH")


async def check_prefect(config: TrainerConfig) -> None:
  import httpx

  try:
    async with httpx.AsyncClient(
      timeout=10, trust_env=False, follow_redirects=False
    ) as client:
      response = await client.get(
        f"{config.prefect_api_url}/work_pools/{config.prefect_pool}"
      )
      response.raise_for_status()
      payload = response.json()
      if not isinstance(payload, dict):
        raise ValueError
      validate_pool(config, payload)
  except TrainerPreflightError:
    raise
  except Exception:
    raise TrainerPreflightError("TRAINER_PREFECT_CHECK_UNAVAILABLE") from None


def check_worker_runtime() -> None:
  """Exercise the actual worker imports without registering or starting it."""
  from importlib import import_module

  try:
    import_module("prefect.workers.process").ProcessWorker
  except Exception:
    raise TrainerPreflightError("TRAINER_WORKER_RUNTIME_UNAVAILABLE") from None


async def preflight(config: TrainerConfig) -> dict[str, str]:
  try:
    await asyncio.to_thread(
      check_runtime_permissions, config.code_root, Path(sys.prefix)
    )
  except RuntimePermissionsError as exc:
    raise TrainerPreflightError(str(exc)) from None
  await asyncio.to_thread(check_worker_runtime)
  # Do not contact Prefect if database identity or privilege isolation is wrong.
  await check_database(config)
  await check_prefect(config)
  from quantx_infrastructure.training_transfer import TransferConfig, check_store

  try:
    transfer = TransferConfig.load(config.transfer_config, state_root=config.state_root)
    await asyncio.to_thread(check_store, transfer)
  except Exception:
    raise TrainerPreflightError("TRAINER_STORE_UNAVAILABLE") from None
  return {
    "status": "PREFLIGHT_PASSED",
    "environment": "development",
    "pool": config.prefect_pool,
  }
