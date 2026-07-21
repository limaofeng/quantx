from decimal import Decimal
from types import SimpleNamespace

import pytest

from miniqmt.trading.trading_manager import OrderType
from models.enums import InstrumentType, PriceType
from services.trading_service import TradingService


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


@pytest.mark.asyncio
async def test_market_order_with_zero_price_skips_limit_price_check():
  service = TradingService.__new__(TradingService)

  async def is_trading_time(stock_info):
    return True

  service._is_trading_time = is_trading_time

  await service._risk_check(
    order_volume=100,
    price=0,
    stock_info=make_stock_info(),
    price_type=PriceType.MARKET_CONVERT_5_LIMIT,
  )


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


@pytest.mark.asyncio
async def test_buy_capacity_check_uses_default_fee_config_without_service_config():
  service = TradingService.__new__(TradingService)
  account = SimpleNamespace(cash=10000)

  await service._check_trading_capacity(
    OrderType.BUY,
    100,
    10,
    make_stock_info(),
    account,
  )


def test_calculate_commission_uses_default_fee_constants():
  service = TradingService.__new__(TradingService)

  assert service._calculate_commission(Decimal("6041"), OrderType.BUY) == Decimal(
    "5.06041"
  )


@pytest.mark.asyncio
async def test_try_execute_order_preserves_manager_failure_message():
  service = TradingService.__new__(TradingService)

  class TradingManagerStub:
    def place_order(self, **kwargs):
      return {"success": False, "message": "交易连接未建立"}

  service.trading_manager = TradingManagerStub()

  result = await service._try_execute_order(
    stock_code="000001.SZ",
    order_type=OrderType.BUY,
    order_volume=100,
    price_type=PriceType.FIX_PRICE,
    price=10,
  )

  assert result == {
    "success": False,
    "order_id": None,
    "message": "交易连接未建立",
  }
