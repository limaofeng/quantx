from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.quantx_mcp.tools import AccountTools, MarketDataTools, OrderTools
from quantx_infrastructure.models.enums import OrderStatus
from quantx_infrastructure.services.trade_command_service import QueuedTradeCommand


def test_market_tools_publish_durable_and_realtime_capabilities() -> None:
  names = {tool.name for tool in MarketDataTools().get_tools()}

  assert "market_data_get_realtime" in names
  assert "market_data_get_historical" in names
  assert "market_data_list_instruments" in names


@pytest.mark.asyncio
async def test_realtime_tool_returns_observed_tick(monkeypatch) -> None:
  from quantx_api.market_data_read_service import market_data_read_service

  tick = SimpleNamespace(
    last_price=12.5,
    volume=100_000,
    amount=1_250_000,
    bid_price=[12.49],
    ask_price=[12.51],
    bid_volume=[100],
    ask_volume=[200],
    timestamp=None,
  )
  monkeypatch.setattr(
    market_data_read_service,
    "get_latest_price",
    AsyncMock(return_value=tick),
  )

  result = await MarketDataTools()._get_realtime(
    {"symbol": "000001.SZ", "fields": ["last_price", "volume"]}
  )

  assert result["status"] == "success"
  assert result["data"] == {
    "symbol": "000001.SZ",
    "last_price": 12.5,
    "volume": 100_000,
  }


@pytest.mark.asyncio
async def test_account_positions_are_database_backed(monkeypatch) -> None:
  from quantx_infrastructure.services.position_service import PositionService

  position = SimpleNamespace(
    stock_code="000001.SZ",
    volume=100,
    can_use_volume=80,
    avg_price=10,
    market_value=1_200,
  )
  monkeypatch.setattr(
    PositionService,
    "get_positions",
    AsyncMock(return_value=[position]),
  )

  result = await AccountTools()._get_positions({"account_id": "account-1"})

  assert result["status"] == "success"
  assert result["count"] == 1
  assert result["positions"][0]["symbol"] == "000001.SZ"


@pytest.mark.asyncio
async def test_create_order_returns_queued_trade_command(monkeypatch) -> None:
  from quantx_infrastructure.database import relational_connection
  from quantx_infrastructure.services.trade_command_service import TradeCommandService

  @asynccontextmanager
  async def fake_session():
    yield object()

  enqueue = AsyncMock(
    return_value=QueuedTradeCommand("client-1", "message-1", "QUEUED")
  )
  monkeypatch.setattr(relational_connection, "AsyncSessionLocal", fake_session)
  monkeypatch.setattr(TradeCommandService, "enqueue_order_for_account", enqueue)

  result = await OrderTools()._create_order(
    {
      "account_id": "account-1",
      "symbol": "000001.SZ",
      "side": "buy",
      "quantity": 100,
      "type": "limit",
      "price": 12.5,
      "idempotency_key": "mcp-request-1",
    }
  )

  assert result == {
    "status": "QUEUED",
    "client_order_id": "client-1",
    "message": "Order command queued",
  }
  assert enqueue.await_args.kwargs["idempotency_key"] == "mcp-request-1"


@pytest.mark.asyncio
async def test_order_status_never_fabricates_a_fill(monkeypatch) -> None:
  from quantx_infrastructure.services.order_service import OrderService

  monkeypatch.setattr(
    OrderService,
    "get_order_by_id",
    AsyncMock(return_value=None),
  )

  result = await OrderTools()._get_order_status(
    {"account_id": "account-1", "order_id": "123"}
  )

  assert result == {"status": "missing", "order_id": "123"}


@pytest.mark.asyncio
async def test_order_status_returns_reconciled_database_state(monkeypatch) -> None:
  from quantx_infrastructure.services.order_service import OrderService

  order = SimpleNamespace(
    order_status=OrderStatus.PART_SUCC,
    traded_volume=100,
    traded_price=12.4,
  )
  monkeypatch.setattr(
    OrderService,
    "get_order_by_id",
    AsyncMock(return_value=order),
  )

  result = await OrderTools()._get_order_status(
    {"account_id": "account-1", "order_id": "123"}
  )

  assert result["order_status"] == "PART_SUCC"
  assert result["traded_volume"] == 100
  assert result["traded_price"] == 12.4


def test_mcp_server_registers_all_tool_categories() -> None:
  from quantx_api.quantx_mcp.server import create_mcp_server

  server = create_mcp_server()
  names = {
    tool.name
    for category in (
      server.market_data_tools,
      server.strategy_tools,
      server.account_tools,
      server.order_tools,
      server.analysis_tools,
    )
    for tool in category.get_tools()
  }

  assert any(name.startswith("market_data_") for name in names)
  assert any(name.startswith("strategy_") for name in names)
  assert any(name.startswith("account_") for name in names)
  assert any(name.startswith("order_") for name in names)
  assert any(name.startswith("analysis_") for name in names)
