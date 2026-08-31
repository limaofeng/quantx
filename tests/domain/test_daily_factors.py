"""The factor formula contract is shared, finite-window and strictly causal."""

import numpy as np
import pandas as pd
import pytest
from quantx_domain.factors import (
  FACTOR_VERSION,
  calculate_factor_frame,
  condition_mask,
  normalize_conditions,
)


def bars(count=320):
  close = 30 + np.sin(np.arange(count) / 4) + np.arange(count) * 0.02
  return pd.DataFrame(
    {
      "time": pd.bdate_range("2024-01-01", periods=count),
      "close": close,
      "high": close + 0.5,
      "low": close - 0.5,
      "open": close - 0.1,
      "volume": 1000 + np.arange(count),
      "amount": (1000 + np.arange(count)) * close * 100,
    }
  )


def test_prefix_and_finite_warmup_invariance():
  frame = bars()
  all_values = calculate_factor_frame(frame)
  pd.testing.assert_frame_equal(
    all_values.iloc[:290], calculate_factor_frame(frame.iloc[:290])
  )
  # Enough local history is identical to loading the whole listing history.
  pd.testing.assert_series_equal(
    all_values.iloc[-1], calculate_factor_frame(frame.iloc[-260:]).iloc[-1]
  )


def test_physical_missing_session_matches_explicit_nan_without_future_rows():
  frame = bars(320)
  missing_index = 316
  sparse = frame.drop(index=missing_index)
  explicit = frame.copy()
  explicit.loc[missing_index, ["open", "close", "high", "low", "volume", "amount"]] = (
    np.nan
  )
  calculated = calculate_factor_frame(sparse, trading_dates=frame.time)
  pd.testing.assert_frame_equal(
    calculated, calculate_factor_frame(explicit).drop(index=missing_index)
  )
  assert pd.isna(calculated.iloc[-1].ma5)
  assert pd.isna(calculated.iloc[-1].price_drop_pct)
  assert calculated.iloc[-1].current_price == pytest.approx(frame.close.iloc[-1])
  prefix = sparse.iloc[:-2]
  pd.testing.assert_frame_equal(
    calculated.iloc[:-2],
    calculate_factor_frame(prefix, trading_dates=frame.time),
  )


def test_nullable_quality_flags_conservatively_reject_missing_calendar_rows():
  frame = bars(40)
  for name, value in (
    ("suspend_flag", 0),
    ("adjustment_valid", 1),
    ("listing_valid", 1),
  ):
    frame[name] = pd.Series(value, index=frame.index, dtype="Int64")
  calculated = calculate_factor_frame(frame.drop(index=37), trading_dates=frame.time)
  assert pd.isna(calculated.iloc[-1].ma5)
  assert calculated.iloc[-1].current_price == pytest.approx(frame.close.iloc[-1])
  frame.loc[39, "suspend_flag"] = pd.NA
  assert pd.isna(calculate_factor_frame(frame).iloc[-1].current_price)


def test_true_falling_streak_and_zero_rsi_are_preserved():
  frame = bars(30)
  frame["close"] = np.arange(40, 10, -1, dtype=float)
  frame["open"], frame["high"], frame["low"] = (
    frame.close,
    frame.close + 0.5,
    frame.close - 0.5,
  )
  falling = calculate_factor_frame(frame).iloc[-1]
  assert falling.consecutive_down_days == 20
  assert falling.consecutive_down_pct < 0
  assert falling.rsi12 == 0
  frame["close"] = np.arange(10, 40, dtype=float)
  frame["open"], frame["high"], frame["low"] = (
    frame.close,
    frame.close + 0.5,
    frame.close - 0.5,
  )
  rising = calculate_factor_frame(frame).iloc[-1]
  assert rising.consecutive_down_days == 0
  assert rising.consecutive_down_pct == 0
  frame.loc[29, "close"] = frame.loc[28, "close"]
  frame.loc[29, ["open", "high", "low"]] = [38, 38.5, 37.5]
  assert calculate_factor_frame(frame).iloc[-1].consecutive_down_days == 0


def test_missing_history_denominators_and_bad_values_do_not_fabricate_factors():
  frame = bars(40)
  frame["volume"] = 0
  frame["amount"] = np.nan
  value = calculate_factor_frame(frame).iloc[-1]
  assert pd.isna(value.volume_ratio)
  assert pd.isna(value.amount_ratio_20)
  assert pd.isna(value.volume_percentile_60)
  assert pd.isna(value.price_drop_pct)
  assert pd.isna(value.kdj_cross_up)
  frame.loc[39, "close"] = np.inf
  assert pd.isna(calculate_factor_frame(frame).iloc[-1].current_price)


def test_flat_price_rsi_bollinger_are_neutral():
  frame = bars()
  frame[["close", "high", "low", "open"]] = 10.0
  value = calculate_factor_frame(frame).iloc[-1]
  assert value.rsi12 == 50
  assert value.boll_percent_b == 0.5
  assert value.boll_bandwidth == 0


def test_condition_identity_zeros_boundaries_and_strict_errors():
  low = {"factor_id": "rsi12", "operator": "lte", "value": 0}
  high = {"factor_id": "volume_ratio", "operator": "between", "value": 1, "value_to": 2}
  assert normalize_conditions([low, high, low]) == normalize_conditions([high, low])
  frame = pd.DataFrame({"rsi12": [0, 1, np.nan], "volume_ratio": [1, 2, 1]})
  assert condition_mask(frame, [low, high]).tolist() == [True, False, False]
  for invalid in (
    {**low, "factor_id": "unknown"},
    {**low, "value": np.inf},
    {**low, "operator": "oops"},
    {**high, "value_to": 0},
    {"factor_id": "kdj_cross_up", "operator": "eq", "value": 2},
  ):
    with pytest.raises(ValueError):
      normalize_conditions([invalid])


def test_price_units_and_snapshot_parity():
  from quantx_infrastructure.services.daily_indicator_snapshot_service import (
    build_snapshot_record,
  )

  frame = bars()
  frame["raw_close"] = frame.close / 2
  calculated = calculate_factor_frame(frame).iloc[-1]
  snapshot = build_snapshot_record(
    "000001.SZ", "stock", "示例", frame.time.iloc[-1].date(), frame
  )
  assert snapshot["calculation_version"] == FACTOR_VERSION
  for key, value in calculated.items():
    assert snapshot[key] == pytest.approx(value)
  assert snapshot["current_price"] == pytest.approx(frame.raw_close.iloc[-1])


def test_first_observation_keeps_price_without_inventing_history():
  from quantx_infrastructure.services.daily_indicator_snapshot_service import (
    build_snapshot_record,
  )

  frame = bars(1)
  snapshot = build_snapshot_record(
    "000001.SZ", "stock", "示例", frame.time.iloc[0].date(), frame
  )
  assert snapshot is not None
  assert snapshot["current_price"] == frame.close.iloc[0]
  assert snapshot["change_pct"] is None
  assert snapshot["volume_ratio"] is None
  assert snapshot["ma5"] is None
  assert snapshot["price_drop_pct"] is None
