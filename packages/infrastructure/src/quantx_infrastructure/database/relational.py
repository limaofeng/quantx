"""
关系型数据库管理
"""

from .relational_base import (
  Base,
  BaseModel,
  BaseRepository,
  BulkSaveResult,
  TimestampMixin,
  WhereBuilder,
)
from .relational_connection import engine, get_async_db


def _prepare_runtime_message_boxes(connection):
  """Rename the additive message boxes before ``create_all`` sees them.

  This preserves already uploaded chunks from the first migration draft and
  prevents SQLAlchemy from creating a second empty table under the final name.
  """
  from sqlalchemy import inspect, text

  def ensure_idempotency_column(
    table_name: str,
    column_name: str,
    index_name: str,
  ) -> None:
    inspector = inspect(connection)
    columns = {
      column["name"]: column for column in inspector.get_columns(table_name)
    }
    if column_name not in columns:
      connection.execute(
        text(
          f"ALTER TABLE {table_name} "
          f"ADD COLUMN {column_name} VARCHAR(128)"
        )
      )
      columns = {
        column["name"]: column
        for column in inspect(connection).get_columns(table_name)
      }
    if columns[column_name].get("nullable", True):
      connection.execute(
        text(
          f"UPDATE {table_name} "
          f"SET {column_name} = message_id "
          f"WHERE {column_name} IS NULL"
        )
      )
      connection.execute(
        text(
          f"ALTER TABLE {table_name} "
          f"ALTER COLUMN {column_name} SET NOT NULL"
        )
      )

    indexes = {
      index["name"]: index
      for index in inspect(connection).get_indexes(table_name)
    }
    constraints = {
      constraint["name"]: constraint
      for constraint in inspect(connection).get_unique_constraints(table_name)
      if constraint.get("name")
    }
    existing = indexes.get(index_name) or constraints.get(index_name)
    if existing is not None and not existing.get("unique", True):
      raise RuntimeError(
        f"{index_name} exists but is not unique; refusing unsafe startup"
      )
    if existing is None:
      connection.execute(
        text(
          f"CREATE UNIQUE INDEX {index_name} "
          f"ON {table_name} ({column_name})"
        )
      )

  tables = set(inspect(connection).get_table_names())
  pairs = (
    ("market_data_requests", "market_data_request"),
    ("market_data_transfers", "market_data_transfer"),
  )
  for old_name, new_name in pairs:
    if old_name in tables and new_name in tables:
      raise RuntimeError(
        f"Both {old_name} and {new_name} exist; refusing an ambiguous merge"
      )
    if old_name in tables:
      connection.execute(text(f"ALTER TABLE {old_name} RENAME TO {new_name}"))
      tables.remove(old_name)
      tables.add(new_name)
  if "market_data_request" in tables:
    columns = {
      column["name"]
      for column in inspect(connection).get_columns("market_data_request")
    }
    if "processing_error" not in columns:
      connection.execute(
        text("ALTER TABLE market_data_request ADD COLUMN processing_error TEXT")
      )
  if "trade_command_outbox" in tables:
    ensure_idempotency_column(
      "trade_command_outbox",
      "idempotency_key",
      "uq_trade_command_idempotency",
    )
  if "agent_report_inbox" in tables:
    ensure_idempotency_column(
      "agent_report_inbox",
      "business_idempotency_key",
      "uq_agent_report_business_idempotency",
    )
    report_columns = {
      column["name"]
      for column in inspect(connection).get_columns("agent_report_inbox")
    }
    if "processing_attempts" not in report_columns:
      connection.execute(
        text(
          "ALTER TABLE agent_report_inbox "
          "ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0"
        )
      )
    if "next_attempt_at" not in report_columns:
      connection.execute(
        text(
          "ALTER TABLE agent_report_inbox "
          "ADD COLUMN next_attempt_at TIMESTAMP"
        )
      )


def _ensure_compat_columns(connection):
  """Apply the small additive migrations required by create-all deployments."""
  from sqlalchemy import inspect, text

  inspector = inspect(connection)
  tables = set(inspector.get_table_names())

  def ensure_index(table_name: str, index_name: str, columns: str) -> None:
    if table_name not in tables:
      return
    existing = {
      index["name"] for index in inspect(connection).get_indexes(table_name)
    }
    if index_name not in existing:
      connection.execute(
        text(f"CREATE INDEX {index_name} ON {table_name} ({columns})")
      )

  if "positions" in tables:
    position_columns = {
      column["name"] for column in inspector.get_columns("positions")
    }
    if "last_price" not in position_columns:
      connection.execute(
        text("ALTER TABLE positions ADD COLUMN last_price NUMERIC(10, 4)")
      )
  if "pending_trade_orders" in tables:
    pending_columns = {
      column["name"]
      for column in inspector.get_columns("pending_trade_orders")
    }
    pending_additions = {
      "execution_mode": "VARCHAR(16) NOT NULL DEFAULT 'paper'",
      "strategy_run_id": "VARCHAR(36)",
      "strategy_order_id": "VARCHAR(128)",
      "intent_id": "VARCHAR(128)",
      "batch_id": "VARCHAR(36)",
      "bucket": "VARCHAR(32) NOT NULL DEFAULT 'manual'",
      "t_trade_role": "VARCHAR(16)",
      "risk_decision_id": "VARCHAR(128)",
      "trace_id": "VARCHAR(128)",
      "substitution_plan": "JSON",
      "request_metadata": "JSON NOT NULL DEFAULT '{}'",
      "last_source_sequence": "BIGINT NOT NULL DEFAULT 0",
      "last_source_event_at": "TIMESTAMP",
    }
    for column_name, definition in pending_additions.items():
      if column_name not in pending_columns:
        connection.execute(
          text(
            f"ALTER TABLE pending_trade_orders "
            f"ADD COLUMN {column_name} {definition}"
          )
        )
  if "t_trade_global_configs" in tables:
    columns = {
      column["name"] for column in inspector.get_columns("t_trade_global_configs")
    }
    if "strategy_run_id" not in columns:
      connection.execute(
        text("ALTER TABLE t_trade_global_configs ADD COLUMN strategy_run_id VARCHAR(36)")
      )
    if "universe_revision" not in columns:
      connection.execute(
        text(
          "ALTER TABLE t_trade_global_configs "
          "ADD COLUMN universe_revision INTEGER NOT NULL DEFAULT 0"
        )
      )
  ensure_index(
    "pending_trade_orders",
    "ix_pending_trade_order_account_batch_client",
    "account_id, batch_id, client_order_id",
  )
  ensure_index(
    "strategy_runtime_events",
    "ix_strategy_runtime_event_client_created",
    "client_order_id, created_at, event_id",
  )
  ensure_index(
    "t_trade_batches",
    "ix_t_trade_batch_account_updated",
    "account_id, updated_at, batch_id",
  )
  ensure_index(
    "strategy_trade_intents",
    "ix_trade_intent_run_reason_direction_created",
    "strategy_run_id, reason, direction, created_at, id",
  )
  if "strategies" in tables:
    strategy_columns = {
      column["name"] for column in inspector.get_columns("strategies")
    }
    if "instrument_universe_mode" not in strategy_columns:
      connection.execute(
        text(
          "ALTER TABLE strategies ADD COLUMN "
          "instrument_universe_mode VARCHAR(32) NOT NULL DEFAULT 'STATIC'"
        )
      )
  if "agent_report_inbox" in tables:
    report_columns = {
      column["name"]
      for column in inspector.get_columns("agent_report_inbox")
    }
    if "protocol_version" not in report_columns:
      connection.execute(
        text(
          "ALTER TABLE agent_report_inbox "
          "ADD COLUMN protocol_version VARCHAR(16) NOT NULL DEFAULT '1.0'"
        )
      )
async def create_tables():
  """创建表"""
  import quantx_infrastructure.models  # noqa: F401  # 确保所有模型注册到 Base.metadata

  async with engine.begin() as conn:
    await conn.run_sync(_prepare_runtime_message_boxes)
    await conn.run_sync(Base.metadata.create_all)
    await conn.run_sync(_ensure_compat_columns)


# 导出所有需要的组件
__all__ = [
  "Base",
  "BaseModel",
  "TimestampMixin",
  "BaseRepository",
  "BulkSaveResult",
  "WhereBuilder",
  "create_tables",
  "get_async_db",
  "engine",
]
