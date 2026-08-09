from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_infrastructure.models.enums import InstrumentType, OrderType, PriceType
from quantx_infrastructure.services.trading_service import (
  InvalidOrderError,
  TradingService,
)


def make_stock_info(**overrides):
  data = {
    "id": "000001.SZ",
    "type": InstrumentType.STOCK,
    "min_market_order_volume": 100,
    "max_market_order_volume": 1000000,
    "up_stop_price": Decimal("11.00"),
    "down_stop_price": Decimal("9.00"),
  }
  data.update(overrides)
  return SimpleNamespace(**data)


def test_close_position_sell_allows_odd_lot_volume():
  service = TradingService.__new__(TradingService)

  assert service._validate_order_volume(
    150,
    make_stock_info(),
    order_type=OrderType.SELL,
    close_position=True,
  )


def test_ordinary_sell_still_rejects_odd_lot_volume():
  service = TradingService.__new__(TradingService)

  assert not service._validate_order_volume(
    150,
    make_stock_info(),
    order_type=OrderType.SELL,
    close_position=False,
  )


def test_calculate_commission_uses_default_fee_constants():
  service = TradingService.__new__(TradingService)

  assert service._calculate_commission(Decimal("6041"), OrderType.BUY) == Decimal(
    "5.06041"
  )


@pytest.mark.asyncio
async def test_market_order_returns_queued_client_order_without_broker_id():
  service = TradingService(account_id="account-1")
  queued = SimpleNamespace(
    client_order_id="client-1",
    status="QUEUED",
  )

  class SessionContext:
    async def __aenter__(self):
      return object()

    async def __aexit__(self, *_):
      return None

  with patch(
    "quantx_infrastructure.services.trading_service.AsyncSessionLocal",
    return_value=SessionContext(),
  ), patch(
    "quantx_infrastructure.services.trading_service.TradeCommandService"
  ) as command_service:
    command_service.return_value.enqueue_order_for_account = AsyncMock(
      return_value=queued
    )
    result = await service.place_order(
      stock_code="000001.SZ",
      order_type=OrderType.BUY,
      order_volume=100,
      price_type=PriceType.MARKET_CONVERT_5_LIMIT,
      price=0,
    )

  assert result == {
    "success": True,
    "order_id": None,
    "client_order_id": "client-1",
    "status": "QUEUED",
    "message": "交易命令已排队",
  }
  command_service.return_value.enqueue_order_for_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_volume_is_rejected_before_command_queue_access():
  service = TradingService(account_id="account-1")

  with pytest.raises(InvalidOrderError, match="订单数量必须大于 0"):
    await service.place_order(
      stock_code="000001.SZ",
      order_type=OrderType.BUY,
      order_volume=0,
      price_type=PriceType.FIX_PRICE,
      price=10,
    )
