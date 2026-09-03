from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import report_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.services import order_service as order_service_module
from quantx_infrastructure.services import trade_service as trade_service_module
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _load_revision() -> ModuleType:
  path = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "infrastructure"
    / "alembic"
    / "versions"
    / "20260903_0047_widen_broker_order_identity.py"
  )
  spec = importlib.util.spec_from_file_location(
    "quantx_test_broker_order_identity_width_revision",
    path,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_revision_and_models_use_the_same_widened_non_nullable_identity() -> None:
  revision = _load_revision()
  calls: list[tuple[str, str, dict]] = []

  def alter_column(table_name: str, column_name: str, **kwargs) -> None:
    calls.append((table_name, column_name, kwargs))

  original_alter_column = revision.op.alter_column
  revision.op.alter_column = alter_column
  try:
    revision.upgrade()
  finally:
    revision.op.alter_column = original_alter_column

  assert revision.revision == "20260903_0047"
  assert revision.down_revision == "20260903_0046"
  assert [(table, column) for table, column, _ in calls] == [
    ("orders", "order_sysid"),
    ("trades", "order_sysid"),
  ]
  for table_name, _column_name, kwargs in calls:
    assert kwargs["existing_type"].length == 10
    assert kwargs["type_"].length == 32
    assert kwargs["existing_nullable"] is False

  order_sysid = Order.__table__.c.order_sysid
  trade_sysid = Trade.__table__.c.order_sysid
  assert order_sysid.type.length == 32
  assert order_sysid.unique is True
  assert order_sysid.nullable is False
  assert trade_sysid.type.length == 32
  assert trade_sysid.nullable is False


def test_revision_downgrade_is_disabled_for_non_lossless_identity_change() -> None:
  with pytest.raises(RuntimeError, match="downgrades"):
    _load_revision().downgrade()


@pytest.mark.asyncio
async def test_order_and_trade_report_upsert_preserves_long_shared_order_identity(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[Order.__table__, Trade.__table__],
      )
    )

  async def db_provider():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(order_service_module, "get_async_db", db_provider)
  monkeypatch.setattr(trade_service_module, "get_async_db", db_provider)
  monkeypatch.setattr(
    report_processor,
    "_update_pending",
    AsyncMock(return_value=report_processor.PendingOrderUpdate(False)),
  )
  monkeypatch.setattr(
    report_processor,
    "_consume_exact_auto_entry_fill",
    AsyncMock(),
  )
  monkeypatch.setattr(
    report_processor,
    "AutoExitPlanService",
    lambda: SimpleNamespace(
      apply_order_event_for_report=AsyncMock(),
      apply_execution_for_report=AsyncMock(),
    ),
  )

  order_sysid = "QMT-ORDER-12345"
  timestamp = int(datetime.now(timezone.utc).timestamp())
  await report_processor._process_order_report(
    {
      "client_order_id": "client-1",
      "source_sequence": 1,
      "order": {
        "order_id": 1001,
        "account_id": "account-1",
        "stock_code": "600000.SH",
        "order_sysid": order_sysid,
        "order_time": timestamp,
        "order_type": 23,
        "order_volume": 100,
        "price_type": 50,
        "price": 10.5,
        "traded_volume": 0,
        "traded_price": 0,
        "order_status": 50,
      },
    }
  )
  await report_processor._process_execution_report(
    {
      "client_order_id": "client-1",
      "source_sequence": 2,
      "execution": {
        "account_id": "account-1",
        "stock_code": "600000.SH",
        "order_id": 1001,
        "execution_id": "trade-1001",
        "traded_price": 10.5,
        "traded_volume": 100,
      },
    }
  )

  assert len(order_sysid) > 10

  async with sessions() as db:
    persisted_order = await db.get(Order, 1001)
    persisted_trade = await db.get(Trade, "trade-1001")
    assert persisted_order is not None
    assert persisted_trade is not None
    assert persisted_order.sysid == order_sysid
    assert persisted_trade.order_sysid == persisted_order.sysid
    assert persisted_trade.order_id == persisted_order.id
    assert persisted_trade.id == "trade-1001"

  await engine.dispose()
