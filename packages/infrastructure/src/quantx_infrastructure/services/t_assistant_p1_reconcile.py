"""Fail-closed maintenance for the pre-P1 PAPER T-assistant obligations.

This module is intentionally a one-shot maintenance tool.  It does not add a
new owner, alter the Agent contract, or call a broker.  ``inspect_connection``
only discovers and proves aggregate facts.  ``apply_reconciliation`` opens a
new transaction, takes the maintenance fence, repeats discovery, and mutates
only the two proven PAPER legacy record types as one batch.

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

from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

P1_SCHEMA_VERSION = 1
RECONCILE_SCOPE = "P1_PAPER_LEGACY_RECONCILE"
CONFIRMATION_WORD = "P1_PAPER_LEGACY_RECONCILE"
APPROVAL_REASON = "P1_PAPER_STALE_APPROVAL_RECONCILED_ZERO_FILL"
PLAN_REASON = "P1_PAPER_ORPHAN_PLAN_RECONCILED"
PLAN_EVENT_TYPE = "PLAN_CANCELLED"
PLAN_EVENT_KEY_PREFIX = "p1-paper-orphan-reconciled:"
STRATEGY_CLASS = "AshareIntradayTAssistantStrategy"
PAPER_MODE = "paper"
ACTIVE_RUN_STATUSES = frozenset({"PENDING", "RUNNING", "PAUSED"})
TERMINAL_PLAN_STATUSES = frozenset({"COMPLETED", "CANCELLED"})

REQUIRED_TABLES = (
  "strategies",
  "strategy_runs",
  "strategy_trade_intents",
  "pending_trade_orders",
  "strategy_order_correlations",
  "trade_command_outbox",
  "strategy_runtime_events",
  "t_trade_batches",
  "auto_exit_plans",
  "auto_exit_plan_events",
  "agent_report_inbox",
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


def _approval_row_sql(*, for_update: bool) -> str:
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
    "FROM strategy_trade_intents AS intent "
    "JOIN strategy_runs AS run ON run.id = intent.strategy_run_id "
    "JOIN strategies AS strategy ON strategy.id = run.strategy_id "
    "WHERE strategy.class_name = :strategy_class "
    "AND UPPER(CAST(intent.status AS TEXT)) = 'AWAITING_APPROVAL' "
    "ORDER BY intent.created_at, intent.id"
    + suffix
  )


_PLAN_ROW_SQL = (
  "SELECT plan.plan_id, plan.account_id, plan.instrument_code, plan.source_type, "
  "plan.source_id, plan.strategy_run_id, plan.enabled, plan.status, "
  "plan.execution_mode, plan.remaining_volume, plan.pending_client_order_id, "
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
  table_names = ", ".join(f"'{name}'" for name in REQUIRED_TABLES)
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


async def _inbox_unsettled_count(connection: AsyncConnection) -> int:
  result = await connection.execute(
    text(
      "SELECT COUNT(*) AS count_value FROM agent_report_inbox "
      "WHERE processing_status IS NULL "
      "OR UPPER(CAST(processing_status AS TEXT)) NOT IN ('PROCESSED', 'SUPERSEDED')"
    )
  )
  return _normalise_count(_result_scalar(result))


async def _durable_rows(connection: AsyncConnection) -> dict[str, list[dict[str, Any]]]:
  return {
    "pending": await _query_rows(
      connection,
      "SELECT client_order_id, strategy_run_id, intent_id, batch_id, "
      "request_metadata FROM pending_trade_orders",
    ),
    "correlation": await _query_rows(
      connection,
      "SELECT client_order_id, strategy_run_id, intent_id, batch_id, "
      "request_metadata FROM strategy_order_correlations",
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
      "SELECT id, owner_id, strategy_run_id, order_id, metadata "
      "FROM strategy_trade_intents",
    ),
  }


def _approval_proof(
  row: Mapping[str, Any],
  *,
  now: datetime,
  durable: Mapping[str, Sequence[Mapping[str, Any]]],
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
  return _ApprovalCandidate(intent_id, run_id, metadata, created_at), None


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
  if _upper(row.get("execution_mode")) != _upper(PAPER_MODE):
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
  missing = tuple(name for name in REQUIRED_TABLES if name not in existing)
  if missing:
    return _Discovery(
      safe_approvals=(),
      blocked_approval_count=0,
      safe_plans=(),
      blocked_plan_count=0,
      unsettled_inbox_count=0,
      missing_tables=missing,
      reason_codes=("REQUIRED_TABLES_MISSING",),
    )

  approval_rows = await _query_rows(
    connection,
    _approval_row_sql(for_update=for_update and _is_postgresql(connection)),
    {"strategy_class": STRATEGY_CLASS},
  )
  plan_rows = await _query_rows(
    connection,
    _PLAN_ROW_SQL + (" FOR UPDATE OF plan" if for_update and _is_postgresql(connection) else ""),
  )
  # In apply mode candidate rows are locked before the durable evidence is
  # read.  At READ COMMITTED this prevents a concurrent writer from adding an
  # order/correlation after an absence check but before the mutation.
  unsettled = await _inbox_unsettled_count(connection)
  durable = await _durable_rows(connection)
  batches = await _query_rows(
    connection,
    "SELECT batch_id FROM t_trade_batches",
  )
  events = await _plan_event_rows(connection)
  safe_approvals: list[_ApprovalCandidate] = []
  blocked_approvals = 0
  approval_reason_codes: set[str] = set()
  for row in approval_rows:
    candidate, reason = _approval_proof(row, now=now, durable=durable)
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

  reasons: set[str] = set(approval_reason_codes) | plan_reason_codes
  if blocked_approvals:
    reasons.add("BLOCKED_APPROVALS")
  if blocked_plans:
    reasons.add("BLOCKED_PLANS")
  if unsettled:
    reasons.add("UNSETTLED_AGENT_INBOX")
  return _Discovery(
    safe_approvals=tuple(safe_approvals),
    blocked_approval_count=blocked_approvals,
    safe_plans=tuple(safe_plans),
    blocked_plan_count=blocked_plans,
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
    "unsettledInboxCount": discovery.unsettled_inbox_count,
    "applied": bool(applied),
    "readyToApply": bool(ready),
    "reasonCodes": sorted(reasons),
    "missingTables": list(discovery.missing_tables),
    "expectedApprovalCount": expected_approval_count,
    "expectedPlanCount": expected_plan_count,
  }


async def inspect_connection(
  connection: AsyncConnection,
  *,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Inspect one connection without changing it or exposing business IDs."""

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
) -> tuple[int, int]:
  """Apply a previously proven *same-transaction* discovery atomically."""

  intent_table = TradeIntentRecord.__table__
  plan_table = AutoExitPlanRecord.__table__
  event_table = AutoExitPlanEvent.__table__
  audit_at = _utc_iso(now)
  intent_count = 0
  plan_count = 0
  for candidate in discovery.safe_approvals:
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
  return intent_count, plan_count


async def apply_reconciliation(
  engine: AsyncEngine,
  *,
  confirmation: str,
  expected_approval_count: int,
  expected_plan_count: int,
  now: datetime | None = None,
) -> dict[str, Any]:
  """Re-discover, prove, and atomically repair PAPER legacy obligations."""

  if confirmation != CONFIRMATION_WORD:
    raise P1ReconcileError("CONFIRMATION_REQUIRED")
  if not isinstance(expected_approval_count, int) or isinstance(
    expected_approval_count, bool
  ):
    raise P1ReconcileError("EXPECTED_APPROVAL_COUNT_REQUIRED")
  if not isinstance(expected_plan_count, int) or isinstance(expected_plan_count, bool):
    raise P1ReconcileError("EXPECTED_PLAN_COUNT_REQUIRED")
  if expected_approval_count < 0 or expected_plan_count < 0:
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
      report = _report(
        discovery,
        mode="apply",
        applied=False,
        expected_approval_count=expected_approval_count,
        expected_plan_count=expected_plan_count,
        extra_reasons=mismatch_reasons,
      )
      if not report["readyToApply"] or mismatch_reasons:
        await transaction.rollback()
        return report
      await _mutate(connection, discovery, now=checked_at)
      await transaction.commit()
      return _report(
        discovery,
        mode="apply",
        applied=True,
        expected_approval_count=expected_approval_count,
        expected_plan_count=expected_plan_count,
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
  return await apply_reconciliation(
    engine,
    confirmation=confirmation,
    expected_approval_count=expected_approval_count,
    expected_plan_count=expected_plan_count,
    now=now,
  )


def render_markdown(report: Mapping[str, Any]) -> str:
  """Render aggregate-only output; identifiers never enter the formatter."""

  reasons = ", ".join(str(item) for item in report.get("reasonCodes", ())) or "none"
  lines = [
    "# T assistant P1 PAPER legacy reconciliation",
    "",
    f"- Scope: `{report.get('scope', RECONCILE_SCOPE)}`",
    f"- Mode: `{report.get('mode', 'dry-run')}`",
    f"- Safe approvals: `{report.get('safeApprovalCount', 0)}`; "
    f"blocked approvals: `{report.get('blockedApprovalCount', 0)}`",
    f"- Safe plans: `{report.get('safePlanCount', 0)}`; "
    f"blocked plans: `{report.get('blockedPlanCount', 0)}`",
    f"- Unsettled Agent inbox: `{report.get('unsettledInboxCount', 0)}`",
    f"- Ready to apply: `{str(bool(report.get('readyToApply'))).lower()}`",
    f"- Applied: `{str(bool(report.get('applied'))).lower()}`",
    f"- Reason codes: `{reasons}`",
  ]
  return "\n".join(lines) + "\n"


def _gate_error_report(code: str, *, mode: str = "apply") -> dict[str, Any]:
  discovery = _Discovery((), 0, (), 0, 0, (), ())
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
      )
    finally:
      await engine.dispose()
  except P1ReconcileError as exc:
    print(f"P1 reconciliation failed: {exc.code}", file=sys.stderr)
    return 2
  except Exception as exc:  # noqa: BLE001 - never print secret-bearing DB errors
    print(f"P1 reconciliation failed: {type(exc).__name__}", file=sys.stderr)
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
    description="Dry-run or explicitly gated PAPER legacy T-assistant reconciliation"
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
  parser.add_argument("--require-ready", action="store_true")
  return parser


def main(argv: Iterable[str] | None = None) -> int:
  return asyncio.run(_run_cli(build_argument_parser().parse_args(argv)))


__all__ = [
  "APPROVAL_REASON",
  "CONFIRMATION_WORD",
  "PLAN_EVENT_KEY_PREFIX",
  "PLAN_EVENT_TYPE",
  "PLAN_REASON",
  "P1ReconcileError",
  "P1_SCHEMA_VERSION",
  "RECONCILE_SCOPE",
  "REQUIRED_TABLES",
  "apply_reconciliation",
  "build_argument_parser",
  "inspect_connection",
  "main",
  "reconcile",
  "render_markdown",
  "run_inspection",
]


if __name__ == "__main__":
  raise SystemExit(main())
