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
