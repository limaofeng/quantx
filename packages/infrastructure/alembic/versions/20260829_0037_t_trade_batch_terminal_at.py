"""Separate terminal lifecycle time from fill-based batch close time."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_0037"
down_revision = "20260829_0036"
branch_labels = None
depends_on = None

_TABLE = "t_trade_batches"
_TERMINAL_INDEX = "ix_t_trade_batch_account_terminal"


def _mapping(value: Any) -> dict[str, Any]:
  if isinstance(value, Mapping):
    return dict(value)
  if isinstance(value, str) and value:
    try:
      decoded = json.loads(value)
    except (TypeError, ValueError):
      return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}
  return {}


def _event_time(value: Any) -> datetime | None:
  if isinstance(value, datetime):
    parsed = value
  elif isinstance(value, (int, float)) and not isinstance(value, bool):
    numeric = float(value)
    if not math.isfinite(numeric):
      return None
    if numeric > 10_000_000_000:
      numeric /= 1000.0
    try:
      parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
      return None
  elif isinstance(value, str) and value.strip():
    text = value.strip()
    try:
      parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
      try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
      except ValueError:
        return None
  else:
    return None
  if parsed.tzinfo is not None:
    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
  if parsed > datetime.now(timezone.utc).replace(tzinfo=None):
    return None
  return parsed


def _terminal_entry_status(report: Mapping[str, Any]) -> bool:
  value = str(
    report.get("effective_order_status")
    or report.get("status")
    or report.get("order_status")
    or ""
  ).strip().upper()
  return value in {
    "REJECTED",
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "54",
    "56",
    "57",
  }


def _entry_client_order_ids(bind, batch_id: str, tables: set[str]) -> set[str]:
  client_order_ids: set[str] = set()
  if "strategy_order_correlations" in tables:
    client_order_ids.update(
      str(row["client_order_id"])
      for row in bind.execute(
        sa.text(
          "SELECT client_order_id FROM strategy_order_correlations "
          "WHERE batch_id = :batch_id "
          "AND UPPER(COALESCE(t_trade_role, '')) = 'ENTRY'"
        ),
        {"batch_id": batch_id},
      ).mappings()
      if row.get("client_order_id")
    )
  if "pending_trade_orders" in tables:
    client_order_ids.update(
      str(row["client_order_id"])
      for row in bind.execute(
        sa.text(
          "SELECT client_order_id FROM pending_trade_orders "
          "WHERE batch_id = :batch_id "
          "AND UPPER(COALESCE(t_trade_role, '')) = 'ENTRY'"
        ),
        {"batch_id": batch_id},
      ).mappings()
      if row.get("client_order_id")
    )
  return client_order_ids


def _terminal_order_time(bind, batch_id: str, tables: set[str]) -> datetime | None:
  if "strategy_runtime_events" not in tables:
    return None
  timestamps: list[datetime] = []
  for client_order_id in _entry_client_order_ids(bind, batch_id, tables):
    events = bind.execute(
      sa.text(
        "SELECT payload FROM strategy_runtime_events "
        "WHERE client_order_id = :client_order_id "
        "AND UPPER(event_type) = 'ORDER'"
      ),
      {"client_order_id": client_order_id},
    ).mappings()
    for event in events:
      report = _mapping(_mapping(event.get("payload")).get("report"))
      if not _terminal_entry_status(report):
        continue
      # Only broker ORDER lifecycle fields are authoritative here.  The prior
      # migration accepted ``updated_at`` as a fallback, so 0037 deliberately
      # reconstructs instead of copying that value into the new column.
      timestamp = _event_time(report.get("order_time")) or _event_time(
        report.get("reported_at")
      )
      if timestamp is not None:
        timestamps.append(timestamp)
  return max(timestamps) if timestamps else None


def _backfill(bind) -> None:
  tables = set(inspect(bind).get_table_names())
  bind.execute(
    sa.text(
      "UPDATE t_trade_batches SET terminal_at = closed_at "
      "WHERE terminal_at IS NULL AND UPPER(status) = 'CLOSED' "
      "AND COALESCE(entry_filled_volume, 0) > 0 "
      "AND COALESCE(entry_filled_volume, 0) = "
      "COALESCE(exit_filled_volume, 0) AND closed_at IS NOT NULL"
    )
  )

  rejected_batches = list(
    bind.execute(
      sa.text(
        "SELECT batch_id FROM t_trade_batches "
        "WHERE UPPER(status) IN ('ENTRY_REJECTED', 'ENTRY_EXPIRED') "
        "AND COALESCE(entry_filled_volume, 0) = 0 "
        "AND COALESCE(exit_filled_volume, 0) = 0"
      )
    ).mappings()
  )
  for row in rejected_batches:
    batch_id = str(row["batch_id"])
    terminal_at = _terminal_order_time(bind, batch_id, tables)
    if terminal_at is not None:
      bind.execute(
        sa.text(
          "UPDATE t_trade_batches SET terminal_at = :terminal_at "
          "WHERE batch_id = :batch_id"
        ),
        {"batch_id": batch_id, "terminal_at": terminal_at},
      )

  # A zero-fill rejected/expired entry was never closed by a TRADE.  Clear the
  # 0036 compatibility value even when no authoritative terminal report exists.
  bind.execute(
    sa.text(
      "UPDATE t_trade_batches SET closed_at = NULL "
      "WHERE UPPER(status) IN ('ENTRY_REJECTED', 'ENTRY_EXPIRED') "
      "AND COALESCE(entry_filled_volume, 0) = 0 "
      "AND COALESCE(exit_filled_volume, 0) = 0"
    )
  )


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if _TABLE not in set(inspector.get_table_names()):
    return
  columns = {str(column["name"]) for column in inspector.get_columns(_TABLE)}
  if "terminal_at" not in columns:
    op.add_column(_TABLE, sa.Column("terminal_at", sa.DateTime(), nullable=True))

  _backfill(bind)
  indexes = {
    str(index["name"])
    for index in inspect(bind).get_indexes(_TABLE)
    if index.get("name")
  }
  if _TERMINAL_INDEX not in indexes:
    op.create_index(
      _TERMINAL_INDEX,
      _TABLE,
      ["account_id", "terminal_at", "batch_id"],
      unique=False,
    )


def downgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if _TABLE not in set(inspector.get_table_names()):
    return
  indexes = {
    str(index["name"])
    for index in inspector.get_indexes(_TABLE)
    if index.get("name")
  }
  if _TERMINAL_INDEX in indexes:
    op.drop_index(_TERMINAL_INDEX, table_name=_TABLE)
  columns = {str(column["name"]) for column in inspector.get_columns(_TABLE)}
  if "terminal_at" in columns:
    op.drop_column(_TABLE, "terminal_at")
