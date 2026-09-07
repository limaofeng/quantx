"""Portfolio sell caps never impersonate the broker's odd-lot sellability."""

from copy import deepcopy
from decimal import Decimal

import pytest
from quantx_domain.brokers.base import OrderType
from quantx_domain.strategies.base import TradeIntent
from quantx_domain.trading.order_sizer import OrderSizer


def sell(*, available, cap, requested=200, side=OrderType.SELL):
  intent = TradeIntent(
    strategy_id="test",
    run_id="test-run",
    instrument_code="600000.SH",
    direction=side.value,
    bucket="core",
    reason="capacity test",
    target_volume=requested,
  )
  original = deepcopy(intent)
  result = OrderSizer().draft_intent(
    intent,
    side,
    10,
    {},
    {"long_volume": available, "available_volume": available},
    sell_volume_cap=cap,
  )
  assert intent == original
  assert result.raw_target_volume == requested
  return result


@pytest.mark.parametrize(
  "available,cap,requested,expected",
  [
    (1000, 50, 100, 0),
    (50, 50, 100, 50),
    (1000, 150, 200, 100),
    (1000, 0, 200, 0),
    (1000, 500, 100, 100),
    (1000, None, 200, 200),
    (1050, 1050, 1050, 1050),
    (1050, 1000, 1050, 1000),
  ],
)
def test_independent_sell_cap_preserves_real_odd_lot_boundary(
  available, cap, requested, expected
):
  result = sell(available=available, cap=cap, requested=requested)
  assert result.sized_volume == expected
  if cap is not None:
    assert result.metadata["sell_volume_cap"] == cap
    assert result.metadata["broker_available_volume"] == available
    assert ("SELL_CAPACITY_CAP" in result.size_reason_codes) == (cap < requested)
  assert "liquidity_cap_amount" not in result.metadata


@pytest.mark.parametrize(
  "cap", [-1, True, False, 1.0, float("nan"), float("inf"), Decimal(1), "100"]
)
def test_sell_cap_requires_an_explicit_nonnegative_integer(cap):
  with pytest.raises(ValueError, match="SELL_VOLUME_CAP_INVALID"):
    sell(available=1000, cap=cap)


def test_buy_cannot_use_sell_cap():
  with pytest.raises(ValueError, match="SELL_VOLUME_CAP_REQUIRES_SELL"):
    sell(available=1000, cap=100, side=OrderType.BUY)
