import pytest
from quantx_qmt_agent.miniqmt.local_agent import _to_miniqmt_price_type
from quantx_qmt_agent.miniqmt.trading.trading_manager import XTTradingManager
from quantx_qmt_agent.qmt_types import OrderType, PriceType


@pytest.mark.parametrize("value", ["MARKET_CONVERT_5_LIMIT", "LIMIT", "MARKET"])
def test_non_fixed_price_order_is_rejected(value):
  with pytest.raises(ValueError, match="unsupported order price type"):
    _to_miniqmt_price_type(value)


def test_fixed_price_order_is_accepted():
  assert _to_miniqmt_price_type("FIX_PRICE", 10.25) == PriceType.FIX_PRICE


def test_protective_sell_rejects_non_fixed_price_before_socket_write():
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
    stock_code="600000.SH",
    order_type=OrderType.SELL,
    order_volume=100,
    price_type=PriceType.MARKET_CONVERT_5_LIMIT,
    price=10.25,
    strategy_name="",
    order_remark="qx:00000000000000000000",
  )

  assert result["success"] is False
  assert submitted == {}
