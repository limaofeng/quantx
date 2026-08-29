import pytest
from quantx_qmt_agent.miniqmt.local_agent import _to_miniqmt_price_type
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
from quantx_qmt_agent.qmt_types import OrderType, PriceType
from xtquant import xtconstant


def test_protective_market_order_keeps_five_level_ioc_semantics():
  assert _to_miniqmt_price_type("MARKET_CONVERT_5_LIMIT") == (
    PriceType.MARKET_CONVERT_5_LIMIT
  )


def test_plain_limit_order_remains_fixed_price():
  assert _to_miniqmt_price_type("LIMIT", 10.25) == PriceType.FIX_PRICE


@pytest.mark.parametrize(
  ("stock_code", "expected_price_type"),
  [
    ("600000.SH", xtconstant.MARKET_SH_CONVERT_5_CANCEL),
    ("000001.SZ", xtconstant.MARKET_SZ_CONVERT_5_CANCEL),
    ("920001.BJ", xtconstant.MARKET_SH_CONVERT_5_CANCEL),
  ],
)
def test_protective_sell_maps_to_exchange_five_level_ioc(
  stock_code,
  expected_price_type,
):
  submitted = {}

  class Trader:
    def order_stock(self, **kwargs):
      submitted.update(kwargs)
      return 123

  manager = object.__new__(XTTradingManager)
  manager.is_connected = True
  manager.acc = object()
  manager.xttrader = Trader()

  result = manager.place_order(
    stock_code=stock_code,
    order_type=OrderType.SELL,
    order_volume=100,
    price_type=PriceType.MARKET_CONVERT_5_LIMIT,
    price=10.25,
    strategy_name="T trade exit",
    order_remark="exit-plan-1",
  )

  assert result["success"] is True
  assert submitted["order_type"] == xtconstant.STOCK_SELL
  assert submitted["price_type"] == expected_price_type
  assert submitted["price"] == 0.0
