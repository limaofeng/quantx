from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal as D

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.strategies.base import TradeIntent, TradeIntentDirection
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import TradingRiskChecker


def intent(**changes):
  return TradeIntent(
    strategy_id="strategy",
    run_id="run",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="T_ENTRY",
    **({"target_amount": 10000} | changes),
  )


def draft(value=None, *, cap=None, price=10, position=None):
  return OrderSizer().draft_intent(
    value or intent(),
    OrderType.BUY,
    price,
    {"available_cash": 1000000},
    position,
    allocated_amount_cap=cap,
  )


@pytest.mark.parametrize(
  "cap,volume",
  [
    (None, 1000),
    (D("10005.10"), 1000),
    (D("5005.05"), 500),
    (D("5005.049999"), 400),
    (D("1005.01"), 100),
    (D("1005.009999"), 0),
    (D(0), 0),
  ],
)
def test_cap_includes_minimum_commission_and_transfer_fee(cap, volume):
  result = draft(cap=cap)
  assert result.sized_volume == volume
  assert result.raw_target_amount == 10000
  assert result.raw_target_volume == 1000
  if cap is not None:
    assert D(result.metadata["allocation_cash_required"]) <= cap
    assert D(result.metadata["allocation_estimated_fee_cny"]) == D(
      str(estimate_buy_fee_cny(price=10, volume=volume))
    )
  if volume < 1000:
    assert "ALLOCATION_AMOUNT_CAP" in result.size_reason_codes
  if volume == 0:
    assert "MIN_LOT_EXCEEDS_ALLOCATION_BUDGET" in result.size_reason_codes
    assert "ZERO_SIZED_VOLUME" in result.size_reason_codes


def test_percentage_commission_boundary_and_requested_volume_are_preserved():
  value = intent(target_amount=None, target_volume=10000)
  assert draft(value, cap=D("100031")).sized_volume == 10000
  assert draft(value, cap=D("100030.99")).sized_volume == 9900


def test_cap_does_not_mutate_intent_or_impersonate_liquidity_limit():
  value = intent(metadata={"evidence": {"candidate": "one"}})
  before = deepcopy(value)
  result = draft(value, cap=3000)
  assert value == before
  assert result.raw_target_amount == value.target_amount
  assert result.sized_volume == 200
  assert "liquidity_cap_amount" not in result.metadata
  assert "LIQUIDITY_PARTICIPATION_CAP" not in result.size_reason_codes


def test_allocation_never_relaxes_liquidity_or_old_inventory_cap():
  value = intent(metadata={"liquidity_cap_amount": 6000, "t_trade_role": "entry"})
  result = draft(value, cap=5005.05, position={"t_trade_exit_capacity": 300})
  assert result.sized_volume == 300
  assert "T_TRADE_OLD_INVENTORY_CAP" in result.size_reason_codes
  assert "LIQUIDITY_PARTICIPATION_CAP" in result.size_reason_codes
  assert "ALLOCATION_AMOUNT_CAP" not in result.size_reason_codes


@pytest.mark.parametrize(
  "cap", [True, False, -1, D("NaN"), float("nan"), D("Infinity"), float("inf"), "1000"]
)
def test_invalid_cap_is_rejected(cap):
  with pytest.raises(ValueError, match="ALLOCATION_AMOUNT_CAP_INVALID"):
    draft(cap=cap)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), True, "10"])
def test_cap_requires_finite_positive_price(price):
  with pytest.raises(ValueError, match="ALLOCATION_AMOUNT_CAP_PRICE_INVALID"):
    draft(cap=1000, price=price)


def test_allocation_is_buy_only_and_never_changes_sell_direction():
  value = intent()
  with pytest.raises(ValueError, match="ALLOCATION_AMOUNT_CAP_REQUIRES_BUY"):
    OrderSizer().draft_intent(value, OrderType.SELL, 10, {}, allocated_amount_cap=1000)


@pytest.mark.asyncio
async def test_shared_risk_still_enforces_higher_market_minimum_after_cap():
  @dataclass
  class MarketWithMinimum(MarketDataSnapshot):
    min_limit_order_volume: int = 200

  value = intent()
  result = draft(value, cap=D("1005.01"))
  request = OrderRequest(
    instrument_code=value.instrument_code,
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=result.sized_volume,
    price=10,
    execution_ref=value.execution_ref,
    environment=ExecutionEnvironment.PAPER,
  )
  risk = await TradingRiskChecker().evaluate_order(
    request,
    account={"available_cash": 100000},
    position={},
    market_data=MarketWithMinimum(instrument_code=value.instrument_code, price=10),
  )
  assert result.sized_volume == 100
  assert not risk.allowed
  assert risk.reason_code == "BELOW_MIN_VOLUME"
