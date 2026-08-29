"""Add authoritative lifecycle and cost snapshots to T-trade batches."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260829_0036"
down_revision = "20260825_0035"
branch_labels = None
depends_on = None

_TABLE = "t_trade_batches"
_ADDITIONS = {
  "execution_mode": sa.String(length=16),
  "metrics_origin": sa.String(length=24),
  "entry_filled_at": sa.DateTime(),
  "last_exit_filled_at": sa.DateTime(),
  "closed_at": sa.DateTime(),
  "commission_rate": sa.Float(),
  "minimum_commission": sa.Float(),
  "stamp_tax_rate": sa.Float(),
  "transfer_fee_rate": sa.Float(),
}
_INDEXES = {
  "ix_t_trade_batch_account_mode": ("account_id", "execution_mode"),
  "ix_t_trade_batch_account_closed": ("account_id", "closed_at", "batch_id"),
}


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


def _nonnegative(value: Any) -> float | None:
  try:
    normalized = float(value)
  except (TypeError, ValueError):
    return None
  return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _costs(metadata: Any) -> dict[str, float] | None:
  raw = _mapping(metadata)
  template = _mapping(raw.get("exit_plan_template"))
  nested = _mapping(template.get("costs"))
  raw = {**nested, **raw}
  values = {
    "commission_rate": _nonnegative(raw.get("commission_rate")),
    "minimum_commission": _nonnegative(
      raw.get("minimum_commission", raw.get("min_commission"))
    ),
    "stamp_tax_rate": _nonnegative(raw.get("stamp_tax_rate")),
    "transfer_fee_rate": _nonnegative(raw.get("transfer_fee_rate")),
  }
  if any(value is None for value in values.values()):
    return None
  return {key: float(value) for key, value in values.items() if value is not None}


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


def _report_time(report: Mapping[str, Any], *, trade: bool) -> datetime | None:
  keys = (
    ("traded_time", "trade_time")
    if trade
    else ("order_time", "reported_at", "updated_at")
  )
  for key in keys:
    parsed = _event_time(report.get(key))
    if parsed is not None:
      return parsed
  return None


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


def _backfill(bind) -> None:
  tables = set(inspect(bind).get_table_names())
  if "strategy_order_correlations" not in tables:
    return

  batches = list(
    bind.execute(
      sa.text(
        "SELECT batch_id, strategy_run_id, status, entry_filled_volume, "
        "exit_filled_volume "
        "FROM t_trade_batches"
      )
    ).mappings()
  )
  has_events = "strategy_runtime_events" in tables
  for batch in batches:
    batch_id = str(batch["batch_id"])
    correlations = list(
      bind.execute(
        sa.text(
          "SELECT client_order_id, execution_mode, t_trade_role, request_metadata "
          "FROM strategy_order_correlations WHERE batch_id = :batch_id "
          "ORDER BY CASE WHEN UPPER(COALESCE(t_trade_role, '')) = 'ENTRY' "
          "THEN 0 ELSE 1 END, created_at ASC"
        ),
        {"batch_id": batch_id},
      ).mappings()
    )
    if not correlations and "pending_trade_orders" in tables:
      correlations = list(
        bind.execute(
          sa.text(
            "SELECT client_order_id, execution_mode, t_trade_role, "
            "request_metadata, created_at FROM pending_trade_orders "
            "WHERE batch_id = :batch_id ORDER BY created_at ASC"
          ),
          {"batch_id": batch_id},
        ).mappings()
      )
    if not correlations:
      continue
    entry = next(
      (
        row
        for row in correlations
        if str(row.get("t_trade_role") or "").upper() == "ENTRY"
      ),
      correlations[0],
    )
    mode = str(entry.get("execution_mode") or "").strip().lower()
    update_values: dict[str, Any] = {}
    if mode in {"paper", "live"}:
      update_values["execution_mode"] = mode
    else:
      mode = ""
    snapshot = _costs(entry.get("request_metadata"))
    if "strategy_runs" in tables and (snapshot is None or not mode):
      run = bind.execute(
        sa.text(
          "SELECT mode, parameters FROM strategy_runs WHERE id = :run_id"
        ),
        {"run_id": str(batch.get("strategy_run_id") or "")},
      ).mappings().first()
      if run is not None:
        run_mode = str(run.get("mode") or "").strip().lower()
        if not mode and run_mode in {"paper", "live"}:
          mode = run_mode
          update_values["execution_mode"] = mode
        if snapshot is None:
          snapshot = _costs(run.get("parameters"))
    if snapshot is not None:
      update_values.update(snapshot)

    if has_events:
      client_roles = {
        str(row["client_order_id"]): str(row.get("t_trade_role") or "").upper()
        for row in correlations
      }
      events = []
      for client_order_id in client_roles:
        events.extend(
          bind.execute(
            sa.text(
              "SELECT client_order_id, event_type, payload, created_at "
              "FROM strategy_runtime_events WHERE client_order_id = :client_order_id "
              "ORDER BY created_at ASC, event_id ASC"
            ),
            {"client_order_id": client_order_id},
          ).mappings()
        )
      events.sort(
        key=lambda event: (
          event.get("created_at") or datetime.min,
          str(event.get("client_order_id") or ""),
        )
      )
      entry_times: list[datetime] = []
      exit_times: list[datetime] = []
      rejected_times: list[datetime] = []
      for event in events:
        payload = _mapping(event.get("payload"))
        report = _mapping(payload.get("report"))
        metadata = _mapping(payload.get("metadata"))
        role = str(
          metadata.get("t_trade_role")
          or client_roles.get(str(event.get("client_order_id")))
          or ""
        ).upper()
        event_type = str(event.get("event_type") or "").upper()
        if event_type == "TRADE":
          timestamp = _report_time(report, trade=True)
          if timestamp is not None and role == "ENTRY":
            entry_times.append(timestamp)
          elif timestamp is not None and role == "EXIT":
            exit_times.append(timestamp)
        elif event_type == "ORDER" and role == "ENTRY" and _terminal_entry_status(
          report
        ):
          timestamp = _report_time(report, trade=False)
          if timestamp is not None:
            rejected_times.append(timestamp)

      if entry_times:
        update_values["entry_filled_at"] = min(entry_times)
      if exit_times:
        update_values["last_exit_filled_at"] = max(exit_times)
      entry_volume = max(0, int(batch.get("entry_filled_volume") or 0))
      exit_volume = max(0, int(batch.get("exit_filled_volume") or 0))
      if entry_volume > 0 and exit_volume == entry_volume and exit_times:
        update_values["closed_at"] = max(exit_times)
      elif (
        entry_volume == 0
        and str(batch.get("status") or "").upper()
        in {"ENTRY_REJECTED", "ENTRY_EXPIRED"}
        and rejected_times
      ):
        update_values["closed_at"] = max(rejected_times)

    if update_values:
      assignments = ", ".join(f"{key} = :{key}" for key in update_values)
      bind.execute(
        sa.text(f"UPDATE t_trade_batches SET {assignments} WHERE batch_id = :batch_id"),
        {"batch_id": batch_id, **update_values},
      )


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if _TABLE not in set(inspector.get_table_names()):
    return
  columns = {str(column["name"]) for column in inspector.get_columns(_TABLE)}
  for name, type_ in _ADDITIONS.items():
    if name not in columns:
      op.add_column(_TABLE, sa.Column(name, type_, nullable=True))

  bind.execute(
    sa.text(
      "UPDATE t_trade_batches SET metrics_origin = 'LEGACY_BACKFILL' "
      "WHERE metrics_origin IS NULL"
    )
  )

  _backfill(bind)
  indexes = {
    str(index["name"])
    for index in inspect(bind).get_indexes(_TABLE)
    if index.get("name")
  }
  for name, columns_ in _INDEXES.items():
    if name not in indexes:
      op.create_index(name, _TABLE, list(columns_), unique=False)


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
  for name in _INDEXES:
    if name in indexes:
      op.drop_index(name, table_name=_TABLE)
  columns = {str(column["name"]) for column in inspector.get_columns(_TABLE)}
  for name in reversed(tuple(_ADDITIONS)):
    if name in columns:
      op.drop_column(_TABLE, name)
