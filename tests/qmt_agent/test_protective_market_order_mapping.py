from quantx_qmt_agent.miniqmt.local_agent import _to_miniqmt_price_type
from quantx_qmt_agent.qmt_types import PriceType


def test_protective_market_order_keeps_five_level_ioc_semantics():
  assert _to_miniqmt_price_type("MARKET_CONVERT_5_LIMIT") == (
    PriceType.MARKET_CONVERT_5_LIMIT
  )


def test_plain_limit_order_remains_fixed_price():
  assert _to_miniqmt_price_type("LIMIT", 10.25) == PriceType.FIX_PRICE
