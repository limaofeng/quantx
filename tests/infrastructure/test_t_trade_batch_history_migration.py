from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260829_0036_t_trade_batch_history.py"
)
TERMINAL_MIGRATION = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260829_0037_t_trade_batch_terminal_at.py"
)


def _revision(path: Path = MIGRATION):
  spec = importlib.util.spec_from_file_location(path.stem, path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_migration_backfills_lineage_without_current_defaults(monkeypatch) -> None:
  revision = _revision()
  engine = sa.create_engine("sqlite:///:memory:")
  metadata = sa.MetaData()
  batches = sa.Table(
    "t_trade_batches",
    metadata,
    sa.Column("batch_id", sa.String(36), primary_key=True),
    sa.Column("strategy_run_id", sa.String(36), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("entry_filled_volume", sa.Integer, nullable=False),
    sa.Column("exit_filled_volume", sa.Integer, nullable=False),
    sa.Column("account_id", sa.String(50), nullable=False),
  )
  correlations = sa.Table(
    "strategy_order_correlations",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("client_order_id", sa.String(128), nullable=False),
    sa.Column("batch_id", sa.String(36)),
    sa.Column("execution_mode", sa.String(16), nullable=False),
    sa.Column("t_trade_role", sa.String(16)),
    sa.Column("request_metadata", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
  )
  events = sa.Table(
    "strategy_runtime_events",
    metadata,
    sa.Column("event_id", sa.String(36), primary_key=True),
    sa.Column("client_order_id", sa.String(128), nullable=False),
    sa.Column("event_type", sa.String(24), nullable=False),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
  )
  metadata.create_all(engine)

  entry_at = datetime(2026, 8, 29, 1, 35)
  closed_at = datetime(2026, 8, 29, 2, 20)
  rejected_at = datetime(2026, 8, 29, 1, 40)
  costs = {
    "exit_plan_template": {
      "costs": {
        "commission_rate": 0.0003,
        "minimum_commission": 5.0,
        "stamp_tax_rate": 0.0005,
        "transfer_fee_rate": 0.00001,
      }
    }
  }
  with engine.begin() as connection:
    connection.execute(
      batches.insert(),
      [
        {
          "batch_id": "closed",
          "status": "CLOSED",
          "strategy_run_id": "run-closed",
          "entry_filled_volume": 100,
          "exit_filled_volume": 100,
          "account_id": "account-1",
        },
        {
          "batch_id": "rejected",
          "status": "ENTRY_REJECTED",
          "strategy_run_id": "run-rejected",
          "entry_filled_volume": 0,
          "exit_filled_volume": 0,
          "account_id": "account-1",
        },
      ],
    )
    connection.execute(
      correlations.insert(),
      [
        {
          "id": "c-entry",
          "client_order_id": "entry",
          "batch_id": "closed",
          "execution_mode": "live",
          "t_trade_role": "ENTRY",
          "request_metadata": costs,
          "created_at": entry_at,
        },
        {
          "id": "c-exit",
          "client_order_id": "exit",
          "batch_id": "closed",
          "execution_mode": "live",
          "t_trade_role": "EXIT",
          "request_metadata": {},
          "created_at": closed_at,
        },
        {
          "id": "c-rejected",
          "client_order_id": "rejected-entry",
          "batch_id": "rejected",
          "execution_mode": "paper",
          "t_trade_role": "ENTRY",
          "request_metadata": {},
          "created_at": rejected_at,
        },
      ],
    )
    connection.execute(
      events.insert(),
      [
        {
          "event_id": "e-entry",
          "client_order_id": "entry",
          "event_type": "TRADE",
          "payload": {
            "metadata": {"t_trade_role": "entry"},
            "report": {"traded_time": entry_at.isoformat()},
          },
          "created_at": entry_at,
        },
        {
          "event_id": "e-exit",
          "client_order_id": "exit",
          "event_type": "TRADE",
          "payload": {
            "metadata": {"t_trade_role": "exit"},
            "report": {"traded_time": closed_at.isoformat()},
          },
          "created_at": closed_at,
        },
        {
          "event_id": "e-rejected",
          "client_order_id": "rejected-entry",
          "event_type": "ORDER",
          "payload": {
            "metadata": {"t_trade_role": "entry"},
            "report": {
              "status": "REJECTED",
              "order_time": rejected_at.isoformat(),
            },
          },
          "created_at": rejected_at,
        },
      ],
    )

    monkeypatch.setattr(revision.op, "get_bind", lambda: connection)

    def add_column(table_name, column):
      type_sql = column.type.compile(dialect=connection.dialect)
      connection.execute(
        sa.text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_sql}")
      )

    def create_index(name, table_name, columns, unique=False):
      prefix = "UNIQUE " if unique else ""
      connection.execute(
        sa.text(
          f"CREATE {prefix}INDEX {name} ON {table_name} ({', '.join(columns)})"
        )
      )

    monkeypatch.setattr(revision.op, "add_column", add_column)
    monkeypatch.setattr(revision.op, "create_index", create_index)
    revision.upgrade()

    rows = {
      row["batch_id"]: row
      for row in connection.execute(
        sa.text("SELECT * FROM t_trade_batches")
      ).mappings()
    }

  assert rows["closed"]["metrics_origin"] == "LEGACY_BACKFILL"
  assert rows["closed"]["execution_mode"] == "live"
  assert rows["closed"]["commission_rate"] == 0.0003
  assert rows["closed"]["entry_filled_at"] == entry_at.isoformat(
    sep=" "
  )
  assert rows["closed"]["last_exit_filled_at"] == closed_at.isoformat(sep=" ")
  assert rows["closed"]["closed_at"] == closed_at.isoformat(sep=" ")
  assert rows["rejected"]["metrics_origin"] == "LEGACY_BACKFILL"
  assert rows["rejected"]["closed_at"] == rejected_at.isoformat(sep=" ")
  assert rows["rejected"]["commission_rate"] is None


def test_terminal_migration_separates_order_terminal_from_trade_close(
  monkeypatch,
) -> None:
  revision = _revision(TERMINAL_MIGRATION)
  engine = sa.create_engine("sqlite:///:memory:")
  metadata = sa.MetaData()
  batches = sa.Table(
    "t_trade_batches",
    metadata,
    sa.Column("batch_id", sa.String(36), primary_key=True),
    sa.Column("account_id", sa.String(50), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("entry_filled_volume", sa.Integer, nullable=False),
    sa.Column("exit_filled_volume", sa.Integer, nullable=False),
    sa.Column("closed_at", sa.DateTime),
  )
  correlations = sa.Table(
    "strategy_order_correlations",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("client_order_id", sa.String(128), nullable=False),
    sa.Column("batch_id", sa.String(36)),
    sa.Column("t_trade_role", sa.String(16)),
  )
  events = sa.Table(
    "strategy_runtime_events",
    metadata,
    sa.Column("event_id", sa.String(36), primary_key=True),
    sa.Column("client_order_id", sa.String(128), nullable=False),
    sa.Column("event_type", sa.String(24), nullable=False),
    sa.Column("payload", sa.JSON, nullable=False),
  )
  metadata.create_all(engine)

  closed_at = datetime(2026, 8, 29, 2, 20)
  rejected_at = datetime(2026, 8, 29, 1, 40)
  with engine.begin() as connection:
    connection.execute(
      batches.insert(),
      [
        {
          "batch_id": "closed",
          "account_id": "account-1",
          "status": "CLOSED",
          "entry_filled_volume": 100,
          "exit_filled_volume": 100,
          "closed_at": closed_at,
        },
        {
          "batch_id": "rejected",
          "account_id": "account-1",
          "status": "ENTRY_REJECTED",
          "entry_filled_volume": 0,
          "exit_filled_volume": 0,
          "closed_at": rejected_at,
        },
        {
          "batch_id": "updated-only",
          "account_id": "account-1",
          "status": "ENTRY_EXPIRED",
          "entry_filled_volume": 0,
          "exit_filled_volume": 0,
          "closed_at": rejected_at,
        },
      ],
    )
    connection.execute(
      correlations.insert(),
      [
        {
          "id": "c-rejected",
          "client_order_id": "rejected-entry",
          "batch_id": "rejected",
          "t_trade_role": "ENTRY",
        },
        {
          "id": "c-updated",
          "client_order_id": "updated-entry",
          "batch_id": "updated-only",
          "t_trade_role": "ENTRY",
        },
      ],
    )
    connection.execute(
      events.insert(),
      [
        {
          "event_id": "e-rejected",
          "client_order_id": "rejected-entry",
          "event_type": "ORDER",
          "payload": {
            "report": {
              "status": "REJECTED",
              "order_time": rejected_at.isoformat(),
            }
          },
        },
        {
          "event_id": "e-updated",
          "client_order_id": "updated-entry",
          "event_type": "ORDER",
          "payload": {
            "report": {
              "status": "EXPIRED",
              "updated_at": rejected_at.isoformat(),
            }
          },
        },
      ],
    )

    monkeypatch.setattr(revision.op, "get_bind", lambda: connection)

    def add_column(table_name, column):
      type_sql = column.type.compile(dialect=connection.dialect)
      connection.execute(
        sa.text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_sql}")
      )

    def create_index(name, table_name, columns, unique=False):
      prefix = "UNIQUE " if unique else ""
      connection.execute(
        sa.text(
          f"CREATE {prefix}INDEX {name} ON {table_name} ({', '.join(columns)})"
        )
      )

    monkeypatch.setattr(revision.op, "add_column", add_column)
    monkeypatch.setattr(revision.op, "create_index", create_index)
    revision.upgrade()

    rows = {
      row["batch_id"]: row
      for row in connection.execute(
        sa.text("SELECT * FROM t_trade_batches")
      ).mappings()
    }

  assert datetime.fromisoformat(str(rows["closed"]["closed_at"])) == closed_at
  assert datetime.fromisoformat(str(rows["closed"]["terminal_at"])) == closed_at
  assert rows["rejected"]["closed_at"] is None
  assert datetime.fromisoformat(str(rows["rejected"]["terminal_at"])) == rejected_at
  assert rows["updated-only"]["closed_at"] is None
  assert rows["updated-only"]["terminal_at"] is None
