from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.brokers.base import OrderRequest, OrderStatus, OrderType, PriceType
from quantx_domain.clock import utcnow
from quantx_infrastructure.core.brokers.live import LiveBroker
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
)
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_PRE_BROKER_ZERO_FILL_SOURCE,
)


class _TradingService:
  def __init__(self) -> None:
    self.position_service = SimpleNamespace(get_positions=self._get_positions)

  async def get_account_info(self, realtime: bool = False):
    assert realtime is True
    return SimpleNamespace(
      account_id="account-1",
      total_asset=Decimal("123456.78"),
      cash=Decimal("23456.78"),
      frozen_cash=Decimal("100.25"),
      market_value=Decimal("100000.00"),
    )

  async def _get_positions(self, *, account_id: str):
    assert account_id == "account-1"
    return []


def _sell_request(*, price: float = 10.0, volume: int = 100) -> OrderRequest:
  return OrderRequest(
    instrument_code="600000.SH",
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=volume,
    price=price,
    metadata={"intent_id": "intent-1"},
  )


@pytest.mark.asyncio
async def test_get_account_normalizes_database_decimals_to_domain_floats(monkeypatch) -> None:
  broker = LiveBroker(account_id="account-1", initial_capital=Decimal("100000.00"))
  broker.trading_service = _TradingService()
  broker.is_connected = True
  stamp = utcnow()
  control = SimpleNamespace(last_snapshot_at=stamp, reconcile_status="READY")
  db = SimpleNamespace(
    get=AsyncMock(return_value=control),
    scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
  )

  class Session:
    async def __aenter__(self):
      return db

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr("quantx_infrastructure.database.AsyncSessionLocal", Session)
  monkeypatch.setattr(
    "quantx_infrastructure.services.account_capacity_service.load_authoritative_account_snapshot",
    AsyncMock(return_value={
      "accounts": [vars(await broker.trading_service.get_account_info(realtime=True))],
      "positions_by_account": {"account-1": []}, "orders": [],
    }),
  )

  account = await broker.get_account()

  assert broker.initial_capital == 100000.0
  assert account.last_update_time == stamp
  assert account.total_asset == pytest.approx(123456.78)
  assert account.cash == pytest.approx(23456.78)
  assert account.frozen_cash == pytest.approx(100.25)
  assert account.market_value == pytest.approx(100000.0)
  assert account.total_pnl == pytest.approx(23456.78)
  assert all(
    isinstance(value, float)
    for value in (
      account.total_asset,
      account.cash,
      account.frozen_cash,
      account.market_value,
      account.total_pnl,
    )
  )


@pytest.mark.asyncio
async def test_disconnected_rejection_is_authoritative_local_zero_fill() -> None:
  broker = LiveBroker(account_id="account-1")
  request = _sell_request()

  order = await broker.place_order(request)

  assert order.status is OrderStatus.REJECTED
  assert request.metadata["execution_terminal_source"] == (
    LOCAL_PRE_BROKER_ZERO_FILL_SOURCE
  )
  assert request.metadata["execution_terminal_reason"] == "未连接到交易系统"


@pytest.mark.asyncio
async def test_local_risk_rejection_is_authoritative_zero_fill() -> None:
  broker = LiveBroker(account_id="account-1", max_order_amount=10.0)
  broker.trading_service = _TradingService()
  broker.is_connected = True
  request = OrderRequest(
    instrument_code="600000.SH",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=10.0,
    metadata={"intent_id": "intent-1"},
  )

  order = await broker.place_order(request)

  assert order.status is OrderStatus.REJECTED
  assert request.metadata["execution_terminal_source"] == (
    LOCAL_PRE_BROKER_ZERO_FILL_SOURCE
  )
  assert "超过限制" in request.metadata["execution_terminal_reason"]


@pytest.mark.asyncio
async def test_local_risk_exception_is_authoritative_zero_fill() -> None:
  broker = LiveBroker(account_id="account-1")
  broker.trading_service = _TradingService()
  broker.is_connected = True

  async def broken_risk_check(_request: OrderRequest):
    raise RuntimeError("local risk snapshot unavailable")

  broker._risk_check = broken_risk_check  # type: ignore[method-assign]
  request = _sell_request()

  order = await broker.place_order(request)

  assert order.status is OrderStatus.REJECTED
  assert request.metadata["execution_terminal_source"] == (
    LOCAL_PRE_BROKER_ZERO_FILL_SOURCE
  )
  assert request.metadata["execution_terminal_reason"] == (
    "local risk snapshot unavailable"
  )


@pytest.mark.asyncio
async def test_agent_unavailable_is_authoritative_pre_enqueue_zero_fill() -> None:
  class UnavailableTradingService:
    async def place_order(self, **_kwargs):
      raise AgentUnavailableError("没有就绪 QMT Agent")

  broker = LiveBroker(account_id="account-1", enable_risk_control=False)
  broker.trading_service = UnavailableTradingService()
  broker.is_connected = True
  request = _sell_request()

  order = await broker.place_order(request)

  assert order.status is OrderStatus.REJECTED
  assert request.metadata["execution_terminal_source"] == (
    LOCAL_PRE_BROKER_ZERO_FILL_SOURCE
  )
  assert request.metadata["execution_terminal_reason"] == "没有就绪 QMT Agent"


@pytest.mark.asyncio
async def test_enqueue_outcome_unknown_is_not_fabricated_as_rejection() -> None:
  class OutcomeUnknownTradingService:
    async def place_order(self, **_kwargs):
      raise RuntimeError("connection lost while commit outcome is unknown")

  broker = LiveBroker(account_id="account-1", enable_risk_control=False)
  broker.trading_service = OutcomeUnknownTradingService()
  broker.is_connected = True
  request = _sell_request()

  with pytest.raises(RuntimeError, match="commit outcome is unknown"):
    await broker.place_order(request)

  assert "execution_terminal_source" not in request.metadata
  assert broker.orders == {}
