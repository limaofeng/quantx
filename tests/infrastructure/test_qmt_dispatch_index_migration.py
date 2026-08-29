from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  TradeCommandOutbox,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260829_0039_qmt_dispatch_indexes.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_qmt_dispatch_indexes_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_revision_creates_qmt_dispatch_indexes(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision()
  created: list[tuple[str, str, tuple[object, ...]]] = []
  monkeypatch.setattr(
    revision.op,
    "create_index",
    lambda name, table_name, columns: created.append(
      (name, table_name, tuple(columns))
    ),
  )

  revision.upgrade()

  assert revision.revision == "20260829_0039"
  assert revision.down_revision == "20260829_0037"
  assert created[0] == (
    "ix_trade_command_device_status_expiry_created",
    "trade_command_outbox",
    ("device_id", "delivery_status", "expires_at", "created_at"),
  )
  assert created[1] == (
    "ix_trade_command_device_status_delivery_expiry",
    "trade_command_outbox",
    ("device_id", "delivery_status", "delivered_at", "expires_at"),
  )
  assert created[2][0:2] == (
    "ix_trade_command_device_status_kind_created",
    "trade_command_outbox",
  )
  assert tuple(str(column) for column in created[2][2]) == (
    "device_id",
    "delivery_status",
    "upper(payload ->> 'command_kind')",
    "created_at",
  )
  assert created[3] == (
    "ix_market_data_request_device_status_created",
    "market_data_request",
    ("device_id", "status", "created_at"),
  )
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_models_expose_the_same_dispatch_index_contract() -> None:
  trade_indexes = {
    index.name: str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for index in TradeCommandOutbox.__table__.indexes
  }
  market_indexes = {
    index.name: tuple(column.name for column in index.columns)
    for index in MarketDataRequest.__table__.indexes
  }

  assert (
    "(device_id, delivery_status, expires_at, created_at)"
    in trade_indexes["ix_trade_command_device_status_expiry_created"]
  )
  assert (
    "(device_id, delivery_status, delivered_at, expires_at)"
    in trade_indexes["ix_trade_command_device_status_delivery_expiry"]
  )
  assert (
    "(device_id, delivery_status, upper(payload ->> 'command_kind'), created_at)"
    in trade_indexes["ix_trade_command_device_status_kind_created"]
  )
  assert market_indexes["ix_market_data_request_device_status_created"] == (
    "device_id",
    "status",
    "created_at",
  )
