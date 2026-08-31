import pytest
from quantx_domain.brokers.base import OrderType
from quantx_domain.strategies.base import TradeIntent, TradeIntentDirection
from quantx_domain.trading.order_sizer import OrderSizer


@pytest.mark.parametrize(
  "position,expected",
  [
    ({"available_volume": 100}, 100),
    ({"available_volume": 99}, 0),
    ({"available_volume": 1000, "locked_core_available_volume": 900}, 100),
    ({"available_volume": 1000, "t_trade_exit_capacity": 200}, 200),
    ({}, 0),
  ],
)
def test_positive_t_is_sized_by_old_share_exit_capacity(position, expected):
  intent = TradeIntent(
    strategy_id="strategy",
    run_id="run",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="T_ENTRY",
    target_amount=10000,
    metadata={"t_trade_role": "entry", "t_trade_exit_capacity": 1000000},
  )
  draft = OrderSizer().draft_intent(
    intent,
    OrderType.BUY,
    10,
    {"available_cash": 100000},
    position,
  )
  assert draft.raw_target_volume == 1000
  assert draft.sized_volume == expected
  assert "T_TRADE_OLD_INVENTORY_CAP" in draft.size_reason_codes
