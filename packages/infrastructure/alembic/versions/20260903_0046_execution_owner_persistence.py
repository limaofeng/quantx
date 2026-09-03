"""Make execution ownership explicit for the durable order facts.

This revision is intentionally an adoption migration.  It reads every row and
proves the owner/environment projection before issuing *any* DDL.  A missing or
ambiguous proof aborts the upgrade, leaving the old schema untouched for
reconciliation.  The old table names are renamed in-place; no compatibility
view or dual-write alias is created.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from alembic import op
from quantx_contracts import PROTOCOL_VERSION
from sqlalchemy import inspect

revision = "20260903_0046"
down_revision = "20260902_0045"
branch_labels = None
depends_on = None

OLD_NEW_RENAMES = (
  ("strategy_trade_intents", "trade_intents"),
  ("strategy_order_correlations", "order_correlations"),
)
PUBLIC_FACT_TABLES = (
  "trade_intents",
  "pending_trade_orders",
  "order_correlations",
  "trade_command_outbox",
  "strategy_runtime_events",
)
SOURCE_PROJECTION_TABLES = (
  "auto_exit_plans",
  "t_trade_batches",
  "trade_confirmation_challenges",
)
OWNER_TYPES = (
  "STRATEGY_RUN",
  "T_ASSISTANT_EXECUTION",
  "ENTRY_PLAN",
  "BOARD_ASSISTANT_EXECUTION",
  "EXIT_PLAN",
  "MANUAL_COMMAND",
)
ENVIRONMENTS = ("PAPER", "LIVE", "BACKTEST")
TERMINAL_EXIT_PLAN_STATUSES = frozenset({"COMPLETED", "CANCELLED"})
TERMINAL_INTENT_STATUSES = frozenset(
  {"COMPLETED", "CANCELLED", "EXPIRED", "FILLED", "REJECTED"}
)
TERMINAL_PENDING_STATUSES = frozenset(
  {"CANCELLED", "EXPIRED", "RECONCILED_ZERO_FILL", "REJECTED"}
)
TERMINAL_OUTBOX_STATUSES = frozenset({"CANCELLED", "EXPIRED", "FAILED"})
TERMINAL_RUNTIME_EVENT_STATUSES = frozenset(
  {"APPLIED", "CANCELLED", "FAILED", "REJECTED"}
)
EXECUTION_APPROVAL_ACTIONS = frozenset(
  {
    "MANUAL_ORDER",
    "STRATEGY_TRADE_INTENT_APPROVAL",
    "T_TRADE_ENTRY_APPROVAL",
    "EXIT_PLAN_SELL_APPROVAL",
    "ENTRY_PLAN_AUTHORIZATION",
    "EXIT_PLAN_AUTHORIZATION",
    "LIQUIDATION_GROUP",
  }
)


def _mapping(value: Any) -> dict[str, Any]:
  if isinstance(value, Mapping):
    return dict(value)
  if isinstance(value, str) and value.strip():
    try:
      decoded = json.loads(value)
    except (TypeError, ValueError):
      return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}
  return {}


def _payload_path(payload: Mapping[str, Any], *path: str) -> Any:
  """Read one documented legacy payload path without recursive guessing."""

  current: Any = payload
  for key in path:
    if not isinstance(current, Mapping):
      return None
    current = current.get(key)
  return current


def _nested_values(value: Any, key: str):
  """Yield values for ``key`` from a JSON projection without guessing names."""

  current = _mapping(value)
  if not current:
    return
  for name, nested in current.items():
    if name == key:
      yield nested
    if isinstance(nested, Mapping):
      yield from _nested_values(nested, key)


def _first_value(
  rows: tuple[Mapping[str, Any], ...],
  *keys: str,
  include_nested: bool = True,
) -> Any:
  for key in keys:
    for row in rows:
      if key in row and row[key] not in (None, ""):
        return row[key]
      if include_nested:
        for nested in _nested_values(row, key):
          if nested not in (None, ""):
            return nested
  return None


def _text(value: Any) -> str:
  return value.strip() if isinstance(value, str) else ""


def _environment(value: Any) -> str | None:
  normalized = _text(getattr(value, "value", value)).upper()
  if normalized in ENVIRONMENTS:
    return normalized
  return None


def _row_environment(
  *rows: Mapping[str, Any],
  include_nested: bool = True,
) -> str | None:
  explicit_values: list[str] = []
  legacy_values: list[str] = []
  for row in rows:
    for key in ("environment", "execution_environment", "source_execution_environment"):
      values = []
      if key in row:
        values.append(row[key])
      if include_nested:
        values.extend(_nested_values(row, key))
      for value in values:
        if value not in (None, ""):
          normalized = _environment(value)
          if normalized is None:
            raise RuntimeError(
              "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: invalid environment"
            )
          explicit_values.append(normalized)
    # ``execution_mode`` is a legacy wire field, not the new persisted field.
    # It is accepted only as durable evidence and is never copied as-is.
    for key in ("execution_mode", "mode"):
      values = []
      if key in row:
        values.append(row[key])
      if include_nested:
        values.extend(_nested_values(row, key))
      for value in values:
        if value in (None, ""):
          continue
        normalized = _environment(value)
        if normalized is None:
          raise RuntimeError(
            "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: invalid environment"
          )
        legacy_values.append(normalized)
  values = set(explicit_values + legacy_values)
  if len(values) > 1:
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: environment conflict")
  return next(iter(values), None)


def _row_owner(
  *rows: Mapping[str, Any],
  include_nested: bool = True,
) -> tuple[str | None, str | None]:
  projections = tuple(rows)
  complete: set[tuple[str, str]] = set()
  for row in projections:
    row_type = _first_value(
      (row,),
      "owner_type",
      "execution_owner_type",
      "source_execution_owner_type",
      include_nested=include_nested,
    )
    row_id = _first_value(
      (row,),
      "owner_id",
      "execution_owner_id",
      "source_execution_owner_id",
      include_nested=include_nested,
    )
    row_type_text = _text(getattr(row_type, "value", row_type))
    row_id_text = _text(row_id)
    if row_type_text and row_id_text:
      complete.add((row_type_text, row_id_text))
  if len(complete) > 1:
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: owner conflict")
  if complete:
    owner_type_text, owner_id_text = next(iter(complete))
    if owner_type_text not in OWNER_TYPES or len(owner_id_text) > 128:
      raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: invalid owner")
    return owner_type_text, owner_id_text
  owner_type = _first_value(
    projections,
    "owner_type",
    "execution_owner_type",
    "source_execution_owner_type",
    include_nested=include_nested,
  )
  owner_id = _first_value(
    projections,
    "owner_id",
    "execution_owner_id",
    "source_execution_owner_id",
    include_nested=include_nested,
  )
  owner_type_text = _text(getattr(owner_type, "value", owner_type))
  owner_id_text = _text(owner_id)
  if not owner_type_text and not owner_id_text:
    return None, None
  if owner_type_text not in OWNER_TYPES:
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: invalid owner")
  if not owner_id_text:
    if owner_type_text == "STRATEGY_RUN":
      return owner_type_text, None
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: owner id missing")
  if len(owner_id_text) > 128 or owner_id_text != owner_id_text.strip():
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: invalid owner id")
  return owner_type_text, owner_id_text


def _strategy_runs(bind) -> dict[str, Mapping[str, Any]]:
  tables = set(inspect(bind).get_table_names())
  if "strategy_runs" not in tables:
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy_runs missing")
  return {
    str(row["id"]): row
    for row in bind.execute(sa.text("SELECT id, mode FROM strategy_runs")).mappings()
    if row.get("id") not in (None, "")
  }


def _resolve_owner_environment(
  row: Mapping[str, Any],
  strategy_runs: Mapping[str, Mapping[str, Any]],
  *related: Mapping[str, Any],
  include_nested: bool = False,
) -> tuple[dict[str, Any], tuple[str, str, str]]:
  projections = (row, *related)
  owner_type, owner_id = _row_owner(
    *projections,
    include_nested=include_nested,
  )
  strategy_run_id = _text(row.get("strategy_run_id"))
  if owner_type is None:
    if not strategy_run_id:
      raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: owner missing")
    owner_type, owner_id = "STRATEGY_RUN", strategy_run_id
  elif owner_type == "STRATEGY_RUN" and not owner_id:
    owner_id = strategy_run_id
  if owner_type == "STRATEGY_RUN":
    if not owner_id or owner_id not in strategy_runs:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy run proof missing"
      )
    run_environment = _environment(strategy_runs[owner_id].get("mode"))
    if run_environment is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy run mode missing"
      )
  else:
    run_environment = None
  environment = _row_environment(
    *projections,
    include_nested=include_nested,
  )
  if environment is None:
    environment = run_environment
  if environment is None:
    raise RuntimeError("EXECUTION_OWNER_MIGRATION_AMBIGUOUS: environment missing")
  if run_environment is not None and environment != run_environment:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: environment conflicts with strategy run"
    )
  if strategy_run_id and owner_type == "STRATEGY_RUN" and strategy_run_id != owner_id:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy_run_id owner mismatch"
    )
  update: dict[str, Any] = {
    "owner_type": owner_type,
    "owner_id": owner_id,
    "environment": environment,
  }
  if "strategy_run_id" in row and strategy_run_id and owner_type != "STRATEGY_RUN":
    # This is a legacy nullable association, not an owner fallback.  It must
    # be removed for non-strategy owners so the database check is meaningful.
    update["strategy_run_id"] = None
  return update, (owner_type, owner_id, environment)


def _load_rows(bind, table_name: str) -> list[Mapping[str, Any]]:
  return list(bind.execute(sa.text(f'SELECT * FROM "{table_name}"')).mappings())


def _columns(bind, table_name: str) -> set[str]:
  return {str(item["name"]) for item in inspect(bind).get_columns(table_name)}


def _primary_key(table_name: str) -> str:
  return {
    "trade_intents": "id",
    "pending_trade_orders": "client_order_id",
    "order_correlations": "id",
    "trade_command_outbox": "message_id",
    "strategy_runtime_events": "event_id",
    "auto_exit_plans": "plan_id",
    "t_trade_batches": "batch_id",
    "trade_confirmation_challenges": "id",
  }[table_name]


def _linked_indexes(
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
) -> tuple[
  dict[str, Mapping[str, Any]],
  dict[str, tuple[Mapping[str, Any], ...]],
  dict[str, tuple[Mapping[str, Any], ...]],
]:
  intents = {
    str(row.get("id")): row
    for row in rows_by_table.get("trade_intents", [])
    if row.get("id") not in (None, "")
  }
  pending_by_client: dict[str, list[Mapping[str, Any]]] = {}
  correlation_by_client: dict[str, list[Mapping[str, Any]]] = {}
  for row in rows_by_table.get("pending_trade_orders", []):
    client_order_id = _text(row.get("client_order_id"))
    if client_order_id:
      pending_by_client.setdefault(client_order_id, []).append(row)
  for row in rows_by_table.get("order_correlations", []):
    client_order_id = _text(row.get("client_order_id"))
    if client_order_id:
      correlation_by_client.setdefault(client_order_id, []).append(row)
  return (
    intents,
    {key: tuple(value) for key, value in pending_by_client.items()},
    {key: tuple(value) for key, value in correlation_by_client.items()},
  )


def _index_rows(
  rows: list[Mapping[str, Any]],
  key: str,
) -> dict[str, tuple[Mapping[str, Any], ...]]:
  """Index durable rows without silently collapsing duplicate identities."""

  indexed: dict[str, list[Mapping[str, Any]]] = {}
  for row in rows:
    value = _text(row.get(key))
    if value:
      indexed.setdefault(value, []).append(row)
  return {value: tuple(items) for value, items in indexed.items()}


def _exact_order_relation_owner(
  row: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  intents: Mapping[str, Mapping[str, Any]],
  pending_rows: tuple[Mapping[str, Any], ...],
  correlation_rows: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], tuple[str, str, str]]:
  """Resolve a public fact only through its durable order relation.

  ``pending_trade_orders`` and ``order_correlations`` are the authoritative
  bridge for outbox/runtime facts.  In particular, JSON request metadata is
  deliberately excluded from these projections; it can identify a command
  kind or broker id, but it cannot establish ownership.
  """

  owners: list[tuple[str, str, str]] = []
  client_order_id = _text(row.get("client_order_id"))
  for pending in pending_rows:
    intent = intents.get(_text(pending.get("intent_id")))
    related = (intent,) if intent is not None else ()
    _, owner = _resolve_owner_environment(
      pending,
      strategy_runs,
      *related,
      include_nested=False,
    )
    owners.append(owner)
  for correlation in correlation_rows:
    correlation_client_id = _text(correlation.get("client_order_id"))
    related_pending = tuple(
      pending
      for pending in pending_rows
      if _text(pending.get("client_order_id")) == correlation_client_id
    )
    intent = intents.get(_text(correlation.get("intent_id")))
    related = ((intent,) if intent is not None else ()) + related_pending
    _, owner = _resolve_owner_environment(
      correlation,
      strategy_runs,
      *related,
      include_nested=False,
    )
    owners.append(owner)
  if not owners:
    relation_kind = "client order " + client_order_id if client_order_id else "fact"
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: durable order relation missing for "
      f"{relation_kind}"
    )
  if len(set(owners)) != 1:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: durable order relation owner conflict"
    )
  owner = owners[0]
  update: dict[str, Any] = {
    "owner_type": owner[0],
    "owner_id": owner[1],
    "environment": owner[2],
  }
  row_strategy_run_id = _text(row.get("strategy_run_id"))
  if row_strategy_run_id:
    if owner[0] == "STRATEGY_RUN" and row_strategy_run_id != owner[1]:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: fact strategy run owner mismatch"
      )
    if owner[0] != "STRATEGY_RUN":
      update["strategy_run_id"] = None
  return update, owner


def _command_kind(row: Mapping[str, Any]) -> str:
  payload = _mapping(row.get("payload"))
  value = row.get("command_kind") or row.get("command_type")
  if value in (None, ""):
    value = payload.get("command_kind") or payload.get("command_type")
  return _text(value).upper()


def _broker_order_id(row: Mapping[str, Any]) -> str:
  payload = _mapping(row.get("payload"))
  value = row.get("broker_order_id")
  if value in (None, ""):
    value = payload.get("broker_order_id") or payload.get("order_id")
  return _text(value)


def _resolve_outbox_owner_environment(
  row: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  intents: Mapping[str, Mapping[str, Any]],
  pending_by_client: Mapping[str, tuple[Mapping[str, Any], ...]],
  correlation_by_client: Mapping[str, tuple[Mapping[str, Any], ...]],
  correlations: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], tuple[str, str, str]]:
  if _command_kind(row) in {"CANCEL", "CANCEL_ORDER"}:
    broker_order_id = _broker_order_id(row)
    account_id = _text(row.get("account_id"))
    if not broker_order_id or not account_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: cancel broker correlation missing"
      )
    matches = tuple(
      correlation
      for correlation in correlations
      if _text(correlation.get("broker_order_id")) == broker_order_id
      and _text(correlation.get("account_id")) == account_id
    )
    if len(matches) > 1:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: cancel broker correlation is not "
        "unique"
      )
    if matches:
      correlation = matches[0]
      client_order_id = _text(correlation.get("client_order_id"))
      return _exact_order_relation_owner(
        row,
        strategy_runs=strategy_runs,
        intents=intents,
        pending_rows=pending_by_client.get(client_order_id, ()),
        correlation_rows=(correlation,),
      )

    # 0045 may contain a terminal CANCEL whose target was durably projected in
    # ``pending_trade_orders`` before the correlation row was written.  The
    # target's account plus broker id is an authoritative order fact; require
    # it to be unique and still resolve its public owner chain.  Never infer
    # ownership from the CANCEL payload or its request metadata.
    pending_matches = tuple(
      pending
      for pending_rows in pending_by_client.values()
      for pending in pending_rows
      if _text(pending.get("account_id")) == account_id
      and _text(pending.get("broker_order_id")) == broker_order_id
    )
    if len(pending_matches) != 1:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: cancel broker target is not "
        "uniquely persisted"
      )
    pending = pending_matches[0]
    client_order_id = _text(pending.get("client_order_id"))
    return _exact_order_relation_owner(
      row,
      strategy_runs=strategy_runs,
      intents=intents,
      pending_rows=(pending,),
      correlation_rows=(),
    )

  client_order_id = _text(row.get("client_order_id"))
  return _exact_order_relation_owner(
    row,
    strategy_runs=strategy_runs,
    intents=intents,
    pending_rows=pending_by_client.get(client_order_id, ()),
    correlation_rows=correlation_by_client.get(client_order_id, ()),
  )


def _resolve_runtime_event_owner_environment(
  row: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  intents: Mapping[str, Mapping[str, Any]],
  pending_by_client: Mapping[str, tuple[Mapping[str, Any], ...]],
  correlation_by_client: Mapping[str, tuple[Mapping[str, Any], ...]],
) -> tuple[dict[str, Any], tuple[str, str, str]]:
  client_order_id = _text(row.get("client_order_id"))
  return _exact_order_relation_owner(
    row,
    strategy_runs=strategy_runs,
    intents=intents,
    pending_rows=pending_by_client.get(client_order_id, ()),
    correlation_rows=correlation_by_client.get(client_order_id, ()),
  )


def _canonical_rows(bind) -> tuple[dict[str, list[Mapping[str, Any]]], dict[str, str]]:
  """Load the complete adoption scope before any schema mutation."""

  inspector = inspect(bind)
  tables = set(inspector.get_table_names())
  sources: dict[str, str] = {}
  for old_name, new_name in OLD_NEW_RENAMES:
    if old_name in tables and new_name in tables:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: both legacy and authoritative "
        f"tables exist ({old_name}, {new_name})"
      )
    if old_name in tables:
      sources[new_name] = old_name
    elif new_name in tables:
      sources[new_name] = new_name
    else:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: required table missing "
        f"({old_name} or {new_name})"
      )
  # Rename targets were already resolved above.  In particular, an existing
  # 0045 database legitimately has only ``strategy_order_correlations``; do
  # not check the canonical target name a second time before the rename.
  for table_name in (
    *(
      table_name
      for table_name in PUBLIC_FACT_TABLES[1:]
      if table_name not in sources
    ),
    *SOURCE_PROJECTION_TABLES,
  ):
    if table_name not in tables:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: required table missing "
        f"({table_name})"
      )
    sources[table_name] = table_name
  for table_name in (
    "pending_trade_orders",
    "order_correlations",
    "auto_exit_plans",
    "t_trade_batches",
  ):
    columns = _columns(bind, sources[table_name])
    if "execution_mode" in columns and "environment" in columns:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: duplicate environment projection "
        f"on {table_name}"
      )

  rows = {
    canonical_name: _load_rows(bind, source_name)
    for canonical_name, source_name in sources.items()
  }
  return rows, sources


def _intent_idempotency_key(row: Mapping[str, Any]) -> str:
  value = _first_value((row,), "idempotency_key")
  if value in (None, ""):
    value = _first_value((row,), "intent_idempotency_key")
  if value in (None, ""):
    # The primary key is an existing durable identity, not a generated default
    # or owner fallback.  It is the only safe identity available in the
    # pre-P1 schema when no separate retry key was persisted.
    value = row.get("id")
  key = _text(value)
  if not key or len(key) > 128 or any(ord(character) <= 31 for character in key):
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: intent idempotency identity missing"
    )
  return key


def _preflight(bind) -> dict[str, list[tuple[str, dict[str, Any]]]]:
  """Return all deterministic writes; raise before DDL for every ambiguity."""

  rows_by_table, _ = _canonical_rows(bind)
  for table_name in (
    "pending_trade_orders",
    "order_correlations",
    "trade_command_outbox",
  ):
    seen_client_ids: set[str] = set()
    for row in rows_by_table[table_name]:
      client_order_id = _text(row.get("client_order_id"))
      if not client_order_id:
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: client_order_id missing"
        )
      if client_order_id in seen_client_ids:
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: duplicate client_order_id"
        )
      seen_client_ids.add(client_order_id)
  strategy_runs = _strategy_runs(bind)
  intents, pending_by_client, correlation_by_client = _linked_indexes(rows_by_table)
  exit_plan_intent_owners = _exit_plan_intent_owners(rows_by_table, strategy_runs)
  intent_projections: dict[str, Mapping[str, Any]] = dict(intents)
  for intent_id, (owner_type, owner_id, environment) in exit_plan_intent_owners.items():
    projection = dict(intents[intent_id])
    projection.update(
      {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "environment": environment,
        "strategy_run_id": None,
      }
    )
    intent_projections[intent_id] = projection
  updates: dict[str, list[tuple[str, dict[str, Any]]]] = {
    table_name: []
    for table_name in (*PUBLIC_FACT_TABLES, *SOURCE_PROJECTION_TABLES)
  }
  intent_keys: set[tuple[str, str, str, str]] = set()
  challenge_by_id = _index_rows(
    rows_by_table["trade_confirmation_challenges"],
    "id",
  )
  outbox_by_message_id = _index_rows(
    rows_by_table["trade_command_outbox"],
    "message_id",
  )

  for row in rows_by_table["trade_intents"]:
    intent_id = _text(row.get("id"))
    if intent_id in exit_plan_intent_owners:
      owner = exit_plan_intent_owners[intent_id]
      update = {
        "owner_type": owner[0],
        "owner_id": owner[1],
        "environment": owner[2],
        "strategy_run_id": None,
      }
    else:
      update, owner = _resolve_owner_environment(
        row,
        strategy_runs,
        include_nested=False,
      )
    key = _intent_idempotency_key(row)
    unique_key = (*owner, key)
    if unique_key in intent_keys:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: duplicate owner intent idempotency"
      )
    intent_keys.add(unique_key)
    update["idempotency_key"] = key
    updates["trade_intents"].append((str(row["id"]), update))

  pending_projections: dict[str, list[Mapping[str, Any]]] = {}
  for row in rows_by_table["pending_trade_orders"]:
    intent_id = _text(row.get("intent_id"))
    if intent_id:
      related = intent_projections.get(intent_id)
      if related is None:
        raise _pending_owner_error(
          row,
          "pending intent proof missing",
        )
      update, _ = _resolve_owner_environment(
        row,
        strategy_runs,
        related,
        include_nested=False,
      )
    else:
      update, _ = _resolve_intentless_pending_owner(
        row,
        strategy_runs=strategy_runs,
        challenge_by_id=challenge_by_id,
        outbox_by_message_id=outbox_by_message_id,
        outbox_rows=rows_by_table["trade_command_outbox"],
      )
    owner_type = _text(update.get("owner_type")).upper()
    strategy_order_id = _text(row.get("strategy_order_id"))
    strategy_run_id = _text(row.get("strategy_run_id"))
    if owner_type == "STRATEGY_RUN":
      if not strategy_order_id or not intent_id:
        raise _pending_owner_error(row, "strategy pending identity missing")
    elif owner_type == "EXIT_PLAN":
      if not intent_id:
        raise _pending_owner_error(row, "exit-plan pending intent missing")
      if strategy_order_id:
        raise _pending_owner_error(
          row,
          "exit-plan pending strategy_order_id must be absent",
        )
    elif owner_type == "MANUAL_COMMAND":
      if intent_id or strategy_order_id or strategy_run_id:
        raise _pending_owner_error(
          row,
          "manual pending strategy identity must be absent",
        )
    else:
      raise _pending_owner_error(
        row,
        f"unsupported pending owner type: {owner_type or '<missing>'}",
      )
    projected = dict(row)
    projected.update(update)
    pending_projections.setdefault(
      _text(row.get("client_order_id")),
      [],
    ).append(projected)
    updates["pending_trade_orders"].append((str(row["client_order_id"]), update))

  pending_by_client = {
    key: tuple(value) for key, value in pending_projections.items()
  }

  for row in rows_by_table["order_correlations"]:
    related = intent_projections.get(_text(row.get("intent_id")))
    pending_rows = pending_by_client.get(_text(row.get("client_order_id")), ())
    related_rows = ((related,) if related is not None else ()) + pending_rows
    update, owner = _resolve_owner_environment(
      row,
      strategy_runs,
      *related_rows,
      include_nested=False,
    )
    correlation_intent_id = _text(row.get("intent_id"))
    correlation_strategy_order_id = _text(row.get("strategy_order_id"))
    correlation_strategy_run_id = _text(row.get("strategy_run_id"))
    if owner[0] == "STRATEGY_RUN":
      if not correlation_strategy_order_id or not correlation_intent_id:
        raise _correlation_owner_error(
          row,
          "strategy correlation identity missing",
        )
    elif owner[0] == "EXIT_PLAN":
      if not correlation_intent_id:
        raise _correlation_owner_error(
          row,
          "exit-plan correlation intent missing",
        )
      if correlation_strategy_order_id:
        raise _correlation_owner_error(
          row,
          "exit-plan correlation strategy_order_id must be absent",
        )
    elif owner[0] == "MANUAL_COMMAND":
      if (
        correlation_intent_id
        or correlation_strategy_order_id
        or correlation_strategy_run_id
      ):
        raise _correlation_owner_error(
          row,
          "manual correlation strategy identity must be absent",
        )
    else:
      raise _correlation_owner_error(
        row,
        f"unsupported correlation owner type: {owner[0] or '<missing>'}",
      )
    updates["order_correlations"].append((str(row["id"]), update))

  for row in rows_by_table["trade_command_outbox"]:
    update, _ = _resolve_outbox_owner_environment(
      row,
      strategy_runs=strategy_runs,
      intents=intent_projections,
      pending_by_client=pending_by_client,
      correlation_by_client=correlation_by_client,
      correlations=rows_by_table["order_correlations"],
    )
    updates["trade_command_outbox"].append((str(row["message_id"]), update))

  for row in rows_by_table["strategy_runtime_events"]:
    update, _ = _resolve_runtime_event_owner_environment(
      row,
      strategy_runs=strategy_runs,
      intents=intent_projections,
      pending_by_client=pending_by_client,
      correlation_by_client=correlation_by_client,
    )
    updates["strategy_runtime_events"].append((str(row["event_id"]), update))

  _preflight_exit_plans(rows_by_table, strategy_runs, updates)
  _preflight_batches(rows_by_table, strategy_runs, updates)
  _preflight_challenges(rows_by_table, strategy_runs, updates)
  return updates


def _explicit_source_owner(
  row: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None]:
  """Read only the dedicated source-owner columns from a source projection."""

  values = tuple(
    _text(row.get(key))
    for key in (
      "source_execution_owner_type",
      "source_execution_owner_id",
      "source_execution_environment",
    )
  )
  if not any(values):
    return None, None, None
  source_type, source_id, source_environment = values
  if not all(values):
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: source owner projection incomplete"
    )
  source_environment = _environment(source_environment)
  if source_type not in OWNER_TYPES or source_environment is None:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: source owner projection invalid"
    )
  if len(source_id) > 128 or source_id != source_id.strip():
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: source owner id invalid"
    )
  return source_type, source_id, source_environment


def _plan_environment_proof(row: Mapping[str, Any]) -> str | None:
  """Use only direct ExitPlan/T-batch environment evidence.

  The order's environment is checked for consistency, but never used to infer
  the source owner.  Nested ``plan_state``/payload owner fields are excluded.
  """

  values: list[str] = []
  for key in ("environment", "execution_mode"):
    value = row.get(key)
    if value in (None, ""):
      continue
    normalized = _environment(value)
    if normalized is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: direct source environment invalid"
      )
    values.append(normalized)
  if len(set(values)) > 1:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: direct source environment conflict"
    )
  return values[0] if values else None


def _strategy_run_owner(
  run_id: str,
  strategy_runs: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str]:
  run = strategy_runs.get(run_id)
  if run is None:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy run proof missing"
    )
  environment = _environment(run.get("mode"))
  if environment is None:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: strategy run mode missing"
    )
  return "STRATEGY_RUN", run_id, environment


def _source_owner_from_plan(
  row: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  batches: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str]:
  source_type, source_id, source_environment = _explicit_source_owner(row)
  if source_type is not None:
    plan_environment = _plan_environment_proof(row)
    if plan_environment is not None and plan_environment != source_environment:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan environment conflict"
      )
    return source_type, source_id, source_environment

  run_id = _text(row.get("strategy_run_id"))
  if run_id:
    owner = _strategy_run_owner(run_id, strategy_runs)
    plan_environment = _plan_environment_proof(row)
    if plan_environment is not None and plan_environment != owner[2]:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan strategy environment "
        "conflict"
      )
    return owner

  source_kind = _text(row.get("source_type")).upper()
  source_id = _text(row.get("source_id"))
  if not source_id:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan source identity missing"
    )
  if source_kind == "T_TRADE_BATCH":
    batch = batches.get(source_id)
    if batch is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan batch proof missing"
      )
    return _batch_source_owner(batch, strategy_runs)
  if source_kind == "ENTRY_PLAN":
    owner_environment = _plan_environment_proof(row)
    if owner_environment is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: entry plan environment missing"
      )
    return "ENTRY_PLAN", source_id, owner_environment
  if source_kind in {"MANUAL_POSITION", "MANUAL_LIQUIDATION"}:
    owner_environment = _plan_environment_proof(row)
    if owner_environment is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: manual exit plan environment missing"
      )
    if source_kind == "MANUAL_LIQUIDATION":
      # 0045's ``source_id`` was the per-instrument plan id.  The durable
      # command identity for a liquidation group is the row's group_id.
      group_id = _text(row.get("group_id"))
      if not group_id:
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: liquidation group identity missing"
        )
      return "MANUAL_COMMAND", group_id, owner_environment
    # MANUAL_POSITION source_id is the persisted conditional-order identity;
    # do not replace it with the generated exit-plan id.
    return "MANUAL_COMMAND", source_id, owner_environment
  raise RuntimeError(
    "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: unsupported exit plan source"
  )


def _batch_source_owner(
  row: Mapping[str, Any],
  strategy_runs: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str]:
  source_type, source_id, source_environment = _explicit_source_owner(row)
  if source_type is not None:
    return source_type, source_id, source_environment
  run_id = _text(row.get("strategy_run_id"))
  if not run_id:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: T-trade batch source missing"
    )
  owner = _strategy_run_owner(run_id, strategy_runs)
  batch_environment = _plan_environment_proof(row)
  if batch_environment is not None and batch_environment != owner[2]:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: T-trade batch environment conflict"
    )
  return owner


def _plan_owner_error(row: Mapping[str, Any], detail: str) -> RuntimeError:
  """Attach the durable exit-plan identity to source-proof failures."""

  plan_id = _text(row.get("plan_id")) or "<missing>"
  return RuntimeError(
    "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: "
    f"{detail} (table=auto_exit_plans, plan_id={plan_id})"
  )


def _terminal_strategy_run_tombstone_owner(
  row: Mapping[str, Any],
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
  strategy_runs: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str] | None:
  """Prove a terminal plan can retain a deleted StrategyRun identity.

  The source ``StrategyRun`` row is intentionally not required for this
  narrow historical-tombstone case.  The plan's direct terminal status,
  direct environment, empty pending state, and absence of active public
  execution facts are the complete proof.  No error text, source-id prefix, or
  JSON metadata is used to classify a tombstone.
  """

  if _text(row.get("status")).upper() not in TERMINAL_EXIT_PLAN_STATUSES:
    return None
  run_id_value = row.get("strategy_run_id")
  run_id = _text(run_id_value)
  if not run_id or run_id in strategy_runs:
    return None
  if isinstance(run_id_value, str) and run_id_value != run_id:
    raise _plan_owner_error(row, "terminal strategy run identity invalid")
  plan_id = _text(row.get("plan_id"))
  if not plan_id:
    raise _plan_owner_error(row, "terminal strategy run plan identity missing")
  try:
    environment = _plan_environment_proof(row)
  except RuntimeError as exc:
    raise _plan_owner_error(row, str(exc)) from exc
  if environment is None:
    raise _plan_owner_error(row, "terminal strategy run environment missing")

  state_value = row.get("plan_state")
  state = _mapping(state_value)
  if isinstance(state_value, str) and state_value.strip() and not state:
    raise _plan_owner_error(row, "terminal strategy run plan state invalid")
  for key in (
    "pending_intent_id",
    "pending_order_id",
    "pending_client_order_id",
  ):
    if _text(row.get(key)) or _text(state.get(key)):
      raise _plan_owner_error(
        row,
        f"terminal strategy run pending identity present ({key})",
      )

  try:
    source_owner = _explicit_source_owner(row)
  except RuntimeError as exc:
    raise _plan_owner_error(row, str(exc)) from exc
  expected_owner = ("STRATEGY_RUN", run_id, environment)
  if source_owner[0] is not None and source_owner != expected_owner:
    raise _plan_owner_error(row, "terminal strategy run source owner conflict")

  for other_plan in rows_by_table["auto_exit_plans"]:
    if _text(other_plan.get("plan_id")) == plan_id:
      continue
    if _text(other_plan.get("strategy_run_id")) != run_id:
      continue
    if _text(other_plan.get("status")).upper() not in TERMINAL_EXIT_PLAN_STATUSES:
      raise _plan_owner_error(row, "terminal strategy run active sibling plan exists")

  owner_refs = {
    ("EXIT_PLAN", plan_id),
    ("STRATEGY_RUN", run_id),
  }
  plan_intent_ids: set[str] = set()
  for intent in rows_by_table["trade_intents"]:
    intent_owner = (
      _text(intent.get("owner_type")),
      _text(intent.get("owner_id")),
    )
    if intent_owner == ("EXIT_PLAN", plan_id):
      intent_id = _text(intent.get("id"))
      if not intent_id:
        raise _plan_owner_error(row, "terminal strategy run intent identity missing")
      plan_intent_ids.add(intent_id)
      if _text(intent.get("status")).upper() not in TERMINAL_INTENT_STATUSES:
        raise _plan_owner_error(row, "terminal strategy run active intent exists")
    elif intent_owner == ("STRATEGY_RUN", run_id) and (
      _text(intent.get("status")).upper() not in TERMINAL_INTENT_STATUSES
    ):
      raise _plan_owner_error(
        row,
        "terminal strategy run active strategy intent exists",
      )

  related_clients: set[str] = set()
  pending_status_by_client: dict[str, str] = {}
  for pending in rows_by_table["pending_trade_orders"]:
    pending_owner = (
      _text(pending.get("owner_type")),
      _text(pending.get("owner_id")),
    )
    pending_intent_id = _text(pending.get("intent_id"))
    client_order_id = _text(pending.get("client_order_id"))
    if (
      pending_owner not in owner_refs
      and pending_intent_id not in plan_intent_ids
      and client_order_id not in related_clients
    ):
      continue
    if client_order_id:
      related_clients.add(client_order_id)
      status = _text(pending.get("status")).upper()
      pending_status_by_client[client_order_id] = status
      if status not in TERMINAL_PENDING_STATUSES:
        raise _plan_owner_error(row, "terminal strategy run active pending order exists")
    else:
      raise _plan_owner_error(row, "terminal strategy run pending client identity missing")

  for correlation in rows_by_table["order_correlations"]:
    correlation_owner = (
      _text(correlation.get("owner_type")),
      _text(correlation.get("owner_id")),
    )
    correlation_intent_id = _text(correlation.get("intent_id"))
    client_order_id = _text(correlation.get("client_order_id"))
    if not (
      correlation_owner in owner_refs
      or correlation_intent_id in plan_intent_ids
      or client_order_id in related_clients
    ):
      continue
    if not client_order_id:
      raise _plan_owner_error(row, "terminal strategy run correlation client identity missing")
    related_clients.add(client_order_id)
    if pending_status_by_client.get(client_order_id) not in TERMINAL_PENDING_STATUSES:
      raise _plan_owner_error(row, "terminal strategy run active correlation exists")

  for outbox in rows_by_table["trade_command_outbox"]:
    outbox_owner = (
      _text(outbox.get("owner_type")),
      _text(outbox.get("owner_id")),
    )
    client_order_id = _text(outbox.get("client_order_id"))
    if outbox_owner not in owner_refs and client_order_id not in related_clients:
      continue
    if _text(outbox.get("delivery_status")).upper() not in TERMINAL_OUTBOX_STATUSES:
      raise _plan_owner_error(row, "terminal strategy run active outbox exists")

  for event in rows_by_table["strategy_runtime_events"]:
    event_owner = (
      _text(event.get("owner_type")),
      _text(event.get("owner_id")),
    )
    client_order_id = _text(event.get("client_order_id"))
    event_run_id = _text(event.get("strategy_run_id"))
    if (
      event_owner not in owner_refs
      and event_run_id != run_id
      and client_order_id not in related_clients
    ):
      continue
    if (
      _text(event.get("application_status")).upper()
      not in TERMINAL_RUNTIME_EVENT_STATUSES
    ):
      raise _plan_owner_error(row, "terminal strategy run active runtime event exists")

  return expected_owner


def _exit_plan_intent_error(intent_id: Any, detail: str) -> RuntimeError:
  """Add the legacy intent table and row identity to an ambiguity."""

  rendered_id = _text(intent_id) or "<missing>"
  return RuntimeError(
    "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: "
    f"{detail} (table=trade_intents, intent_id={rendered_id})"
  )


def _exit_plan_environment(
  plan: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  batches: Mapping[str, Mapping[str, Any]],
) -> str:
  """Resolve a plan environment from durable, non-JSON source columns."""

  environment = _plan_environment_proof(plan)
  if environment is None:
    environment = _source_owner_from_plan(
      plan,
      strategy_runs=strategy_runs,
      batches=batches,
    )[2]
  if environment not in ENVIRONMENTS:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan environment invalid"
    )
  return environment


def _validate_exit_plan_intent(
  intent: Mapping[str, Any],
  plan: Mapping[str, Any],
  plan_environment: str,
  *,
  pending_link: bool,
  require_strategy_run_absent: bool,
) -> None:
  """Prove that an intent can belong to the referenced durable exit plan."""

  intent_id = intent.get("id")
  detail_prefix = "exit plan pending intent" if pending_link else "exit plan direct intent"
  plan_id = _text(plan.get("plan_id"))
  if _text(intent.get("direction")).upper() != "SELL":
    raise _exit_plan_intent_error(
      intent_id,
      f"{detail_prefix} is not SELL",
    )
  if _text(intent.get("account_id")) != _text(plan.get("account_id")):
    raise _exit_plan_intent_error(
      intent_id,
      f"{detail_prefix} account conflict",
    )
  if _text(intent.get("instrument_code")).upper() != _text(
    plan.get("instrument_code")
  ).upper():
    raise _exit_plan_intent_error(
      intent_id,
      f"{detail_prefix} instrument conflict",
    )

  direct_environment_value = intent.get("environment")
  if direct_environment_value not in (None, ""):
    direct_environment = _environment(direct_environment_value)
    if direct_environment is None:
      raise _exit_plan_intent_error(
        intent_id,
        f"{detail_prefix} environment invalid",
      )
    if direct_environment != plan_environment:
      raise _exit_plan_intent_error(
        intent_id,
        f"{detail_prefix} environment conflict",
      )

  if require_strategy_run_absent and _text(intent.get("strategy_run_id")):
    raise _exit_plan_intent_error(
      intent_id,
      f"{detail_prefix} strategy_run_id must be absent",
    )

  if pending_link:
    direct_owner_type = _text(intent.get("owner_type"))
    direct_owner_id_value = intent.get("owner_id")
    direct_owner_id = _text(direct_owner_id_value)
    if direct_owner_type and direct_owner_type != "EXIT_PLAN":
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan intent owner conflict",
      )
    if direct_owner_type and not direct_owner_id:
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan intent owner id missing",
      )
    if isinstance(direct_owner_id_value, str) and (
      direct_owner_id_value != direct_owner_id
    ):
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan intent owner id invalid",
      )
    if direct_owner_id and direct_owner_id != plan_id:
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan intent owner id conflict",
      )


def _exit_plan_intent_owners(
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
  strategy_runs: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, str, str]]:
  """Build the pre-DDL map for all intents owned by a durable exit plan.

  A plan's durable ``plan_state.pending_intent_id`` is the authoritative
  relation for an existing managed SELL, but historical terminal intents can
  retain the direct ``owner_type``/``owner_id`` projection after that link is
  cleared.  Those direct columns are matched against the exact durable
  ``auto_exit_plans.plan_id`` row.  JSON owner/environment fields on the intent
  or on an order payload are intentionally not consulted.  This map is applied
  before public facts are resolved, so the identity trigger installed later
  can never observe an intermediate STRATEGY_RUN owner.
  """

  batches = {
    _text(row.get("batch_id")): row
    for row in rows_by_table["t_trade_batches"]
    if _text(row.get("batch_id"))
  }
  intents = {
    _text(row.get("id")): row
    for row in rows_by_table["trade_intents"]
    if _text(row.get("id"))
  }
  plans: dict[str, Mapping[str, Any]] = {}
  for plan in rows_by_table["auto_exit_plans"]:
    plan_id_value = plan.get("plan_id")
    plan_id = _text(plan_id_value)
    if not plan_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan identity missing"
      )
    if isinstance(plan_id_value, str) and plan_id_value != plan_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan identity invalid"
      )
    if plan_id in plans:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: duplicate exit plan identity "
        f"(plan_id={plan_id})"
      )
    plans[plan_id] = plan

  references: dict[str, tuple[Mapping[str, Any], str]] = {}
  for plan in rows_by_table["auto_exit_plans"]:
    plan_id = _text(plan.get("plan_id"))
    state = _mapping(plan.get("plan_state"))
    intent_id = _text(state.get("pending_intent_id"))
    if not intent_id:
      continue
    if not plan_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan identity missing"
      )
    if intent_id in references:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: pending intent references "
        "multiple exit plans "
        f"(table=trade_intents, intent_id={intent_id})"
      )
    plan_environment = _exit_plan_environment(
      plan,
      strategy_runs=strategy_runs,
      batches=batches,
    )
    references[intent_id] = (plan, plan_environment)

  owners: dict[str, tuple[str, str, str]] = {}
  for intent_id, (plan, plan_environment) in references.items():
    intent = intents.get(intent_id)
    if intent is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: exit plan pending intent proof "
        f"missing (table=trade_intents, intent_id={intent_id})"
      )
    _validate_exit_plan_intent(
      intent,
      plan,
      plan_environment,
      pending_link=True,
      require_strategy_run_absent=False,
    )
    owners[intent_id] = ("EXIT_PLAN", _text(plan.get("plan_id")), plan_environment)

  # A terminal/non-current intent no longer appears in plan_state, but its
  # direct persisted owner still points at the plan that created it.  Match
  # only these dedicated columns; intent metadata is not ownership evidence.
  for intent in rows_by_table["trade_intents"]:
    intent_id = _text(intent.get("id"))
    direct_owner_type = _text(intent.get("owner_type"))
    if direct_owner_type != "EXIT_PLAN":
      continue
    direct_owner_id_value = intent.get("owner_id")
    direct_owner_id = _text(direct_owner_id_value)
    if not intent_id:
      raise _exit_plan_intent_error(
        intent.get("id"),
        "exit plan direct intent identity missing",
      )
    if not direct_owner_id:
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan direct intent owner id missing",
      )
    if isinstance(direct_owner_id_value, str) and (
      direct_owner_id_value != direct_owner_id
    ):
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan direct intent owner id invalid",
      )
    plan = plans.get(direct_owner_id)
    if plan is None:
      raise _exit_plan_intent_error(
        intent_id,
        "exit plan direct intent plan proof missing",
      )
    try:
      plan_environment = _exit_plan_environment(
        plan,
        strategy_runs=strategy_runs,
        batches=batches,
      )
    except RuntimeError as exc:
      raise _exit_plan_intent_error(intent_id, str(exc)) from exc
    _validate_exit_plan_intent(
      intent,
      plan,
      plan_environment,
      pending_link=False,
      require_strategy_run_absent=True,
    )
    owners[intent_id] = ("EXIT_PLAN", direct_owner_id, plan_environment)
  return owners


def _preflight_batches(
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
  strategy_runs: Mapping[str, Mapping[str, Any]],
  updates: dict[str, list[tuple[str, dict[str, Any]]]],
) -> None:
  for row in rows_by_table["t_trade_batches"]:
    owner = _batch_source_owner(row, strategy_runs)
    update = {
      "source_execution_owner_type": owner[0],
      "source_execution_owner_id": owner[1],
      "source_execution_environment": owner[2],
      "environment": owner[2],
      "strategy_run_id": owner[1] if owner[0] == "STRATEGY_RUN" else None,
    }
    updates["t_trade_batches"].append((str(row["batch_id"]), update))


def _preflight_exit_plans(
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
  strategy_runs: Mapping[str, Mapping[str, Any]],
  updates: dict[str, list[tuple[str, dict[str, Any]]]],
) -> None:
  batches = {
    _text(row.get("batch_id")): row
    for row in rows_by_table["t_trade_batches"]
    if _text(row.get("batch_id"))
  }
  for row in rows_by_table["auto_exit_plans"]:
    tombstone_owner = _terminal_strategy_run_tombstone_owner(
      row,
      rows_by_table,
      strategy_runs,
    )
    if tombstone_owner is not None:
      owner = tombstone_owner
    else:
      try:
        owner = _source_owner_from_plan(
          row,
          strategy_runs=strategy_runs,
          batches=batches,
        )
      except RuntimeError as exc:
        raise _plan_owner_error(row, str(exc)) from exc
    updates["auto_exit_plans"].append(
      (
        str(row["plan_id"]),
        {
          "source_execution_owner_type": owner[0],
          "source_execution_owner_id": owner[1],
          "source_execution_environment": owner[2],
          "environment": owner[2],
          "strategy_run_id": owner[1] if owner[0] == "STRATEGY_RUN" else None,
        },
      )
    )


def _challenge_owner(
  row: Mapping[str, Any],
  strategy_runs: Mapping[str, Mapping[str, Any]],
  *,
  auto_exit_plans: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str, str, str]:
  action = _text(row.get("action")).upper()
  if action not in EXECUTION_APPROVAL_ACTIONS:
    if any(
      row.get(key) not in (None, "")
      for key in ("owner_type", "owner_id", "environment")
    ):
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: control challenge has owner"
      )
    return "", "", ""

  expected_owner_type = {
    "MANUAL_ORDER": "MANUAL_COMMAND",
    "STRATEGY_TRADE_INTENT_APPROVAL": "STRATEGY_RUN",
    "T_TRADE_ENTRY_APPROVAL": "STRATEGY_RUN",
    "EXIT_PLAN_SELL_APPROVAL": "EXIT_PLAN",
    "EXIT_PLAN_AUTHORIZATION": "EXIT_PLAN",
    "ENTRY_PLAN_AUTHORIZATION": "ENTRY_PLAN",
    "LIQUIDATION_GROUP": "MANUAL_COMMAND",
  }[action]
  payload = _mapping(row.get("payload"))

  direct_owner_values = (
    row.get("owner_type"),
    row.get("owner_id"),
    row.get("environment"),
  )
  if any(value not in (None, "") for value in direct_owner_values):
    if not all(value not in (None, "") for value in direct_owner_values):
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: execution challenge owner projection incomplete"
      )
    owner_type = _text(row.get("owner_type"))
    owner_id = _text(row.get("owner_id"))
    if owner_type != expected_owner_type:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge action owner type mismatch"
      )
  elif action == "MANUAL_ORDER":
    # ManualOrderChallengeService binds the command to the durable challenge
    # itself.  Payload command/idempotency fields are not ownership proof.
    owner_type = "MANUAL_COMMAND"
    owner_id = _text(row.get("id"))
  elif action in {
    "STRATEGY_TRADE_INTENT_APPROVAL",
    "T_TRADE_ENTRY_APPROVAL",
    "EXIT_PLAN_SELL_APPROVAL",
  }:
    # TradeApprovalChallengeService's 0045 signed payload used the common
    # ``business_owner_id`` field for strategy, T-trade, and managed-exit
    # approvals.  It is a precise top-level path, not a generic metadata
    # owner fallback; the durable relation below still has to corroborate it.
    owner_type = expected_owner_type
    owner_id = _text(_payload_path(payload, "business_owner_id"))
  elif action == "EXIT_PLAN_AUTHORIZATION":
    owner_type = "EXIT_PLAN"
    owner_id = _text(_payload_path(payload, "request", "plan_id"))
  elif action == "ENTRY_PLAN_AUTHORIZATION":
    owner_type = "ENTRY_PLAN"
    owner_id = _text(_payload_path(payload, "scope", "plan_id"))
  elif action == "LIQUIDATION_GROUP":
    owner_type = "MANUAL_COMMAND"
    owner_id = _text(_payload_path(payload, "group_id"))
  else:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: unsupported execution challenge"
    )
  payload_owner_id = _text(
    {
      "STRATEGY_TRADE_INTENT_APPROVAL": _payload_path(
        payload, "business_owner_id"
      ),
      "T_TRADE_ENTRY_APPROVAL": _payload_path(payload, "business_owner_id"),
      "EXIT_PLAN_SELL_APPROVAL": _payload_path(payload, "business_owner_id"),
      "EXIT_PLAN_AUTHORIZATION": _payload_path(
        payload, "request", "plan_id"
      ),
      "ENTRY_PLAN_AUTHORIZATION": _payload_path(
        payload, "scope", "plan_id"
      ),
      "LIQUIDATION_GROUP": _payload_path(payload, "group_id"),
    }.get(action)
  )
  if payload_owner_id and payload_owner_id != owner_id:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge payload owner mismatch"
    )
  if not owner_id:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: execution challenge owner missing"
    )

  if owner_type != expected_owner_type:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge action owner type mismatch"
    )
  environment = _row_environment(row, payload, include_nested=False)
  plan = None
  if owner_type == "EXIT_PLAN":
    plan = (auto_exit_plans or {}).get(owner_id)
    if plan is None and auto_exit_plans is not None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge exit plan proof missing"
      )
    if plan is not None:
      plan_environment = _plan_environment_proof(plan)
      if plan_environment is None:
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge exit plan environment missing"
        )
      if environment is None:
        environment = plan_environment
      elif environment != plan_environment:
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge exit plan environment conflict"
        )
      challenge_account = _text(row.get("account_id"))
      if challenge_account and challenge_account != _text(plan.get("account_id")):
        raise RuntimeError(
          "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge exit plan account conflict"
        )
  if environment is None:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: execution challenge environment missing"
    )
  if owner_type == "STRATEGY_RUN":
    run = strategy_runs.get(owner_id)
    if run is None or _environment(run.get("mode")) != environment:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge strategy proof missing"
      )
    row_strategy_run_id = _text(row.get("strategy_run_id"))
    if row_strategy_run_id and row_strategy_run_id != owner_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: challenge strategy owner mismatch"
      )
  if action == "MANUAL_ORDER" and owner_id != _text(row.get("id")):
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: manual challenge owner mismatch"
    )
  if action == "LIQUIDATION_GROUP":
    group_id = _text(_payload_path(payload, "group_id"))
    if not group_id or group_id != owner_id:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: liquidation group owner mismatch"
      )
  return owner_type, owner_id, environment


def _pending_owner_error(row: Mapping[str, Any], detail: str) -> RuntimeError:
  """Attach the pending order identity to challenge-proof failures."""

  client_order_id = _text(row.get("client_order_id")) or "<missing>"
  return RuntimeError(
    "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: "
    f"{detail} (table=pending_trade_orders, client_order_id={client_order_id})"
  )


def _correlation_owner_error(row: Mapping[str, Any], detail: str) -> RuntimeError:
  """Attach the correlation identity to pending-owner failures."""

  client_order_id = _text(row.get("client_order_id")) or "<missing>"
  return RuntimeError(
    "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: "
    f"{detail} (table=order_correlations, client_order_id={client_order_id})"
  )


def _canonical_decimal(value: Any) -> str | None:
  if value in (None, ""):
    return None
  try:
    number = Decimal(str(value))
  except (InvalidOperation, TypeError, ValueError):
    return None
  if not number.is_finite():
    return None
  return format(number.normalize(), "f")


def _whole_number(value: Any) -> int | None:
  normalized = _canonical_decimal(value)
  if normalized is None:
    return None
  number = Decimal(normalized)
  if number != number.to_integral_value():
    return None
  return int(number)


def _pending_environment(row: Mapping[str, Any]) -> str | None:
  """Resolve only direct pending environment columns, with conflict checks."""

  values: list[str] = []
  for key in ("environment", "execution_mode"):
    value = row.get(key)
    if value in (None, ""):
      continue
    normalized = _environment(value)
    if normalized is None:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: pending environment invalid"
      )
    values.append(normalized)
  if len(set(values)) > 1:
    raise RuntimeError(
      "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: pending environment conflict"
    )
  return values[0] if values else None


def _resolve_intentless_pending_owner(
  row: Mapping[str, Any],
  *,
  strategy_runs: Mapping[str, Mapping[str, Any]],
  challenge_by_id: Mapping[str, tuple[Mapping[str, Any], ...]],
  outbox_by_message_id: Mapping[str, tuple[Mapping[str, Any], ...]],
  outbox_rows: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], tuple[str, str, str]]:
  """Prove a legacy manual pending order through its consumed challenge.

  ``request_metadata.challenge_id`` is only a legacy foreign-link.  Every
  ownership and execution field comes from the challenge row, its signed
  payload, and the uniquely matching outbox row; metadata owner/environment
  fields are never inspected.
  """

  metadata = _mapping(row.get("request_metadata"))
  challenge_id = _text(metadata.get("challenge_id"))
  if not challenge_id:
    raise _pending_owner_error(row, "manual pending challenge link missing")
  challenge_rows = challenge_by_id.get(challenge_id, ())
  if not challenge_rows:
    raise _pending_owner_error(row, "manual pending challenge proof missing")
  if len(challenge_rows) != 1:
    raise _pending_owner_error(row, "manual pending challenge proof is not unique")
  challenge = challenge_rows[0]

  if _text(challenge.get("action")).upper() != "MANUAL_ORDER":
    raise _pending_owner_error(row, "manual pending challenge action mismatch")
  if challenge.get("consumed_at") in (None, ""):
    raise _pending_owner_error(row, "manual pending challenge is not consumed")

  pending_user_id = _text(row.get("user_id"))
  challenge_user_id = _text(challenge.get("user_id"))
  if not pending_user_id or not challenge_user_id or pending_user_id != challenge_user_id:
    raise _pending_owner_error(row, "manual pending challenge user conflict")
  pending_account_id = _text(row.get("account_id"))
  challenge_account_id = _text(challenge.get("account_id"))
  if (
    not pending_account_id
    or not challenge_account_id
    or pending_account_id != challenge_account_id
  ):
    raise _pending_owner_error(row, "manual pending challenge account conflict")

  payload = _mapping(challenge.get("payload"))
  if not payload:
    raise _pending_owner_error(row, "manual pending challenge payload missing")
  if _text(payload.get("action")).upper() != "MANUAL_ORDER":
    raise _pending_owner_error(row, "manual pending signed action mismatch")

  try:
    challenge_owner = _challenge_owner(challenge, strategy_runs)
  except RuntimeError as exc:
    raise _pending_owner_error(row, f"manual pending challenge owner invalid: {exc}") from exc
  if challenge_owner[0] != "MANUAL_COMMAND" or challenge_owner[1] != challenge_id:
    raise _pending_owner_error(row, "manual pending challenge owner mismatch")

  payload_environment = _environment(payload.get("execution_mode"))
  if payload_environment is None:
    raise _pending_owner_error(
      row,
      "manual pending signed execution environment missing or invalid",
    )
  if payload_environment != challenge_owner[2]:
    raise _pending_owner_error(row, "manual pending signed environment conflict")
  try:
    pending_environment = _pending_environment(row)
  except RuntimeError as exc:
    raise _pending_owner_error(row, str(exc)) from exc
  if pending_environment is None:
    raise _pending_owner_error(row, "manual pending environment missing")
  if pending_environment != payload_environment:
    raise _pending_owner_error(row, "manual pending environment conflict")

  payload_account_id = _text(payload.get("account_id"))
  if not payload_account_id or payload_account_id != pending_account_id:
    raise _pending_owner_error(row, "manual pending signed account conflict")
  pending_instrument = _text(row.get("instrument_code")).upper()
  payload_instrument = _text(payload.get("instrument_code")).upper()
  if not pending_instrument or not payload_instrument or pending_instrument != payload_instrument:
    raise _pending_owner_error(row, "manual pending signed instrument conflict")
  pending_side = _text(row.get("side")).upper()
  payload_side = _text(payload.get("side")).upper()
  if not pending_side or not payload_side or pending_side != payload_side:
    raise _pending_owner_error(row, "manual pending signed side conflict")

  pending_volume = _whole_number(row.get("volume"))
  payload_volume = _whole_number(payload.get("final_volume"))
  if pending_volume is None or payload_volume is None or pending_volume != payload_volume:
    raise _pending_owner_error(row, "manual pending signed final volume conflict")

  # 0045 persisted the broker-facing FIX_PRICE order type, while the signed
  # manual-order request called the same mapping LIMIT.  Keep this explicit;
  # comparing the two strings directly would reject the real legacy rows.
  if (
    _text(row.get("order_type")).upper(),
    _text(payload.get("price_type")).upper(),
  ) != ("FIX_PRICE", "LIMIT"):
    raise _pending_owner_error(row, "manual pending signed order type conflict")
  pending_limit_price = _canonical_decimal(row.get("limit_price"))
  payload_limit_price = _canonical_decimal(payload.get("limit_price"))
  if (
    pending_limit_price is None
    or payload_limit_price is None
    or pending_limit_price != payload_limit_price
  ):
    raise _pending_owner_error(row, "manual pending signed limit price conflict")

  pending_risk_id = _text(row.get("risk_decision_id"))
  payload_risk_id = _text(payload.get("risk_decision_id"))
  if not pending_risk_id or not payload_risk_id or pending_risk_id != payload_risk_id:
    raise _pending_owner_error(row, "manual pending signed risk decision conflict")
  pending_idempotency_key = _text(row.get("idempotency_key"))
  if pending_idempotency_key and pending_idempotency_key != _text(
    payload.get("idempotency_key")
  ):
    raise _pending_owner_error(row, "manual pending signed idempotency conflict")

  direct_owner_type = _text(row.get("owner_type"))
  direct_owner_id = _text(row.get("owner_id"))
  if direct_owner_type or direct_owner_id:
    if (
      direct_owner_type != "MANUAL_COMMAND"
      or direct_owner_id != challenge_id
    ):
      raise _pending_owner_error(row, "manual pending owner projection conflict")
  if _text(row.get("strategy_run_id")):
    raise _pending_owner_error(row, "manual pending strategy_run_id must be absent")

  result_reference = _mapping(challenge.get("result_reference"))
  if _text(result_reference.get("status")).upper() != "QUEUED":
    raise _pending_owner_error(row, "manual pending challenge result status mismatch")
  client_order_id = _text(row.get("client_order_id"))
  result_client_order_id = _text(result_reference.get("client_order_id"))
  if (
    not result_client_order_id
    or not client_order_id
    or result_client_order_id != client_order_id
  ):
    raise _pending_owner_error(
      row,
      "manual pending challenge result client_order_id conflict",
    )
  message_id = _text(result_reference.get("message_id"))
  if not message_id:
    raise _pending_owner_error(row, "manual pending challenge result message_id missing")
  message_rows = outbox_by_message_id.get(message_id, ())
  if len(message_rows) != 1:
    raise _pending_owner_error(row, "manual pending challenge result outbox proof missing")
  outbox = message_rows[0]
  if _text(outbox.get("client_order_id")) != client_order_id:
    raise _pending_owner_error(row, "manual pending challenge result outbox client conflict")
  client_outboxes = tuple(
    item
    for item in outbox_rows
    if _text(item.get("client_order_id")) == client_order_id
  )
  if len(client_outboxes) != 1 or _text(client_outboxes[0].get("message_id")) != message_id:
    raise _pending_owner_error(row, "manual pending challenge result outbox is not unique")

  owner = ("MANUAL_COMMAND", challenge_id, payload_environment)
  update: dict[str, Any] = {
    "owner_type": owner[0],
    "owner_id": owner[1],
    "environment": owner[2],
  }
  if "strategy_run_id" in row:
    update["strategy_run_id"] = None
  if _text(row.get("strategy_order_id")):
    raise _pending_owner_error(
      row,
      "manual pending strategy_order_id must be absent",
    )
  return update, owner


def _preflight_challenges(
  rows_by_table: Mapping[str, list[Mapping[str, Any]]],
  strategy_runs: Mapping[str, Mapping[str, Any]],
  updates: dict[str, list[tuple[str, dict[str, Any]]]],
) -> None:
  auto_exit_plans = {
    _text(row.get("plan_id")): row
    for row in rows_by_table["auto_exit_plans"]
    if _text(row.get("plan_id"))
  }
  for row in rows_by_table["trade_confirmation_challenges"]:
    owner = _challenge_owner(
      row,
      strategy_runs,
      auto_exit_plans=auto_exit_plans,
    )
    update = (
      {"owner_type": owner[0], "owner_id": owner[1], "environment": owner[2]}
      if owner[0]
      else {"owner_type": None, "owner_id": None, "environment": None}
    )
    updates["trade_confirmation_challenges"].append((str(row["id"]), update))


def _rename_legacy_columns(bind) -> None:
  tables = set(inspect(bind).get_table_names())
  for table_name in (
    "pending_trade_orders",
    "order_correlations",
    "auto_exit_plans",
    "t_trade_batches",
  ):
    if table_name not in tables:
      continue
    columns = _columns(bind, table_name)
    if "execution_mode" in columns and "environment" in columns:
      raise RuntimeError(
        "EXECUTION_OWNER_MIGRATION_AMBIGUOUS: duplicate environment projection "
        f"on {table_name}"
      )
    if "execution_mode" in columns:
      op.alter_column(
        table_name,
        "execution_mode",
        new_column_name="environment",
      )


def _add_columns(bind) -> None:
  specs: dict[str, tuple[tuple[str, sa.types.TypeEngine], ...]] = {
    "trade_intents": (
      ("environment", sa.String(length=16)),
      ("idempotency_key", sa.String(length=128)),
    ),
    "pending_trade_orders": (
      ("owner_type", sa.String(length=32)),
      ("owner_id", sa.String(length=128)),
      ("environment", sa.String(length=16)),
    ),
    "order_correlations": (
      ("owner_type", sa.String(length=32)),
      ("owner_id", sa.String(length=128)),
      ("environment", sa.String(length=16)),
    ),
    "trade_command_outbox": (
      ("owner_type", sa.String(length=32)),
      ("owner_id", sa.String(length=128)),
      ("environment", sa.String(length=16)),
    ),
    "strategy_runtime_events": (
      ("owner_type", sa.String(length=32)),
      ("owner_id", sa.String(length=128)),
      ("environment", sa.String(length=16)),
    ),
    "auto_exit_plans": (
      ("source_execution_owner_type", sa.String(length=32)),
      ("source_execution_owner_id", sa.String(length=128)),
      ("source_execution_environment", sa.String(length=16)),
    ),
    "t_trade_batches": (
      ("source_execution_owner_type", sa.String(length=32)),
      ("source_execution_owner_id", sa.String(length=128)),
      ("source_execution_environment", sa.String(length=16)),
    ),
    "trade_confirmation_challenges": (
      ("owner_type", sa.String(length=32)),
      ("owner_id", sa.String(length=128)),
      ("environment", sa.String(length=16)),
    ),
  }
  for table_name, columns in specs.items():
    existing = _columns(bind, table_name)
    for column_name, column_type in columns:
      if column_name not in existing:
        # Fill only after the complete preflight.  No server/Python default is
        # installed, so a future write cannot silently invent ownership.
        op.add_column(table_name, sa.Column(column_name, column_type, nullable=True))


def _apply_updates(
  bind,
  updates: Mapping[str, list[tuple[str, dict[str, Any]]]],
) -> None:
  for table_name, rows in updates.items():
    if not rows:
      continue
    columns = _columns(bind, table_name)
    primary_key = _primary_key(table_name)
    for primary_value, values in rows:
      values = {key: value for key, value in values.items() if key in columns}
      if not values:
        continue
      assignments = ", ".join(f'"{key}" = :{key}' for key in values)
      statement = sa.text(
        f'UPDATE "{table_name}" SET {assignments} '
        f'WHERE "{primary_key}" = :_owner_migration_pk'
      )
      bind.execute(
        statement,
        {**values, "_owner_migration_pk": primary_value},
      )


_CHECKS: dict[str, tuple[tuple[str, str], ...]] = {
  "trade_intents": (
    (
      "ck_trade_intent_owner_type",
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_trade_intent_owner_id",
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
    ),
    (
      "ck_trade_intent_environment",
      "environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_trade_intent_strategy_run_owner",
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
    ),
  ),
  "pending_trade_orders": (
    (
      "ck_pending_trade_order_owner_type",
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_pending_trade_order_owner_id",
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
    ),
    (
      "ck_pending_trade_order_environment",
      "environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_pending_trade_order_strategy_run_owner",
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
    ),
    (
      "ck_pending_trade_order_strategy_identity",
      "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NOT NULL) OR "
      "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NULL) OR "
      "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
      "AND strategy_order_id IS NULL)",
    ),
  ),
  "order_correlations": (
    (
      "ck_order_correlation_owner_type",
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_order_correlation_owner_id",
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
    ),
    (
      "ck_order_correlation_environment",
      "environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_order_correlation_strategy_run_owner",
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
    ),
    (
      "ck_order_correlation_strategy_identity",
      "(owner_type = 'STRATEGY_RUN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NOT NULL) OR "
      "(owner_type = 'EXIT_PLAN' AND intent_id IS NOT NULL "
      "AND strategy_order_id IS NULL) OR "
      "(owner_type = 'MANUAL_COMMAND' AND intent_id IS NULL "
      "AND strategy_order_id IS NULL)",
    ),
  ),
  "trade_command_outbox": (
    (
      "ck_trade_command_owner_type",
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_trade_command_owner_id",
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
    ),
    (
      "ck_trade_command_environment",
      "environment IN ('PAPER','LIVE','BACKTEST')",
    ),
  ),
  "strategy_runtime_events": (
    (
      "ck_strategy_runtime_event_owner_type",
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_strategy_runtime_event_owner_id",
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
    ),
    (
      "ck_strategy_runtime_event_environment",
      "environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_strategy_runtime_event_strategy_run_owner",
      "strategy_run_id IS NULL OR (owner_type = 'STRATEGY_RUN' "
      "AND owner_id = strategy_run_id)",
    ),
  ),
  "auto_exit_plans": (
    (
      "ck_auto_exit_plan_source_owner_type",
      "source_execution_owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION',"
      "'ENTRY_PLAN','BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_auto_exit_plan_source_owner_id",
      "length(source_execution_owner_id) > 0 AND "
      "source_execution_owner_id = trim(source_execution_owner_id)",
    ),
    (
      "ck_auto_exit_plan_source_environment",
      "source_execution_environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_auto_exit_plan_source_environment_match",
      "source_execution_environment = environment",
    ),
    (
      "ck_auto_exit_plan_strategy_run_owner",
      "strategy_run_id IS NULL OR (source_execution_owner_type = 'STRATEGY_RUN' "
      "AND source_execution_owner_id = strategy_run_id)",
    ),
  ),
  "t_trade_batches": (
    (
      "ck_t_trade_batch_source_owner_type",
      "source_execution_owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION',"
      "'ENTRY_PLAN','BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND')",
    ),
    (
      "ck_t_trade_batch_source_owner_id",
      "length(source_execution_owner_id) > 0 AND "
      "source_execution_owner_id = trim(source_execution_owner_id)",
    ),
    (
      "ck_t_trade_batch_source_environment",
      "source_execution_environment IN ('PAPER','LIVE','BACKTEST')",
    ),
    (
      "ck_t_trade_batch_source_environment_match",
      "source_execution_environment = environment",
    ),
    (
      "ck_t_trade_batch_strategy_run_owner",
      "strategy_run_id IS NULL OR (source_execution_owner_type = 'STRATEGY_RUN' "
      "AND source_execution_owner_id = strategy_run_id)",
    ),
  ),
  "trade_confirmation_challenges": (
    (
      "ck_trade_confirmation_owner_shape",
      "(owner_type IS NULL AND owner_id IS NULL AND environment IS NULL) OR "
      "(owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','EXIT_PLAN','MANUAL_COMMAND') AND "
      "length(owner_id) > 0 AND owner_id = trim(owner_id) AND "
      "environment IN ('PAPER','LIVE','BACKTEST'))",
    ),
    (
      "ck_trade_confirmation_execution_owner_required",
      "(action IN ('MANUAL_ORDER','STRATEGY_TRADE_INTENT_APPROVAL',"
      "'T_TRADE_ENTRY_APPROVAL','EXIT_PLAN_SELL_APPROVAL',"
      "'ENTRY_PLAN_AUTHORIZATION','EXIT_PLAN_AUTHORIZATION','LIQUIDATION_GROUP') "
      "AND owner_type IS NOT NULL AND owner_id IS NOT NULL "
      "AND environment IS NOT NULL) OR (action NOT IN "
      "('MANUAL_ORDER','STRATEGY_TRADE_INTENT_APPROVAL',"
      "'T_TRADE_ENTRY_APPROVAL','EXIT_PLAN_SELL_APPROVAL',"
      "'ENTRY_PLAN_AUTHORIZATION','EXIT_PLAN_AUTHORIZATION','LIQUIDATION_GROUP') "
      "AND owner_type IS NULL AND owner_id IS NULL AND environment IS NULL)",
    ),
  ),
}


def _check_names(bind, table_name: str) -> set[str]:
  return {
    str(item["name"])
    for item in inspect(bind).get_check_constraints(table_name)
    if item.get("name")
  }


def _unique_names(bind, table_name: str) -> set[str]:
  names = {
    str(item["name"])
    for item in inspect(bind).get_unique_constraints(table_name)
    if item.get("name")
  }
  names.update(
    str(item["name"])
    for item in inspect(bind).get_indexes(table_name)
    if item.get("name") and item.get("unique")
  )
  return names


def _ensure_order_correlation_client_constraint(bind) -> None:
  """Canonicalize the historical strategy-specific client uniqueness name."""

  table_name = "order_correlations"
  inspector = inspect(bind)
  unique_constraints = {
    str(item["name"])
    for item in inspector.get_unique_constraints(table_name)
    if item.get("name")
  }
  unique_indexes = {
    str(item["name"])
    for item in inspector.get_indexes(table_name)
    if (
      item.get("name")
      and item.get("unique")
      and not item.get("duplicates_constraint")
    )
  }
  old_constraint = "uq_strategy_order_client"
  canonical_constraint = "uq_order_correlation_client"
  dialect = str(bind.dialect.name).lower()

  if old_constraint in unique_constraints:
    with op.batch_alter_table(
      table_name,
      recreate="always" if dialect.startswith("sqlite") else "auto",
    ) as batch:
      batch.drop_constraint(old_constraint, type_="unique")
      if (
        canonical_constraint not in unique_constraints
        and canonical_constraint not in unique_indexes
      ):
        batch.create_unique_constraint(
          canonical_constraint,
          ["client_order_id"],
        )
    unique_constraints.discard(old_constraint)
    unique_constraints.add(canonical_constraint)

  if old_constraint in unique_indexes:
    op.drop_index(old_constraint, table_name=table_name)
    unique_indexes.discard(old_constraint)

  if (
    canonical_constraint not in unique_constraints
    and canonical_constraint not in unique_indexes
  ):
    with op.batch_alter_table(
      table_name,
      recreate="always" if dialect.startswith("sqlite") else "auto",
    ) as batch:
      batch.create_unique_constraint(
        canonical_constraint,
        ["client_order_id"],
      )


def _alter_and_constrain(bind) -> None:
  dialect = str(bind.dialect.name).lower()
  for table_name, check_specs in _CHECKS.items():
    existing_columns = _columns(bind, table_name)
    nullable_columns = {
      str(item["name"]): bool(item.get("nullable", True))
      for item in inspect(bind).get_columns(table_name)
    }
    not_null = {
      "owner_type",
      "owner_id",
      "environment",
    }
    if table_name == "trade_intents":
      not_null.add("idempotency_key")
    if table_name in {"auto_exit_plans", "t_trade_batches"}:
      not_null.update(
        {
          "source_execution_owner_type",
          "source_execution_owner_id",
          "source_execution_environment",
        }
      )
    if table_name == "trade_confirmation_challenges":
      not_null = set()
    strategy_run_nullable = table_name in {
      "trade_intents",
      "pending_trade_orders",
      "order_correlations",
      "strategy_runtime_events",
      "auto_exit_plans",
      "t_trade_batches",
    } and "strategy_run_id" in existing_columns
    current_checks = _check_names(bind, table_name)
    with op.batch_alter_table(
      table_name,
      recreate="always" if dialect.startswith("sqlite") else "auto",
    ) as batch:
      for column_name in sorted(not_null):
        if column_name in existing_columns and nullable_columns.get(column_name, False):
          batch.alter_column(column_name, nullable=False)
      if strategy_run_nullable and not nullable_columns.get("strategy_run_id", True):
        batch.alter_column("strategy_run_id", nullable=True)
      if table_name == "order_correlations":
        for column_name in ("strategy_order_id", "intent_id"):
          if (
            column_name in existing_columns
            and not nullable_columns.get(column_name, True)
          ):
            batch.alter_column(column_name, nullable=True)
      for constraint_name, expression in check_specs:
        if constraint_name not in current_checks:
          batch.create_check_constraint(constraint_name, expression)

    # Client correlation uniqueness is a canonical public-fact contract; do
    # not leave the historical strategy-specific physical name behind.
    if table_name == "order_correlations":
      _ensure_order_correlation_client_constraint(bind)

    # The new intent key is the only additional uniqueness requirement on
    # trade_intents.
    if (
      table_name == "trade_intents"
      and "uq_trade_intent_owner_idempotency"
      not in _unique_names(bind, table_name)
    ):
      with op.batch_alter_table(
        table_name,
        recreate="always" if dialect.startswith("sqlite") else "auto",
      ) as batch:
        batch.create_unique_constraint(
          "uq_trade_intent_owner_idempotency",
          ["environment", "owner_type", "owner_id", "idempotency_key"],
        )


def _ensure_indexes(bind) -> None:
  index_specs = {
    "order_correlations": (
      "ix_order_correlation_owner_batch",
      ["owner_type", "owner_id", "batch_id"],
      "ix_strategy_order_run_batch",
    ),
    "strategy_runtime_events": (
      "ix_strategy_runtime_event_owner_created",
      ["owner_type", "owner_id", "created_at", "event_id"],
      "ix_strategy_runtime_event_run_created",
    ),
    "t_trade_batches": (
      "ix_t_trade_batch_account_environment",
      ["account_id", "environment"],
      "ix_t_trade_batch_account_mode",
    ),
  }
  for table_name, (new_name, columns, old_name) in index_specs.items():
    indexes = {
      str(item["name"])
      for item in inspect(bind).get_indexes(table_name)
      if item.get("name")
    }
    if old_name in indexes and old_name != new_name:
      op.drop_index(old_name, table_name=table_name)
      indexes.remove(old_name)
    if new_name not in indexes:
      op.create_index(new_name, table_name, columns)


def _ensure_identity_immutability_triggers(bind) -> None:
  """Install the database hard-stop for persisted execution identities."""

  if str(bind.dialect.name).lower() != "postgresql":
    return
  op.execute(
    """
    CREATE OR REPLACE FUNCTION quantx_reject_identity_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      field_index integer;
      field_name text;
      old_value text;
      new_value text;
    BEGIN
      FOR field_index IN 0 .. (TG_NARGS - 1) LOOP
        field_name := TG_ARGV[field_index];
        old_value := to_jsonb(OLD) ->> field_name;
        new_value := to_jsonb(NEW) ->> field_name;
        IF old_value IS DISTINCT FROM new_value THEN
          RAISE EXCEPTION
            'OWNER_ENVIRONMENT_IMMUTABLE: % cannot change on %',
            field_name, TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
        END IF;
      END LOOP;
      RETURN NEW;
    END;
    $$;
    """
  )
  trigger_specs = {
    "trade_intents": (
      "owner_type",
      "owner_id",
      "environment",
    ),
    "pending_trade_orders": (
      "owner_type",
      "owner_id",
      "environment",
    ),
    "order_correlations": (
      "owner_type",
      "owner_id",
      "environment",
    ),
    "trade_command_outbox": (
      "owner_type",
      "owner_id",
      "environment",
    ),
    "strategy_runtime_events": (
      "owner_type",
      "owner_id",
      "environment",
    ),
    "auto_exit_plans": (
      "source_execution_owner_type",
      "source_execution_owner_id",
      "source_execution_environment",
      "environment",
    ),
    "t_trade_batches": (
      "source_execution_owner_type",
      "source_execution_owner_id",
      "source_execution_environment",
      "environment",
    ),
    "trade_confirmation_challenges": (
      "owner_type",
      "owner_id",
      "environment",
    ),
  }
  for table_name, fields in trigger_specs.items():
    trigger_name = f"trg_{table_name}_identity_immutable"
    update_columns = fields
    columns_sql = ", ".join(f'"{field}"' for field in update_columns)
    arguments_sql = ", ".join(
      "'" + field.replace("'", "''") + "'" for field in fields
    )
    op.execute(
      f'DROP TRIGGER IF EXISTS "{trigger_name}" ON "{table_name}"'
    )
    op.execute(
      f'CREATE TRIGGER "{trigger_name}" BEFORE UPDATE OF {columns_sql} '
      f'ON "{table_name}" FOR EACH ROW EXECUTE FUNCTION '
      f"quantx_reject_identity_mutation({arguments_sql})"
    )


def _rename_tables(bind) -> None:
  tables = set(inspect(bind).get_table_names())
  for old_name, new_name in OLD_NEW_RENAMES:
    if old_name in tables:
      op.rename_table(old_name, new_name)
      tables.remove(old_name)
      tables.add(new_name)


def _table_comments(bind) -> None:
  if str(bind.dialect.name).lower().startswith("sqlite"):
    return
  metadata = sa.MetaData()
  comments = {
    "trade_intents": "公共交易意图及执行状态",
    "order_correlations": "公共订单与券商回报关联关系",
  }
  for table_name, comment in comments.items():
    table = sa.Table(table_name, metadata, comment=comment)
    bind.execute(sa.schema.SetTableComment(table))


def _set_agent_report_protocol_default(bind) -> None:
  """Make new inbox rows current without rewriting historical reports."""

  if "agent_report_inbox" not in set(inspect(bind).get_table_names()):
    return
  columns = {
    str(column["name"])
    for column in inspect(bind).get_columns("agent_report_inbox")
  }
  if "protocol_version" in columns:
    dialect = str(bind.dialect.name).lower()
    with op.batch_alter_table(
      "agent_report_inbox",
      recreate="always" if dialect.startswith("sqlite") else "auto",
    ) as batch:
      batch.alter_column(
        "protocol_version",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=sa.text(f"'{PROTOCOL_VERSION}'"),
      )


def upgrade() -> None:
  bind = op.get_bind()
  # This must remain the first operation.  In particular, do not move table
  # renames or column additions above it: an ambiguous row must produce zero
  # DDL and leave the operator a recoverable legacy schema.
  updates = _preflight(bind)
  _rename_tables(bind)
  _rename_legacy_columns(bind)
  _add_columns(bind)
  _apply_updates(bind, updates)
  _alter_and_constrain(bind)
  _ensure_indexes(bind)
  _ensure_identity_immutability_triggers(bind)
  _set_agent_report_protocol_default(bind)
  _table_comments(bind)


def downgrade() -> None:
  raise RuntimeError("20260903_0046 execution-owner migration downgrades are forbidden")
