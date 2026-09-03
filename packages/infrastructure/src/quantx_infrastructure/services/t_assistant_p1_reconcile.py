"""Fail-closed maintenance for the P1 execution-owner legacy obligations.

This module is intentionally a one-shot maintenance tool.  It does not add a
new owner, alter the Agent contract, or call a broker.  ``inspect_connection``
only discovers and proves aggregate facts.  ``apply_reconciliation`` opens a
new transaction, takes the maintenance fence, repeats discovery, and mutates
only the proven legacy record types as one batch.

The implementation uses small SQL projections instead of ORM objects.  That
keeps the read side usable against both PostgreSQL and the SQLite test store,
while the mutation still uses the mapped table metadata so JSON values are
encoded by SQLAlchemy's configured type.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import JSON, Column, MetaData, String, Table, Text, insert, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

P1_SCHEMA_VERSION = 1
RECONCILE_SCOPE = "P1_EXECUTION_OWNER_LEGACY_RECONCILE"
CONFIRMATION_WORD = "P1_EXECUTION_OWNER_RECONCILE"
APPROVAL_REASON = "P1_PAPER_STALE_APPROVAL_RECONCILED_ZERO_FILL"
PLAN_REASON = "P1_PAPER_ORPHAN_PLAN_RECONCILED"
EXIT_INTENT_REASON = (
  "P1_TERMINAL_EXIT_PLAN_UNROUTED_INTENT_RECONCILED_ZERO_FILL"
)
TERMINAL_EXIT_PLAN_UNROUTED_INTENT = "TERMINAL_EXIT_PLAN_UNROUTED_INTENT"
RECONCILE_DATABASE_ERROR = "RECONCILE_DATABASE_ERROR"
PLAN_EVENT_TYPE = "PLAN_CANCELLED"
PLAN_EVENT_KEY_PREFIX = "p1-paper-orphan-reconciled:"
STRATEGY_CLASS = "AshareIntradayTAssistantStrategy"
PAPER_MODE = "paper"
ACTIVE_RUN_STATUSES = frozenset({"PENDING", "RUNNING", "PAUSED"})
TERMINAL_PLAN_STATUSES = frozenset({"COMPLETED", "CANCELLED"})
TERMINAL_INTENT_STATUSES = frozenset(
  {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "FAILED",
    "SUPPRESSED",
    "RECONCILED_ZERO_FILL",
  }
)
PRE_BROKER_EXIT_INTENT_STATUSES = frozenset(
  {"PENDING", "APPROVED", "AWAITING_APPROVAL", "DELAYED"}
)

REQUIRED_TABLES = (
  "strategies",
  "strategy_runs",
  "trade_intents",
  "pending_trade_orders",
  "order_correlations",
  "trade_command_outbox",
  "strategy_runtime_events",
  "t_trade_batches",
  "auto_exit_plans",
  "auto_exit_plan_events",
  "agent_report_inbox",
)


@dataclass(frozen=True)
class _TableNames:
  """The one schema projection selected by this maintenance invocation."""

  intent: str
  correlation: str
  plan_environment: str


CANONICAL_TABLES = _TableNames(
  intent="trade_intents",
  correlation="order_correlations",
  plan_environment="environment",
)
LEGACY_TABLES = _TableNames(
  intent="strategy_trade_intents",
  correlation="strategy_order_correlations",
  plan_environment="execution_mode",
)
TABLE_DISCOVERY_NAMES = tuple(
  dict.fromkeys(
    (*REQUIRED_TABLES, LEGACY_TABLES.intent, LEGACY_TABLES.correlation)
  )
)


class P1ReconcileError(RuntimeError):
  """Stable, non-sensitive error returned by the maintenance boundary."""

  def __init__(self, code: str):
    self.code = str(code)
    super().__init__(self.code)


@dataclass(frozen=True)
class _ApprovalCandidate:
  """Internal approval proof; never placed in a public report."""

  intent_id: str
  run_id: str
  metadata: dict[str, Any]
  created_at: datetime
  intent_table: str = CANONICAL_TABLES.intent


@dataclass(frozen=True)
class _ExitIntentCandidate:
  """A directly proven, pre-broker ExitPlan intent requiring zero-fill repair."""

  intent_id: str
  plan_id: str
  status: str
  account_id: str
  instrument_code: str
  metadata: dict[str, Any]
  intent_table: str = CANONICAL_TABLES.intent


@dataclass(frozen=True)
class _PlanCandidate:
  """Internal orphan-plan proof; never placed in a public report."""

  plan_id: str
  config_version: int
  state: dict[str, Any]
  event_exists: bool


@dataclass(frozen=True)
class _Discovery:
  safe_approvals: tuple[_ApprovalCandidate, ...]
  blocked_approval_count: int
  safe_plans: tuple[_PlanCandidate, ...]
  blocked_plan_count: int
  safe_exit_intents: tuple[_ExitIntentCandidate, ...]
  blocked_exit_intent_count: int
  blocked_exit_intent_ids: tuple[str, ...]
  unsettled_inbox_count: int
  missing_tables: tuple[str, ...]
  reason_codes: tuple[str, ...]


def _dialect_name(connection: Any) -> str:
  """Return the connection dialect without requiring a real SQLAlchemy object."""

  dialect = getattr(connection, "dialect", None)
  if dialect is None:
    engine = getattr(connection, "engine", None)
    dialect = getattr(engine, "dialect", None)
  name = getattr(dialect, "name", None)
  return str(name or "unknown").lower()


def _is_postgresql(connection: Any) -> bool:
  return _dialect_name(connection).startswith("postgresql")


def _result_scalar(result: Any) -> Any:
  """Read a result from SQLAlchemy or a deliberately tiny test double."""

  for method_name in ("scalar_one_or_none", "scalar", "scalar_one"):
    method = getattr(result, method_name, None)
    if callable(method):
      try:
        return method()
      except Exception:  # noqa: BLE001 - test doubles may expose one method only
        continue
  mappings = getattr(result, "mappings", None)
  if callable(mappings):
    mapped = mappings()
    first = getattr(mapped, "first", None)
    if callable(first):
      row = first()
      if isinstance(row, Mapping):
        return row.get("count_value", next(iter(row.values()), None))
  return None


def _result_rows(result: Any) -> list[Any]:
  mappings = getattr(result, "mappings", None)
  if callable(mappings):
    mapped = mappings()
    all_rows = getattr(mapped, "all", None)
    if callable(all_rows):
      return list(all_rows())
  scalars = getattr(result, "scalars", None)
  if callable(scalars):
    values = scalars()
    all_values = getattr(values, "all", None)
    if callable(all_values):
      return list(all_values())
  all_rows = getattr(result, "all", None)
  if callable(all_rows):
    return list(all_rows())
  return []


def _row_value(row: Any, name: str, default: Any = None) -> Any:
  if isinstance(row, Mapping):
    return row.get(name, default)
  if hasattr(row, name):
    return getattr(row, name)
  if isinstance(row, (tuple, list)):
    return default
  return default


def _normalise_count(value: Any) -> int:
  try:
    return max(0, int(value or 0))
  except (TypeError, ValueError, OverflowError):
    return 0


def _decode_json(value: Any) -> Any:
  if isinstance(value, Mapping):
    return dict(value)
  if isinstance(value, (bytes, bytearray)):
    value = value.decode("utf-8", errors="replace")
  if isinstance(value, str):
    try:
      return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
      return None
  return value


def _mapping(value: Any) -> dict[str, Any] | None:
  decoded = _decode_json(value)
  return dict(decoded) if isinstance(decoded, Mapping) else None


def _clean_text(value: Any) -> str:
  value = getattr(value, "value", value)
  return str(value or "").strip()


def _upper(value: Any) -> str:
  return _clean_text(value).upper()


def _blank(value: Any) -> bool:
  return _clean_text(value) == ""


def _as_bool(value: Any) -> bool | None:
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)) and value in (0, 1):
    return bool(value)
  normalized = _upper(value)
  if normalized in {"TRUE", "1", "YES"}:
    return True
  if normalized in {"FALSE", "0", "NO", ""}:
    return False
  return None


def _parse_datetime(value: Any) -> datetime | None:
  if isinstance(value, datetime):
    parsed = value
  elif isinstance(value, str):
    raw = value.strip()
    if not raw:
      return None
    try:
      parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
      return None
  else:
    return None
  if parsed.tzinfo is None:
    return parsed.replace(tzinfo=timezone.utc)
  return parsed.astimezone(timezone.utc)


def _utc_now(value: datetime | None) -> datetime:
  parsed = _parse_datetime(value)
  return parsed or datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
  return _utc_now(value).isoformat()


def _positive_integer(value: Any) -> int | None:
  # A boolean is an integer subclass but is not a meaningful TTL/count here.
  if isinstance(value, bool):
    return None
  if isinstance(value, int):
    return value if value > 0 else None
  return None


def _nonnegative_number(value: Any, *, positive_only: bool = False) -> float | None:
  if isinstance(value, bool) or value is None:
    return 0.0 if value is None else None
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  if number != number or number in (float("inf"), float("-inf")):
    return None
  if positive_only:
    return number if number > 0 else 0.0
  return number if number >= 0 else None


def _normalise_state_text(value: Any) -> str:
  candidate = getattr(value, "value", value)
  return _upper(candidate)


def _identity_aliases() -> dict[str, frozenset[str]]:
  return {
    "intent": frozenset({"intentid"}),
    "run": frozenset({"strategyrunid", "runid"}),
    "plan": frozenset({"planid", "exitplanid"}),
    "source": frozenset({"sourceid"}),
    "batch": frozenset({"batchid", "tbatchid"}),
    "client": frozenset({"clientorderid"}),
    "order": frozenset({"orderid", "pendingorderid"}),
    "pending_intent": frozenset({"pendingintentid"}),
  }


_ALIASES = _identity_aliases()


def _key_token(value: Any) -> str:
  return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _payload_identity_values(value: Any) -> dict[str, set[str]]:
  """Collect legacy snake/camel identity paths, including nested metadata."""

  found: dict[str, set[str]] = {key: set() for key in _ALIASES}

  def visit(node: Any) -> None:
    if isinstance(node, Mapping):
      for key, child in node.items():
        token = _key_token(key)
        for category, aliases in _ALIASES.items():
          if token in aliases and child is not None:
            text_value = _clean_text(child)
            if text_value:
              found[category].add(text_value)
        visit(child)
    elif isinstance(node, (list, tuple)):
      for child in node:
        visit(child)

  visit(_decode_json(value))
  return found


def _contains_identity(found: Mapping[str, set[str]], category: str, value: Any) -> bool:
  needle = _clean_text(value)
  return bool(needle and needle in found.get(category, set()))


def _row_has_approval_association(
  row: Mapping[str, Any],
  *,
  intent_id: str,
  run_id: str,
  kind: str,
) -> bool:
  if kind == "pending":
    if _clean_text(row.get("intent_id")) == intent_id:
      return True
    if _clean_text(row.get("strategy_run_id")) == run_id:
      return True
    found = _payload_identity_values(row.get("request_metadata"))
  elif kind == "correlation":
    if _clean_text(row.get("intent_id")) == intent_id:
      return True
    if _clean_text(row.get("strategy_run_id")) == run_id:
      return True
    found = _payload_identity_values(row.get("request_metadata"))
  elif kind == "outbox":
    if _clean_text(row.get("client_order_id")) == _clean_text(intent_id):
      return True
    found = _payload_identity_values(row.get("payload"))
  else:
    if _clean_text(row.get("strategy_run_id")) == run_id:
      return True
    if _clean_text(row.get("client_order_id")) == _clean_text(intent_id):
      return True
    found = _payload_identity_values(row.get("payload"))
  return _contains_identity(found, "intent", intent_id) or _contains_identity(
    found, "run", run_id
  )


def _row_has_plan_association(
  row: Mapping[str, Any],
  *,
  plan_id: str,
  source_id: str,
  pending_intent_id: str,
  pending_order_id: str,
  kind: str,
) -> bool:
  """Conservatively match every known legacy plan/source identity path."""

  if kind == "intent":
    row_id = _clean_text(row.get("id"))
    if row_id and row_id in {
      value for value in (plan_id, source_id, pending_intent_id, pending_order_id) if value
    }:
      return True
    if _clean_text(row.get("owner_id")) == plan_id:
      return True
    if (
      _clean_text(row.get("order_id"))
      and pending_order_id
      and _clean_text(row.get("order_id")) == pending_order_id
    ):
      return True
    found = _payload_identity_values(row.get("metadata"))
  elif kind == "pending":
    if (
      _clean_text(row.get("intent_id"))
      and pending_intent_id
      and _clean_text(row.get("intent_id")) == pending_intent_id
    ):
      return True
    if _clean_text(row.get("batch_id")) and _clean_text(row.get("batch_id")) == source_id:
      return True
    if (
      _clean_text(row.get("client_order_id"))
      and pending_order_id
      and _clean_text(row.get("client_order_id")) == pending_order_id
    ):
      return True
    found = _payload_identity_values(row.get("request_metadata"))
  elif kind == "correlation":
    if (
      _clean_text(row.get("intent_id"))
      and pending_intent_id
      and _clean_text(row.get("intent_id")) == pending_intent_id
    ):
      return True
    if _clean_text(row.get("batch_id")) and _clean_text(row.get("batch_id")) == source_id:
      return True
    if (
      _clean_text(row.get("client_order_id"))
      and pending_order_id
      and _clean_text(row.get("client_order_id")) == pending_order_id
    ):
      return True
    found = _payload_identity_values(row.get("request_metadata"))
  else:
    if (
      _clean_text(row.get("client_order_id"))
      and pending_order_id
      and _clean_text(row.get("client_order_id")) == pending_order_id
    ):
      return True
    found = _payload_identity_values(row.get("payload"))
  for category, value in (
    ("plan", plan_id),
    ("source", source_id),
    ("batch", source_id),
    ("pending_intent", pending_intent_id),
    ("intent", pending_intent_id),
    ("client", pending_order_id),
    ("order", pending_order_id),
  ):
    if _contains_identity(found, category, value):
      return True
  return False


def _event_payload_is_target(payload: Any, *, reason: str, config_version: int) -> bool:
  decoded = _mapping(payload)
  if decoded is None:
    return False
  return (
    _clean_text(decoded.get("reason")) == reason
    and _positive_integer(decoded.get("config_version")) == config_version
  )


def _approval_row_sql(*, for_update: bool, intent_table: str) -> str:
  suffix = " FOR UPDATE OF intent, run, strategy" if for_update else ""
  return (
    "SELECT intent.id AS intent_id, intent.strategy_run_id AS run_id, "
    "intent.status AS intent_status, intent.direction AS direction, "
    "intent.executed_price AS executed_price, "
    "intent.executed_volume AS executed_volume, "
    "intent.executed_time AS executed_time, intent.order_id AS order_id, "
    "intent.metadata AS intent_metadata, intent.created_at AS created_at, "
    "run.status AS run_status, run.mode AS run_mode, "
    "strategy.class_name AS strategy_class "
    f"FROM {intent_table} AS intent "
    "JOIN strategy_runs AS run ON run.id = intent.strategy_run_id "
    "JOIN strategies AS strategy ON strategy.id = run.strategy_id "
    "WHERE strategy.class_name = :strategy_class "
    "AND UPPER(CAST(intent.status AS TEXT)) = 'AWAITING_APPROVAL' "
    "ORDER BY intent.created_at, intent.id"
    + suffix
  )


def _plan_row_sql(*, plan_environment_column: str, for_update: bool) -> str:
  suffix = " FOR UPDATE OF plan" if for_update else ""
  return (
    "SELECT plan.plan_id, plan.account_id, plan.instrument_code, plan.source_type, "
    "plan.source_id, plan.strategy_run_id, plan.enabled, plan.status, "
    f"plan.{plan_environment_column} AS environment, "
    "plan.remaining_volume, plan.pending_client_order_id, "
    "plan.auto_exit_authorized, plan.auto_exit_authorization_fingerprint, "
    "plan.auto_exit_authorization_config_version, plan.auto_exit_authorized_at, "
    "plan.auto_exit_authorization_expires_at, "
    "plan.auto_exit_authorization_challenge_id, plan.auto_exit_authorization_user_id, "
    "plan.auto_exit_authorization_device_session_id, plan.config_version, "
    "plan.state_version, plan.plan_state, plan.last_error, "
    "run.id AS associated_run_id, "
    "(SELECT COUNT(*) FROM strategy_runs AS run_count "
    "WHERE run_count.id = plan.strategy_run_id) AS associated_run_count, "
    "(SELECT COUNT(*) FROM strategies AS strategy_count "
    "JOIN strategy_runs AS run_count "
    "ON run_count.strategy_id = strategy_count.id "
    "WHERE run_count.id = plan.strategy_run_id) AS associated_strategy_count "
    "FROM auto_exit_plans AS plan "
    "LEFT JOIN strategy_runs AS run ON run.id = plan.strategy_run_id "
    "WHERE UPPER(CAST(plan.source_type AS TEXT)) = 'T_TRADE_BATCH' "
    "AND UPPER(CAST(plan.status AS TEXT)) NOT IN ('COMPLETED', 'CANCELLED') "
    "AND COALESCE(plan.remaining_volume, 0) > 0 "
    "ORDER BY plan.plan_id"
    + suffix
  )


async def _query_rows(
  connection: AsyncConnection,
  statement: str,
  params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
  result = await connection.execute(text(statement), dict(params or {}))
  rows: list[dict[str, Any]] = []
  for row in _result_rows(result):
    if isinstance(row, Mapping):
      rows.append(dict(row))
  return rows


async def _existing_tables(connection: AsyncConnection) -> set[str]:
  table_names = ", ".join(f"'{name}'" for name in TABLE_DISCOVERY_NAMES)
  if _dialect_name(connection).startswith("sqlite"):
    statement = (
      "SELECT name AS table_name FROM sqlite_master "
      "WHERE type = 'table' AND name IN (" + table_names + ") ORDER BY name"
    )
  else:
    statement = (
      "SELECT table_name FROM information_schema.tables "
      "WHERE table_schema = 'public' AND table_name IN ("
      + table_names
      + ") ORDER BY table_name"
    )
  result = await connection.execute(text(statement))
  values: set[str] = set()
  for row in _result_rows(result):
    name = _row_value(row, "table_name")
    if name is None:
      name = _row_value(row, "name")
    if name is not None:
      values.add(str(name))
  return values


def _select_table_names(existing: set[str]) -> tuple[_TableNames | None, tuple[str, ...], str | None]:
  """Select exactly one persisted schema projection for this maintenance run.

  0046 renames the two legacy tables in place.  The maintenance command is
  allowed to inspect either side of that migration, but a partially renamed
  database is ambiguous and must not be treated as a compatibility surface.
  """

  if CANONICAL_TABLES.intent in existing or CANONICAL_TABLES.correlation in existing:
    if (
      CANONICAL_TABLES.intent in existing
      and CANONICAL_TABLES.correlation in existing
      and LEGACY_TABLES.intent not in existing
      and LEGACY_TABLES.correlation not in existing
    ):
      return CANONICAL_TABLES, (), None
    if (
      LEGACY_TABLES.intent in existing
      or LEGACY_TABLES.correlation in existing
    ):
      return None, (), "TABLE_NAME_PROJECTION_CONFLICT"
    return None, (), "REQUIRED_TABLES_MISSING"
  if LEGACY_TABLES.intent in existing or LEGACY_TABLES.correlation in existing:
    if (
      LEGACY_TABLES.intent in existing
      and LEGACY_TABLES.correlation in existing
      and CANONICAL_TABLES.intent not in existing
      and CANONICAL_TABLES.correlation not in existing
    ):
      return LEGACY_TABLES, (), None
    return None, (), "TABLE_NAME_PROJECTION_CONFLICT"
  return None, (CANONICAL_TABLES.intent, CANONICAL_TABLES.correlation), None


async def _inbox_unsettled_count(connection: AsyncConnection) -> int:
  result = await connection.execute(
    text(
      "SELECT COUNT(*) AS count_value FROM agent_report_inbox "
      "WHERE processing_status IS NULL "
      "OR UPPER(CAST(processing_status AS TEXT)) NOT IN ('PROCESSED', 'SUPERSEDED')"
    )
  )
  return _normalise_count(_result_scalar(result))


async def _durable_rows(
  connection: AsyncConnection,
  *,
  tables: _TableNames,
) -> dict[str, list[dict[str, Any]]]:
  return {
    "pending": await _query_rows(
      connection,
      "SELECT client_order_id, strategy_run_id, intent_id, batch_id, "
      "request_metadata FROM pending_trade_orders",
    ),
    "correlation": await _query_rows(
      connection,
      f"SELECT client_order_id, strategy_run_id, intent_id, batch_id, "
      f"request_metadata FROM {tables.correlation}",
    ),
    "outbox": await _query_rows(
      connection,
      "SELECT client_order_id, payload FROM trade_command_outbox",
    ),
    "runtime": await _query_rows(
      connection,
      "SELECT strategy_run_id, client_order_id, payload FROM strategy_runtime_events",
    ),
    "intent": await _query_rows(
      connection,
      f"SELECT id, owner_id, strategy_run_id, order_id, metadata "
      f"FROM {tables.intent}",
    ),
  }


def _approval_proof(
  row: Mapping[str, Any],
  *,
  now: datetime,
  durable: Mapping[str, Sequence[Mapping[str, Any]]],
  intent_table: str = CANONICAL_TABLES.intent,
) -> tuple[_ApprovalCandidate | None, str | None]:
  intent_id = _clean_text(row.get("intent_id"))
  run_id = _clean_text(row.get("run_id"))
  if not intent_id or not run_id:
    return None, "APPROVAL_IDENTITY_MISSING"
  if _clean_text(row.get("strategy_class")) != STRATEGY_CLASS:
    return None, "APPROVAL_STRATEGY_CLASS_CONFLICT"
  if _clean_text(row.get("intent_status")) != "AWAITING_APPROVAL":
    return None, "APPROVAL_STATUS_CONFLICT"
  if _clean_text(row.get("direction")) != "BUY":
    return None, "APPROVAL_DIRECTION_CONFLICT"
  if _upper(row.get("run_status")) in ACTIVE_RUN_STATUSES:
    return None, "APPROVAL_ACTIVE_RUN"
  if _upper(row.get("run_mode")) != _upper(PAPER_MODE):
    return None, "APPROVAL_NOT_PAPER"
  metadata = _mapping(row.get("intent_metadata"))
  if metadata is None:
    return None, "APPROVAL_METADATA_INVALID"
  ttl = _positive_integer(metadata.get("approval_ttl_ms"))
  created_at = _parse_datetime(row.get("created_at"))
  if ttl is None or created_at is None:
    return None, "APPROVAL_TTL_INVALID"
  if now <= created_at + timedelta(milliseconds=ttl):
    return None, "APPROVAL_NOT_EXPIRED"
  if any(
    _clean_text(metadata.get(key))
    for key in ("candidate_id", "candidate_fingerprint", "fingerprint")
  ):
    return None, "APPROVAL_CANDIDATE_IDENTITY_PRESENT"
  volume = _nonnegative_number(row.get("executed_volume"), positive_only=True)
  price = _nonnegative_number(row.get("executed_price"), positive_only=True)
  if volume is None or price is None:
    return None, "APPROVAL_EXECUTION_FIELD_INVALID"
  if volume > 0 or price > 0 or not _blank(row.get("executed_time")):
    return None, "APPROVAL_EXECUTION_PRESENT"
  if not _blank(row.get("order_id")):
    return None, "APPROVAL_ORDER_PRESENT"
  for kind, rows in durable.items():
    if kind not in {"pending", "correlation", "outbox", "runtime"}:
      continue
    if any(
      _row_has_approval_association(
        item,
        intent_id=intent_id,
        run_id=run_id,
        kind=kind,
      )
      for item in rows
    ):
      return None, "APPROVAL_DURABLE_CHAIN_PRESENT"
  return _ApprovalCandidate(
    intent_id,
    run_id,
    metadata,
    created_at,
    intent_table,
  ), None


def _exit_intent_row_sql(*, tables: _TableNames, for_update: bool) -> str:
  # ``plan`` is on the nullable side of this left join; PostgreSQL rejects
  # ``FOR UPDATE OF plan``.  The serializable transaction plus maintenance
  # advisory lock protects the plan-side absence/terminal proof, while the
  # intent row itself is locked for the mutation.
  suffix = " FOR UPDATE OF intent" if for_update else ""
  intent_environment = (
    "NULL AS intent_environment"
    if tables.intent == LEGACY_TABLES.intent
    else "intent.environment AS intent_environment"
  )
  return (
    "SELECT intent.id AS intent_id, intent.owner_type, intent.owner_id, "
    f"{intent_environment}, intent.account_id AS intent_account_id, "
    "intent.instrument_code AS intent_instrument_code, intent.direction, "
    "intent.status AS intent_status, intent.order_id, intent.executed_volume, "
    "intent.metadata AS intent_metadata, plan.plan_id, "
    "plan.account_id AS plan_account_id, plan.instrument_code AS plan_instrument_code, "
    "plan.status AS plan_status, "
    f"plan.{tables.plan_environment} AS plan_environment, "
    "plan.plan_state AS plan_state "
    f"FROM {tables.intent} AS intent "
    "LEFT JOIN auto_exit_plans AS plan ON plan.plan_id = intent.owner_id "
    "WHERE UPPER(CAST(intent.owner_type AS TEXT)) = 'EXIT_PLAN' "
    "AND (plan.plan_id IS NULL OR "
    "UPPER(CAST(plan.status AS TEXT)) IN ('COMPLETED', 'CANCELLED')) "
    "AND (intent.status IS NULL OR UPPER(CAST(intent.status AS TEXT)) NOT IN "
    "('FILLED', 'CANCELLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'FAILED', "
    "'SUPPRESSED', 'RECONCILED_ZERO_FILL')) "
    "ORDER BY plan.plan_id, intent.id"
    + suffix
  )


async def _exit_intent_rows(
  connection: AsyncConnection,
  *,
  tables: _TableNames,
  for_update: bool,
) -> list[dict[str, Any]]:
  return await _query_rows(
    connection,
    _exit_intent_row_sql(
      tables=tables,
      for_update=for_update and _is_postgresql(connection),
    ),
  )


async def _exit_intent_durable_rows(
  connection: AsyncConnection,
  *,
  tables: _TableNames,
) -> dict[str, list[dict[str, Any]]]:
  """Read only durable intent links used by the terminal-exit proof.

  These projections intentionally omit owner metadata.  The direct intent
  columns and the documented top-level payload identity are the only links
  that can prove a command was routed.
  """

  owner_columns = (
    "NULL AS owner_type, NULL AS owner_id"
    if tables.intent == LEGACY_TABLES.intent
    else "owner_type, owner_id"
  )
  return {
    "pending": await _query_rows(
      connection,
      "SELECT client_order_id, intent_id, "
      f"{owner_columns}, request_metadata FROM pending_trade_orders",
    ),
    "correlation": await _query_rows(
      connection,
      f"SELECT client_order_id, intent_id, {owner_columns}, request_metadata "
      f"FROM {tables.correlation}",
    ),
    "outbox": await _query_rows(
      connection,
      f"SELECT client_order_id, {owner_columns}, payload FROM trade_command_outbox",
    ),
    "runtime": await _query_rows(
      connection,
      f"SELECT client_order_id, {owner_columns}, payload "
      "FROM strategy_runtime_events",
    ),
  }


_PAYLOAD_MISSING = object()


def _direct_payload_value(payload: Any, key: str) -> tuple[Any, bool]:
  """Return one top-level payload value and whether the payload is ambiguous."""

  decoded = _decode_json(payload)
  if not isinstance(decoded, Mapping):
    return _PAYLOAD_MISSING, payload not in (None, "", b"", bytearray())
  if key not in decoded:
    return _PAYLOAD_MISSING, False
  value = decoded[key]
  if value is None or (
    isinstance(value, (str, int, float)) and not isinstance(value, bool)
  ):
    return value, False
  return _PAYLOAD_MISSING, True


def _payload_has_ambiguous_intent(
  payload: Any,
  *,
  intent_id: str,
  direct_keys: Sequence[str],
) -> tuple[bool, bool]:
  """Return ``(direct_match, ambiguous_match)`` for one durable payload."""

  direct_match = False
  decoded = _decode_json(payload)
  if not isinstance(decoded, Mapping):
    return False, payload not in (None, "", b"", bytearray())
  for key in direct_keys:
    value, ambiguous = _direct_payload_value(decoded, key)
    if ambiguous:
      return False, True
    if value is not _PAYLOAD_MISSING and _clean_text(value) == intent_id:
      direct_match = True
  identities = _payload_identity_values(decoded)
  nested_values = identities.get("intent", set())
  direct_values = {
    _clean_text(decoded.get(key))
    for key in direct_keys
    if key in decoded and decoded.get(key) is not None
  }
  if len(nested_values) > 1 or nested_values - direct_values:
    # A nested metadata identity is not a durable command link.  Keep it as
    # an ambiguity so uncertain or conflicting evidence cannot become a zero
    # fill.
    return False, True
  return direct_match, False


def _exit_intent_durable_evidence(
  intent_id: str,
  plan_id: str,
  durable: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[bool, bool]:
  """Check direct durable links, distinguishing evidence from ambiguity."""

  ambiguous = False
  for row in durable.get("pending", ()):
    owner_type = _upper(row.get("owner_type"))
    owner_id = _clean_text(row.get("owner_id"))
    if owner_type == "EXIT_PLAN" and owner_id == plan_id:
      ambiguous = True
    if _clean_text(row.get("intent_id")) == intent_id:
      return True, False
    _, row_ambiguous = _payload_has_ambiguous_intent(
      row.get("request_metadata"),
      intent_id=intent_id,
      direct_keys=("intent_id",),
    )
    ambiguous = ambiguous or row_ambiguous
  for row in durable.get("correlation", ()):
    owner_type = _upper(row.get("owner_type"))
    owner_id = _clean_text(row.get("owner_id"))
    if owner_type == "EXIT_PLAN" and owner_id == plan_id:
      ambiguous = True
    if _clean_text(row.get("intent_id")) == intent_id:
      return True, False
    _, row_ambiguous = _payload_has_ambiguous_intent(
      row.get("request_metadata"),
      intent_id=intent_id,
      direct_keys=("intent_id",),
    )
    ambiguous = ambiguous or row_ambiguous
  for row in durable.get("outbox", ()):
    owner_type = _upper(row.get("owner_type"))
    owner_id = _clean_text(row.get("owner_id"))
    if owner_type == "EXIT_PLAN" and owner_id == plan_id:
      ambiguous = True
    direct_match, row_ambiguous = _payload_has_ambiguous_intent(
      row.get("payload"),
      intent_id=intent_id,
      direct_keys=("intent_id",),
    )
    if direct_match:
      return True, False
    ambiguous = ambiguous or row_ambiguous
  for row in durable.get("runtime", ()):
    owner_type = _upper(row.get("owner_type"))
    owner_id = _clean_text(row.get("owner_id"))
    if owner_type == "EXIT_PLAN" and owner_id == plan_id:
      ambiguous = True
    if _clean_text(row.get("client_order_id")) == intent_id:
      return True, False
    direct_match, row_ambiguous = _payload_has_ambiguous_intent(
      row.get("payload"),
      intent_id=intent_id,
      direct_keys=("intent_id", "client_order_id"),
    )
    if direct_match:
      return True, False
    ambiguous = ambiguous or row_ambiguous
  return False, ambiguous


def _exit_intent_proof(
  row: Mapping[str, Any],
  *,
  durable: Mapping[str, Sequence[Mapping[str, Any]]],
  intent_table: str,
) -> tuple[_ExitIntentCandidate | None, str | None]:
  """Prove a terminal ExitPlan intent was never routed to a broker."""

  intent_id = _clean_text(row.get("intent_id"))
  plan_id = _clean_text(row.get("plan_id"))
  if not intent_id or not plan_id:
    return None, "EXIT_INTENT_IDENTITY_MISSING"
  if _upper(row.get("owner_type")) != "EXIT_PLAN" or _clean_text(
    row.get("owner_id")
  ) != plan_id:
    return None, "EXIT_INTENT_OWNER_CONFLICT"
  if _upper(row.get("direction")) != "SELL":
    return None, "EXIT_INTENT_DIRECTION_CONFLICT"
  if _upper(row.get("plan_status")) not in TERMINAL_PLAN_STATUSES:
    return None, "EXIT_PLAN_NOT_TERMINAL"
  account_id = _clean_text(row.get("plan_account_id"))
  instrument_code = _clean_text(row.get("plan_instrument_code"))
  if not account_id or not instrument_code:
    return None, "EXIT_INTENT_PLAN_IDENTITY_MISSING"
  if _clean_text(row.get("intent_account_id")) != account_id:
    return None, "EXIT_INTENT_ACCOUNT_CONFLICT"
  if _upper(row.get("intent_instrument_code")) != _upper(instrument_code):
    return None, "EXIT_INTENT_INSTRUMENT_CONFLICT"
  plan_environment = _upper(row.get("plan_environment"))
  intent_environment = _upper(row.get("intent_environment"))
  if plan_environment not in {"PAPER", "LIVE"}:
    return None, "EXIT_INTENT_ENVIRONMENT_INVALID"
  if intent_table == LEGACY_TABLES.intent:
    intent_environment = plan_environment
  elif intent_environment not in {"PAPER", "LIVE"}:
    return None, "EXIT_INTENT_ENVIRONMENT_INVALID"
  if plan_environment != intent_environment:
    return None, "EXIT_INTENT_ENVIRONMENT_CONFLICT"
  intent_status = _upper(row.get("intent_status"))
  if intent_status in TERMINAL_INTENT_STATUSES:
    return None, "EXIT_INTENT_ALREADY_TERMINAL"
  if intent_status not in PRE_BROKER_EXIT_INTENT_STATUSES:
    return None, "EXIT_INTENT_STATUS_AMBIGUOUS"
  if not _blank(row.get("order_id")):
    return None, "EXIT_INTENT_ORDER_PRESENT"
  volume = _nonnegative_number(row.get("executed_volume"))
  if volume is None:
    return None, "EXIT_INTENT_EXECUTION_FIELD_INVALID"
  if volume > 0:
    return None, "EXIT_INTENT_EXECUTION_PRESENT"
  state = _mapping(row.get("plan_state"))
  if state is None:
    return None, "EXIT_PLAN_STATE_UNPARSEABLE"
  pending_intent_id = _clean_text(state.get("pending_intent_id"))
  if pending_intent_id == intent_id:
    return None, "EXIT_PLAN_PENDING_INTENT_MATCH"
  durable_match, durable_ambiguous = _exit_intent_durable_evidence(
    intent_id,
    plan_id,
    durable,
  )
  if durable_match:
    return None, "EXIT_INTENT_DURABLE_CHAIN_PRESENT"
  if durable_ambiguous:
    return None, "EXIT_INTENT_DURABLE_EVIDENCE_AMBIGUOUS"
  return (
    _ExitIntentCandidate(
      intent_id=intent_id,
      plan_id=plan_id,
      status=intent_status,
      account_id=account_id,
      instrument_code=instrument_code,
      metadata=_mapping(row.get("intent_metadata")) or {},
      intent_table=intent_table,
    ),
    None,
  )


async def _discover_exit_intents(
  connection: AsyncConnection,
  *,
  tables: _TableNames,
  for_update: bool,
) -> tuple[
  tuple[_ExitIntentCandidate, ...],
  int,
  tuple[str, ...],
  tuple[str, ...],
]:
  """Discover terminal-exit orphan intents with one shared proof path."""

  rows = await _exit_intent_rows(
    connection,
    tables=tables,
    for_update=for_update,
  )
  durable = await _exit_intent_durable_rows(connection, tables=tables)
  safe: list[_ExitIntentCandidate] = []
  blocked_ids: list[str] = []
  reasons: set[str] = set()
  for row in rows:
    candidate, reason = _exit_intent_proof(
      row,
      durable=durable,
      intent_table=tables.intent,
    )
    if candidate is not None:
      safe.append(candidate)
      continue
    intent_id = _clean_text(row.get("intent_id"))
    if intent_id:
      blocked_ids.append(intent_id)
    if reason:
      reasons.add(reason)
  return (
    tuple(safe),
    len(rows) - len(safe),
    tuple(sorted(set(blocked_ids))),
    tuple(sorted(reasons)),
  )


async def inspect_terminal_exit_plan_unrouted_intents(
  connection: AsyncConnection,
  *,
  legacy_schema: bool = False,
) -> dict[str, Any]:
  """Expose the exact detector for cutover readiness without mutating state."""

  existing = await _existing_tables(connection)
  tables = LEGACY_TABLES if legacy_schema else CANONICAL_TABLES
  required = (
    tables.intent,
    tables.correlation,
    "pending_trade_orders",
    "trade_command_outbox",
    "strategy_runtime_events",
    "auto_exit_plans",
  )
  missing = tuple(table for table in required if table not in existing)
  if missing:
    return {
      "safeExitIntentCount": 0,
      "blockedExitIntentCount": 0,
      "safeExitIntentIds": [],
      "blockedExitIntentIds": [],
      "reasonCodes": ["REQUIRED_TABLES_MISSING"],
      "missingTables": list(missing),
    }
  safe, blocked_count, blocked_ids, reasons = await _discover_exit_intents(
    connection,
    tables=tables,
    for_update=False,
  )
  return {
    "safeExitIntentCount": len(safe),
    "blockedExitIntentCount": blocked_count,
    "safeExitIntentIds": sorted(candidate.intent_id for candidate in safe),
    "blockedExitIntentIds": list(blocked_ids),
    "reasonCodes": list(reasons),
    "missingTables": [],
  }


def _plan_state_is_pending(plan: Any, state: Mapping[str, Any]) -> bool:
  for name in (
    "pending_intent_id",
    "pending_order_id",
    "pending_rule_id",
    "pending_client_order_id",
  ):
    if not _blank(getattr(plan, name, None)):
      return True
  for name in ("pending_requested_volume", "pending_filled_volume"):
    value = getattr(plan, name, 0)
    if value is None:
      continue
    parsed = _nonnegative_number(value)
    if parsed is None or parsed > 0:
      return True
  if _as_bool(getattr(plan, "pending_order_terminal", False)):
    return True
  if getattr(plan, "pending_terminal_cumulative_fill", None) is not None:
    return True
  runtime_state = state.get("rule_state")
  if isinstance(runtime_state, Mapping):
    for value in runtime_state.values():
      if isinstance(value, Mapping) and any(
        not _blank(value.get(name))
        for name in ("pending_intent_id", "pending_order_id", "pending_client_order_id")
      ):
        return True
  return False


def _plan_authorization_is_present(row: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
  if _as_bool(row.get("auto_exit_authorized")) is not False:
    return True
  for name in (
    "auto_exit_authorization_fingerprint",
    "auto_exit_authorization_config_version",
    "auto_exit_authorized_at",
    "auto_exit_authorization_expires_at",
    "auto_exit_authorization_challenge_id",
    "auto_exit_authorization_user_id",
    "auto_exit_authorization_device_session_id",
  ):
    if not _blank(row.get(name)):
      return True
  template = state.get("template")
  if (
    isinstance(template, Mapping)
    and _as_bool(template.get("auto_exit_authorized")) is not False
  ):
    return True
  return False


def _plan_identity_matches(row: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
  template = state.get("template")
  if not isinstance(template, Mapping):
    return False
  fields = (
    "plan_id",
    "account_id",
    "instrument_code",
    "source_type",
    "source_id",
  )
  if any(_clean_text(template.get(field)) != _clean_text(row.get(field)) for field in fields):
    return False
  return _clean_text(template.get("run_id")) == _clean_text(row.get("strategy_run_id"))


def _plan_event_key(plan_id: str) -> str:
  return PLAN_EVENT_KEY_PREFIX + plan_id


async def _plan_event_rows(connection: AsyncConnection) -> list[dict[str, Any]]:
  return await _query_rows(
    connection,
    "SELECT business_key, plan_id, event_type, payload FROM auto_exit_plan_events",
  )


def _plan_event_status(
  rows: Sequence[Mapping[str, Any]],
  *,
  plan_id: str,
  config_version: int,
) -> tuple[bool, bool]:
  key = _plan_event_key(plan_id)
  for row in rows:
    if _clean_text(row.get("business_key")) != key:
      continue
    exact = (
      _clean_text(row.get("plan_id")) == plan_id
      and _clean_text(row.get("event_type")) == PLAN_EVENT_TYPE
      and _event_payload_is_target(
        row.get("payload"), reason=PLAN_REASON, config_version=config_version
      )
    )
    return True, exact
  return False, False


def _plan_proof(
  row: Mapping[str, Any],
  *,
  batches: Sequence[Mapping[str, Any]],
  durable: Mapping[str, Sequence[Mapping[str, Any]]],
  events: Sequence[Mapping[str, Any]],
) -> tuple[_PlanCandidate | None, str | None]:
  plan_id = _clean_text(row.get("plan_id"))
  source_id = _clean_text(row.get("source_id"))
  if not plan_id or not source_id:
    return None, "PLAN_IDENTITY_MISSING"
  if _upper(row.get("status")) in TERMINAL_PLAN_STATUSES:
    return None, "PLAN_ALREADY_TERMINAL"
  if _upper(row.get("environment")) != _upper(PAPER_MODE):
    return None, "PLAN_NOT_PAPER"
  if _as_bool(row.get("enabled")) is not False:
    return None, "PLAN_ENABLED"
  strategy_run_id = _clean_text(row.get("strategy_run_id"))
  if not strategy_run_id:
    return None, "PLAN_STRATEGY_RUN_MISSING"
  if (
    _normalise_count(row.get("associated_run_count")) != 0
    or _normalise_count(row.get("associated_strategy_count")) != 0
    or not _blank(row.get("associated_run_id"))
  ):
    return None, "PLAN_STRATEGY_RUN_PRESENT"
  if _upper(row.get("status")) != "ERROR":
    return None, "PLAN_RECORD_NOT_ERROR"
  raw_state = _mapping(row.get("plan_state"))
  if raw_state is None:
    return None, "PLAN_STATE_UNPARSEABLE"
  try:
    from quantx_domain.trading.exit_plan import ExitPlan

    parsed = ExitPlan.from_dict(raw_state)
  except Exception:  # noqa: BLE001 - malformed legacy state is a blocker
    return None, "PLAN_STATE_UNPARSEABLE"
  if _normalise_state_text(getattr(parsed, "status", "")) != "ERROR":
    return None, "PLAN_STATE_NOT_ERROR"
  if not _plan_identity_matches(row, raw_state):
    return None, "PLAN_IDENTITY_CONFLICT"
  if any(_clean_text(item.get("batch_id")) == source_id for item in batches):
    return None, "PLAN_SOURCE_BATCH_PRESENT"
  if not _blank(row.get("pending_client_order_id")) or _plan_state_is_pending(
    parsed, raw_state
  ):
    return None, "PLAN_PENDING_IDENTITY_PRESENT"
  if _plan_authorization_is_present(row, raw_state):
    return None, "PLAN_AUTHORIZATION_PRESENT"
  config_version = _positive_integer(row.get("config_version"))
  if config_version is None:
    return None, "PLAN_CONFIG_VERSION_INVALID"
  event_exists, event_exact = _plan_event_status(
    events, plan_id=plan_id, config_version=config_version
  )
  if event_exists and not event_exact:
    return None, "PLAN_EVENT_IDENTITY_CONFLICT"
  for kind, rows in durable.items():
    if kind not in {"intent", "pending", "correlation", "outbox", "runtime"}:
      continue
    pending_intent_id = _clean_text(getattr(parsed, "pending_intent_id", ""))
    pending_order_id = _clean_text(
      getattr(parsed, "pending_order_id", "") or row.get("pending_client_order_id")
    )
    if any(
      _row_has_plan_association(
        item,
        plan_id=plan_id,
        source_id=source_id,
        pending_intent_id=pending_intent_id,
        pending_order_id=pending_order_id,
        kind=kind,
      )
      for item in rows
    ):
      return None, "PLAN_DURABLE_CHAIN_PRESENT"
  return _PlanCandidate(plan_id, config_version, raw_state, event_exists), None


async def _discover(
  connection: AsyncConnection,
  *,
  now: datetime,
  for_update: bool,
) -> _Discovery:
  existing = await _existing_tables(connection)
  tables, _, table_reason = _select_table_names(existing)
  missing_names = [name for name in REQUIRED_TABLES if name not in existing]
  if tables is LEGACY_TABLES:
    missing_names = [
      name
      for name in missing_names
      if name not in {CANONICAL_TABLES.intent, CANONICAL_TABLES.correlation}
    ]
  missing = tuple(missing_names)
  if missing or tables is None:
    reason = table_reason or "REQUIRED_TABLES_MISSING"
    return _Discovery(
      safe_approvals=(),
      blocked_approval_count=0,
      safe_plans=(),
      blocked_plan_count=0,
      safe_exit_intents=(),
      blocked_exit_intent_count=0,
      blocked_exit_intent_ids=(),
      unsettled_inbox_count=0,
      missing_tables=missing,
      reason_codes=(reason,),
    )

  approval_rows = await _query_rows(
    connection,
    _approval_row_sql(
      for_update=for_update and _is_postgresql(connection),
      intent_table=tables.intent,
    ),
    {"strategy_class": STRATEGY_CLASS},
  )
  plan_rows = await _query_rows(
    connection,
    _plan_row_sql(
      plan_environment_column=tables.plan_environment,
      for_update=for_update and _is_postgresql(connection),
    ),
  )
  # In apply mode candidate rows are locked before the durable evidence is
  # read.  At READ COMMITTED this prevents a concurrent writer from adding an
  # order/correlation after an absence check but before the mutation.
  unsettled = await _inbox_unsettled_count(connection)
  durable = await _durable_rows(connection, tables=tables)
  batches = await _query_rows(
    connection,
    "SELECT batch_id FROM t_trade_batches",
  )
  events = await _plan_event_rows(connection)
  safe_approvals: list[_ApprovalCandidate] = []
  blocked_approvals = 0
  approval_reason_codes: set[str] = set()
  for row in approval_rows:
    candidate, reason = _approval_proof(
      row,
      now=now,
      durable=durable,
      intent_table=tables.intent,
    )
    if candidate is not None:
      safe_approvals.append(candidate)
      continue
    if _upper(row.get("run_status")) not in ACTIVE_RUN_STATUSES:
      blocked_approvals += 1
      if reason:
        approval_reason_codes.add(reason)

  safe_plans: list[_PlanCandidate] = []
  blocked_plans = 0
  plan_reason_codes: set[str] = set()
  for row in plan_rows:
    candidate, reason = _plan_proof(
      row,
      batches=batches,
      durable=durable,
      events=events,
    )
    if candidate is not None:
      safe_plans.append(candidate)
      continue
    blocked_plans += 1
    if reason:
      plan_reason_codes.add(reason)

  (
    safe_exit_intents,
    blocked_exit_intents,
    blocked_exit_intent_ids,
    exit_reason_codes,
  ) = await _discover_exit_intents(
    connection,
    tables=tables,
    for_update=for_update,
  )

  reasons: set[str] = (
    set(approval_reason_codes) | plan_reason_codes | set(exit_reason_codes)
  )
  if blocked_approvals:
    reasons.add("BLOCKED_APPROVALS")
  if blocked_plans:
    reasons.add("BLOCKED_PLANS")
  if blocked_exit_intents:
    reasons.add("BLOCKED_EXIT_INTENTS")
  if unsettled:
    reasons.add("UNSETTLED_AGENT_INBOX")
  return _Discovery(
    safe_approvals=tuple(safe_approvals),
    blocked_approval_count=blocked_approvals,
    safe_plans=tuple(safe_plans),
    blocked_plan_count=blocked_plans,
    safe_exit_intents=tuple(safe_exit_intents),
    blocked_exit_intent_count=blocked_exit_intents,
    blocked_exit_intent_ids=tuple(sorted(set(blocked_exit_intent_ids))),
    unsettled_inbox_count=unsettled,
    missing_tables=(),
    reason_codes=tuple(sorted(reasons)),
  )


def _report(
  discovery: _Discovery,
  *,
  mode: str,
  applied: bool,
  expected_approval_count: int | None = None,
  expected_plan_count: int | None = None,
  expected_exit_intent_count: int | None = None,
  extra_reasons: Iterable[str] = (),
) -> dict[str, Any]:
  reasons = set(discovery.reason_codes)
  extra = {str(value) for value in extra_reasons if value}
  reasons.update(extra)
  if discovery.missing_tables:
    reasons.add("REQUIRED_TABLES_MISSING")
  ready = (
    not discovery.missing_tables
    and discovery.blocked_approval_count == 0
    and discovery.blocked_plan_count == 0
    and discovery.blocked_exit_intent_count == 0
    and discovery.unsettled_inbox_count == 0
    and not extra
  )
  return {
    "schemaVersion": P1_SCHEMA_VERSION,
    "scope": RECONCILE_SCOPE,
    "mode": mode,
    "safeApprovalCount": len(discovery.safe_approvals),
    "blockedApprovalCount": discovery.blocked_approval_count,
    "safePlanCount": len(discovery.safe_plans),
    "blockedPlanCount": discovery.blocked_plan_count,
    "safeExitIntentCount": len(discovery.safe_exit_intents),
    "blockedExitIntentCount": discovery.blocked_exit_intent_count,
    "safeExitIntentIds": sorted(
      candidate.intent_id for candidate in discovery.safe_exit_intents
    ),
    "blockedExitIntentIds": list(discovery.blocked_exit_intent_ids),
    "terminalExitPlanUnroutedIntentAction": TERMINAL_EXIT_PLAN_UNROUTED_INTENT,
    "unsettledInboxCount": discovery.unsettled_inbox_count,
    "applied": bool(applied),
    "readyToApply": bool(ready),
    "reasonCodes": sorted(reasons),
    "missingTables": list(discovery.missing_tables),
    "expectedApprovalCount": expected_approval_count,
    "expectedPlanCount": expected_plan_count,
    "expectedExitIntentCount": expected_exit_intent_count,
  }


_LEGACY_INTENT_METADATA = MetaData()
_LEGACY_INTENT_TABLE = Table(
  LEGACY_TABLES.intent,
  _LEGACY_INTENT_METADATA,
  Column("id", String(128)),
  Column("owner_type", String(32)),
  Column("owner_id", String(128)),
  Column("environment", String(16)),
  Column("account_id", String(50)),
  Column("instrument_code", String(20)),
  Column("status", String(32)),
  Column("metadata", JSON),
  Column("notes", Text),
)


def _intent_mutation_table(table_name: str) -> Table:
  if table_name == TradeIntentRecord.__table__.name:
    return TradeIntentRecord.__table__
  if table_name == LEGACY_TABLES.intent:
    return _LEGACY_INTENT_TABLE
  raise P1ReconcileError("INTENT_TABLE_PROJECTION_UNKNOWN")


async def inspect_connection(
  connection: AsyncConnection,
  *,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Inspect one connection without changing it."""

  discovery = await _discover(
    connection,
    now=_utc_now(now),
    for_update=False,
  )
  return _report(discovery, mode="dry-run", applied=False)


async def run_inspection(
  engine: AsyncEngine,
  *,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Run a read-only dry-run transaction and roll it back unconditionally."""

  async with engine.connect() as connection:
    transaction = await connection.begin()
    try:
      if _is_postgresql(connection):
        await connection.execute(text("SET TRANSACTION READ ONLY"))
      report = await inspect_connection(connection, now=now)
    finally:
      await transaction.rollback()
  return report


async def _acquire_maintenance_lock(connection: AsyncConnection) -> None:
  """Fence concurrent maintenance calls; non-PostgreSQL test stores skip SQL."""

  if _is_postgresql(connection):
    await connection.execute(
      text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
      {"scope": RECONCILE_SCOPE},
    )


async def _set_apply_isolation(connection: AsyncConnection) -> None:
  """Prevent durable-evidence phantom inserts during a PostgreSQL apply."""

  if _is_postgresql(connection):
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))


async def _mutate(
  connection: AsyncConnection,
  discovery: _Discovery,
  *,
  now: datetime,
) -> tuple[int, int, int]:
  """Apply a previously proven *same-transaction* discovery atomically."""

  intent_table = TradeIntentRecord.__table__
  plan_table = AutoExitPlanRecord.__table__
  event_table = AutoExitPlanEvent.__table__
  audit_at = _utc_iso(now)
  intent_count = 0
  plan_count = 0
  exit_intent_count = 0
  for candidate in discovery.safe_approvals:
    intent_table = _intent_mutation_table(candidate.intent_table)
    metadata = copy.deepcopy(candidate.metadata)
    metadata.update(
      {
        "execution_terminal_source": "T_ASSISTANT_P1_MAINTENANCE",
        "execution_terminal_reason": APPROVAL_REASON,
        "execution_terminal_at": audit_at,
      }
    )
    result = await connection.execute(
      update(intent_table)
      .where(intent_table.c.id == candidate.intent_id)
      .where(intent_table.c.status == "AWAITING_APPROVAL")
      .values(
        status="RECONCILED_ZERO_FILL",
        notes=APPROVAL_REASON,
        owner_type="STRATEGY_RUN",
        owner_id=candidate.run_id,
        **{"metadata": metadata},
      )
    )
    if getattr(result, "rowcount", 1) != 1:
      raise P1ReconcileError("APPROVAL_CANDIDATE_CHANGED")
    intent_count += 1

  for candidate in discovery.safe_exit_intents:
    intent_table = _intent_mutation_table(candidate.intent_table)
    metadata = copy.deepcopy(candidate.metadata)
    metadata.update(
      {
        "execution_terminal_source": "T_ASSISTANT_P1_MAINTENANCE",
        "execution_terminal_action": TERMINAL_EXIT_PLAN_UNROUTED_INTENT,
        "execution_terminal_reason": EXIT_INTENT_REASON,
        "execution_terminal_at": audit_at,
      }
    )
    result = await connection.execute(
      update(intent_table)
      .where(intent_table.c.id == candidate.intent_id)
      .where(intent_table.c.status == candidate.status)
      .where(intent_table.c.owner_type == "EXIT_PLAN")
      .where(intent_table.c.owner_id == candidate.plan_id)
      .where(intent_table.c.account_id == candidate.account_id)
      .where(intent_table.c.instrument_code == candidate.instrument_code)
      .values(
        status="RECONCILED_ZERO_FILL",
        notes=EXIT_INTENT_REASON,
        **{"metadata": metadata},
      )
    )
    if getattr(result, "rowcount", 1) != 1:
      raise P1ReconcileError("EXIT_INTENT_CANDIDATE_CHANGED")
    exit_intent_count += 1

  for candidate in discovery.safe_plans:
    state = copy.deepcopy(candidate.state)
    state["status"] = "CANCELLED"
    state["error_message"] = PLAN_REASON
    template = state.get("template")
    if isinstance(template, Mapping):
      template_copy = dict(template)
      template_copy["auto_exit_authorized"] = False
      state["template"] = template_copy
    result = await connection.execute(
      update(plan_table)
      .where(plan_table.c.plan_id == candidate.plan_id)
      .where(plan_table.c.status != "CANCELLED")
      .values(
        status="CANCELLED",
        enabled=False,
        last_error=PLAN_REASON,
        auto_exit_authorized=False,
        auto_exit_authorization_fingerprint=None,
        auto_exit_authorization_config_version=None,
        auto_exit_authorized_at=None,
        auto_exit_authorization_expires_at=None,
        auto_exit_authorization_challenge_id=None,
        auto_exit_authorization_user_id=None,
        auto_exit_authorization_device_session_id=None,
        plan_state=state,
        state_version=plan_table.c.state_version + 1,
        updated_at=now.replace(tzinfo=None),
      )
    )
    if getattr(result, "rowcount", 1) != 1:
      raise P1ReconcileError("PLAN_CANDIDATE_CHANGED")
    if not candidate.event_exists:
      await connection.execute(
        insert(event_table).values(
          event_id=str(uuid.uuid4()),
          business_key=_plan_event_key(candidate.plan_id),
          plan_id=candidate.plan_id,
          event_type=PLAN_EVENT_TYPE,
          payload={"reason": PLAN_REASON, "config_version": candidate.config_version},
          created_at=now.replace(tzinfo=None),
        )
      )
    plan_count += 1
  return intent_count, plan_count, exit_intent_count


async def apply_reconciliation(
  engine: AsyncEngine,
  *,
  confirmation: str,
  expected_approval_count: int,
  expected_plan_count: int,
  expected_exit_intent_count: int,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Re-discover, prove, and atomically repair legacy obligations."""

  if confirmation != CONFIRMATION_WORD:
    raise P1ReconcileError("CONFIRMATION_REQUIRED")
  if not isinstance(expected_approval_count, int) or isinstance(
    expected_approval_count, bool
  ):
    raise P1ReconcileError("EXPECTED_APPROVAL_COUNT_REQUIRED")
  if not isinstance(expected_plan_count, int) or isinstance(expected_plan_count, bool):
    raise P1ReconcileError("EXPECTED_PLAN_COUNT_REQUIRED")
  if not isinstance(expected_exit_intent_count, int) or isinstance(
    expected_exit_intent_count, bool
  ):
    raise P1ReconcileError("EXPECTED_EXIT_INTENT_COUNT_REQUIRED")
  if (
    expected_approval_count < 0
    or expected_plan_count < 0
    or expected_exit_intent_count < 0
  ):
    raise P1ReconcileError("EXPECTED_COUNT_INVALID")

  checked_at = _utc_now(now)
  async with engine.connect() as connection:
    transaction = await connection.begin()
    try:
      await _set_apply_isolation(connection)
      await _acquire_maintenance_lock(connection)
      # This discovery is deliberately not obtained from the dry-run object.
      discovery = await _discover(connection, now=checked_at, for_update=True)
      mismatch_reasons: list[str] = []
      if len(discovery.safe_approvals) != expected_approval_count:
        mismatch_reasons.append("EXPECTED_APPROVAL_COUNT_MISMATCH")
      if len(discovery.safe_plans) != expected_plan_count:
        mismatch_reasons.append("EXPECTED_PLAN_COUNT_MISMATCH")
      if len(discovery.safe_exit_intents) != expected_exit_intent_count:
        mismatch_reasons.append("EXPECTED_EXIT_INTENT_COUNT_MISMATCH")
      report = _report(
        discovery,
        mode="apply",
        applied=False,
        expected_approval_count=expected_approval_count,
        expected_plan_count=expected_plan_count,
        expected_exit_intent_count=expected_exit_intent_count,
        extra_reasons=mismatch_reasons,
      )
      if not report["readyToApply"] or mismatch_reasons:
        await transaction.rollback()
        return report
      await _mutate(connection, discovery, now=checked_at)
      await transaction.commit()
      # Re-discover after commit so an applied report cannot claim that the
      # repaired intent is still an outstanding candidate.
      post_discovery = await _discover(connection, now=checked_at, for_update=False)
      return _report(
        post_discovery,
        mode="apply",
        applied=True,
        expected_approval_count=expected_approval_count,
        expected_plan_count=expected_plan_count,
        expected_exit_intent_count=expected_exit_intent_count,
      )
    except Exception:
      await transaction.rollback()
      raise


async def reconcile(
  engine: AsyncEngine,
  *,
  apply: bool = False,
  confirmation: str | None = None,
  expected_approval_count: int | None = None,
  expected_plan_count: int | None = None,
  expected_exit_intent_count: int | None = None,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Unified API: dry-run by default, gated apply when explicitly requested."""

  if not apply:
    return await run_inspection(engine, now=now)
  if confirmation is None:
    raise P1ReconcileError("CONFIRMATION_REQUIRED")
  if expected_approval_count is None:
    raise P1ReconcileError("EXPECTED_APPROVAL_COUNT_REQUIRED")
  if expected_plan_count is None:
    raise P1ReconcileError("EXPECTED_PLAN_COUNT_REQUIRED")
  if expected_exit_intent_count is None:
    raise P1ReconcileError("EXPECTED_EXIT_INTENT_COUNT_REQUIRED")
  return await apply_reconciliation(
    engine,
    confirmation=confirmation,
    expected_approval_count=expected_approval_count,
    expected_plan_count=expected_plan_count,
    expected_exit_intent_count=expected_exit_intent_count,
    now=now,
  )


def render_markdown(report: Mapping[str, Any]) -> str:
  """Render aggregate-only output; identifiers never enter the formatter."""

  reasons = ", ".join(str(item) for item in report.get("reasonCodes", ())) or "none"
  lines = [
    "# T assistant P1 execution-owner legacy reconciliation",
    "",
    f"- Scope: `{report.get('scope', RECONCILE_SCOPE)}`",
    f"- Mode: `{report.get('mode', 'dry-run')}`",
    f"- Safe approvals: `{report.get('safeApprovalCount', 0)}`; "
    f"blocked approvals: `{report.get('blockedApprovalCount', 0)}`",
    f"- Safe plans: `{report.get('safePlanCount', 0)}`; "
    f"blocked plans: `{report.get('blockedPlanCount', 0)}`",
    f"- Safe terminal ExitPlan intents: `{report.get('safeExitIntentCount', 0)}`; "
    f"blocked terminal ExitPlan intents: `{report.get('blockedExitIntentCount', 0)}`",
    f"- Unsettled Agent inbox: `{report.get('unsettledInboxCount', 0)}`",
    f"- Ready to apply: `{str(bool(report.get('readyToApply'))).lower()}`",
    f"- Applied: `{str(bool(report.get('applied'))).lower()}`",
    f"- Reason codes: `{reasons}`",
  ]
  return "\n".join(lines) + "\n"


def _gate_error_report(code: str, *, mode: str = "apply") -> dict[str, Any]:
  discovery = _Discovery(
    safe_approvals=(),
    blocked_approval_count=0,
    safe_plans=(),
    blocked_plan_count=0,
    safe_exit_intents=(),
    blocked_exit_intent_count=0,
    blocked_exit_intent_ids=(),
    unsettled_inbox_count=0,
    missing_tables=(),
    reason_codes=(),
  )
  return _report(discovery, mode=mode, applied=False, extra_reasons=(code,))


async def _run_cli(args: argparse.Namespace) -> int:
  if args.apply:
    missing: str | None = None
    if args.confirmation != CONFIRMATION_WORD:
      missing = "CONFIRMATION_REQUIRED"
    elif args.expected_approval_count is None:
      missing = "EXPECTED_APPROVAL_COUNT_REQUIRED"
    elif args.expected_plan_count is None:
      missing = "EXPECTED_PLAN_COUNT_REQUIRED"
    elif args.expected_exit_intent_count is None:
      missing = "EXPECTED_EXIT_INTENT_COUNT_REQUIRED"
    if missing:
      report = _gate_error_report(missing)
      output = render_markdown(report) if args.format == "markdown" else json.dumps(
        report, ensure_ascii=False, sort_keys=True
      )
      print(output, end="")
      return 2

  try:
    if args.database_url:
      database_url = args.database_url
    else:
      from quantx_infrastructure.runtime_store import resolve_database_url

      database_url = resolve_database_url()
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
      report = await reconcile(
        engine,
        apply=bool(args.apply),
        confirmation=args.confirmation,
        expected_approval_count=args.expected_approval_count,
        expected_plan_count=args.expected_plan_count,
        expected_exit_intent_count=args.expected_exit_intent_count,
      )
    finally:
      await engine.dispose()
  except P1ReconcileError as exc:
    print(f"P1 reconciliation failed: {exc.code}", file=sys.stderr)
    return 2
  except Exception:  # noqa: BLE001 - never print secret-bearing DB errors
    print(f"P1 reconciliation failed: {RECONCILE_DATABASE_ERROR}", file=sys.stderr)
    return 1

  if args.format == "markdown":
    print(render_markdown(report), end="")
  else:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
  if args.apply and not report.get("applied", False):
    return 2
  return 2 if args.require_ready and not report.get("readyToApply", False) else 0


def build_argument_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Dry-run or explicitly gated P1 execution-owner reconciliation"
  )
  parser.add_argument("--database-url", default=None)
  parser.add_argument("--format", choices=("json", "markdown"), default="json")
  parser.add_argument("--apply", action="store_true")
  parser.add_argument(
    "--confirmation",
    "--confirm",
    dest="confirmation",
    default=None,
    help="must equal the fixed maintenance confirmation word",
  )
  parser.add_argument(
    "--expect-approval-count",
    dest="expected_approval_count",
    type=int,
    default=None,
  )
  parser.add_argument(
    "--expect-plan-count",
    dest="expected_plan_count",
    type=int,
    default=None,
  )
  parser.add_argument(
    "--expect-exit-intent-count",
    dest="expected_exit_intent_count",
    type=int,
    default=None,
  )
  parser.add_argument("--require-ready", action="store_true")
  return parser


def main(argv: Iterable[str] | None = None) -> int:
  return asyncio.run(_run_cli(build_argument_parser().parse_args(argv)))


__all__ = [
  "APPROVAL_REASON",
  "CONFIRMATION_WORD",
  "EXIT_INTENT_REASON",
  "TERMINAL_EXIT_PLAN_UNROUTED_INTENT",
  "PLAN_EVENT_KEY_PREFIX",
  "PLAN_EVENT_TYPE",
  "PLAN_REASON",
  "P1ReconcileError",
  "P1_SCHEMA_VERSION",
  "RECONCILE_DATABASE_ERROR",
  "RECONCILE_SCOPE",
  "REQUIRED_TABLES",
  "apply_reconciliation",
  "build_argument_parser",
  "inspect_connection",
  "inspect_terminal_exit_plan_unrouted_intents",
  "main",
  "reconcile",
  "render_markdown",
  "run_inspection",
]


if __name__ == "__main__":
  raise SystemExit(main())
