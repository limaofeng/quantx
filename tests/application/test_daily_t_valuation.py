from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import (
  TDailyFill,
  TOpeningPosition,
  TValuationMark,
  value_daily_t_positions,
)

NOW = datetime(2026, 9, 7, 2, tzinfo=UTC)
PRIOR = date(2026, 9, 4)


def fill(index, side, volume, price, fee="0"):
  return TDailyFill(
    f"fill-{index}",
    "batch",
    "600000.SH",
    side,
    volume,
    D(price),
    D(fee),
    NOW - timedelta(seconds=10 - index),
    index,
    0,
  )


def value(fills, *, opening=(), old_marks=None, marks=None):
  return value_daily_t_positions(
    as_of=NOW,
    previous_trading_day=PRIOR,
    opening_positions=opening,
    opening_marks=old_marks or {},
    current_marks=marks or {},
    fills=tuple(fills),
    mark_max_age_seconds=5,
  )


def test_daily_cost_is_rebased_so_prior_day_gains_are_not_today_profit():
  old = TValuationMark(
    "600000.SH", D(10), datetime(2026, 9, 4, 7, tzinfo=UTC), "close-1"
  )
  current = TValuationMark("600000.SH", D(10), NOW, "quote-current")
  result = value(
    [fill(1, "BUY", 100, "9", "5"), fill(2, "SELL", 50, "11", "5")],
    opening=(TOpeningPosition("batch", "600000.SH", 100),),
    old_marks={"600000.SH": old},
    marks={"600000.SH": current},
  )
  assert result.realized == D("68.75")
  assert result.unrealized == D("71.25")
  assert (
    result.realized + result.unrealized
    == result.cash_flow + result.closing_value - result.opening_value
    == D(140)
  )
  assert result.remaining_quantities == (("batch", 150),)


def test_partial_exit_then_buy_uses_cost_at_each_fill_not_later_average():
  result = value(
    [
      fill(1, "BUY", 100, "10", "5"),
      fill(2, "SELL", 40, "11", "2"),
      fill(3, "BUY", 40, "9", "1"),
      fill(4, "SELL", 100, "10", "5"),
    ]
  )
  assert result.realized == D(67) and result.unrealized == 0
  assert result.cash_flow == D(67) and result.remaining_quantities == (("batch", 0),)


@pytest.mark.parametrize(
  "problem,reason",
  [
    ("missing", "CURRENT_MARK_REQUIRED"),
    ("future", "FUTURE_MARK"),
    ("stale", "MARK_STALE"),
    ("fill_future", "FILL_TIME_INVALID"),
    ("overfill", "EXIT_OVERFILL"),
    ("duplicate", "FILL_INVALID"),
  ],
)
def test_uncertain_valuation_cannot_open_loss_headroom(problem, reason):
  fills = [fill(1, "BUY", 100, "10")]
  mark = TValuationMark("600000.SH", D(10), NOW, "quote")
  if problem == "future":
    mark = replace(mark, as_of=NOW + timedelta(microseconds=1))
  elif problem == "stale":
    mark = replace(mark, as_of=NOW - timedelta(seconds=6))
  elif problem == "fill_future":
    fills = [replace(fills[0], occurred_at=NOW + timedelta(microseconds=1))]
  elif problem == "overfill":
    fills.append(fill(2, "SELL", 101, "10"))
  elif problem == "duplicate":
    fills.append(fills[0])
  with pytest.raises(ValueError, match=reason):
    value(fills, marks={} if problem == "missing" else {"600000.SH": mark})


def test_overnight_exposure_requires_explicit_correct_previous_day_mark():
  with pytest.raises(ValueError, match="OPENING_MARK_REQUIRED"):
    value([], opening=(TOpeningPosition("batch", "600000.SH", 100),))
