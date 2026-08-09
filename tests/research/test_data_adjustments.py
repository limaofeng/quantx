from datetime import datetime

import pandas as pd
import pytest
from quantx_research.data import (
  apply_dividend_adjustment,
  build_quality_report,
)


def _bars() -> pd.DataFrame:
  return pd.DataFrame(
    {
      "stock_code": ["000001.SZ"] * 3,
      "time": pd.date_range("2024-01-01", periods=3),
      "open": [10.0, 11.0, 12.0],
      "high": [11.0, 12.0, 13.0],
      "low": [9.0, 10.0, 11.0],
      "close": [10.0, 11.0, 12.0],
      "volume": [100.0, 110.0, 120.0],
      "amount": [1_000.0, 1_100.0, 1_200.0],
      "suspend_flag": [0, 0, 0],
    }
  )


def test_front_and_back_adjustment_match_qmt_dr_direction() -> None:
  bars = _bars()
  bars["close"] = [100.0, 50.0, 55.0]
  bars["open"] = bars["high"] = bars["low"] = bars["close"]
  factors = pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-01-02", "dr": 2.0}])

  front = apply_dividend_adjustment(bars, factors, mode="front")
  back = apply_dividend_adjustment(bars, factors, mode="back")

  # Front adjustment restates pre-event history on the post-event basis.
  assert front["close"].tolist() == [50.0, 50.0, 55.0]
  # Back adjustment retains the pre-event basis and scales event/post-event bars.
  assert back["close"].tolist() == [100.0, 100.0, 110.0]
  assert front["volume"].tolist() == bars["volume"].tolist()


def test_600519_cash_dividend_keeps_the_observed_ex_date_return() -> None:
  bars = pd.DataFrame(
    {
      "stock_code": ["600519.SH", "600519.SH"],
      "time": pd.to_datetime(["2020-06-23", "2020-06-24"]),
      "open": [1474.50, 1460.01],
      "high": [1474.50, 1460.01],
      "low": [1474.50, 1460.01],
      "close": [1474.50, 1460.01],
      "volume": [1.0, 1.0],
    }
  )
  factors = pd.DataFrame(
    [
      {
        "stock_code": "600519.SH",
        "time": "2020-06-24",
        "dr": 1.011677,
      }
    ]
  )

  front = apply_dividend_adjustment(bars, factors, mode="front")
  back = apply_dividend_adjustment(bars, factors, mode="back")
  expected_return = 1460.01 / 1457.48 - 1

  assert front.loc[0, "close"] == pytest.approx(1457.48, abs=0.002)
  assert front.loc[1, "close"] / front.loc[0, "close"] - 1 == (
    pytest.approx(expected_return, abs=2e-6)
  )
  assert back.loc[0, "close"] == 1474.50
  assert back.loc[1, "close"] / back.loc[0, "close"] - 1 == (
    pytest.approx(expected_return, abs=2e-6)
  )


def test_point_in_time_adjustment_ignores_future_factors() -> None:
  known = pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-01-02", "dr": 2.0}])
  with_future = pd.concat(
    [
      known,
      pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-02-01", "dr": 10.0}]),
    ],
    ignore_index=True,
  )

  baseline = apply_dividend_adjustment(
    _bars(),
    known,
    mode="point_in_time",
    as_of=datetime(2024, 1, 3),
  )
  future_present = apply_dividend_adjustment(
    _bars(),
    with_future,
    mode="point_in_time",
    as_of=datetime(2024, 1, 3),
  )

  pd.testing.assert_series_equal(baseline["close"], future_present["close"])
  baseline_position = (baseline.loc[2, "close"] - baseline["low"].min()) / (
    baseline["high"].max() - baseline["low"].min()
  )
  future_position = (future_present.loc[2, "close"] - future_present["low"].min()) / (
    future_present["high"].max() - future_present["low"].min()
  )
  assert baseline_position == future_position


def test_empty_factor_window_is_valid_and_keeps_raw_prices() -> None:
  result = apply_dividend_adjustment(
    _bars(),
    pd.DataFrame(columns=["stock_code", "time", "dr"]),
    mode="point_in_time",
    as_of=datetime(2024, 1, 3),
  )

  assert result["adjustment_valid"].all()
  assert result["close"].tolist() == _bars()["close"].tolist()


def test_front_adjustment_as_of_does_not_read_later_actions() -> None:
  known = pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-01-02", "dr": 2.0}])
  with_future = pd.concat(
    [
      known,
      pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-02-01", "dr": 10.0}]),
    ],
    ignore_index=True,
  )

  baseline = apply_dividend_adjustment(
    _bars(),
    known,
    mode="front",
    as_of=datetime(2024, 1, 3),
  )
  future_present = apply_dividend_adjustment(
    _bars(),
    with_future,
    mode="front",
    as_of=datetime(2024, 1, 3),
  )

  pd.testing.assert_series_equal(baseline["close"], future_present["close"])


def test_invalid_adjustment_factor_is_flagged_without_changing_prices() -> None:
  factors = pd.DataFrame([{"stock_code": "000001.SZ", "time": "2024-01-02", "dr": 0}])

  result = apply_dividend_adjustment(_bars(), factors)

  assert not result["adjustment_valid"].any()
  assert result["close"].tolist() == _bars()["close"].tolist()


def test_quality_report_keeps_duplicate_and_coverage_evidence() -> None:
  panel = pd.concat([_bars(), _bars().iloc[[1]]], ignore_index=True)
  panel["adjustment_valid"] = True
  panel.loc[0, "high"] = 8.0
  panel.loc[1, "volume"] = -1

  report = build_quality_report(
    panel,
    requested_codes=["000001.SZ", "000002.SZ"],
    requested_start=datetime(2024, 1, 1),
    requested_end=datetime(2024, 1, 3),
    metadata_codes=["000001.SZ"],
    minimum_observations=5,
  )

  assert report.loaded_codes == ("000001.SZ",)
  assert report.missing_codes == ("000002.SZ",)
  assert report.duplicate_rows == 2
  assert report.invalid_ohlc_rows == 1
  assert report.negative_volume_rows == 1
  assert report.missing_metadata_codes == ("000002.SZ",)
  assert report.insufficient_history_codes == ("000001.SZ", "000002.SZ")
  assert len(report.warnings) == 3


def test_nan_placeholder_cannot_satisfy_history_or_end_coverage() -> None:
  valid_dates = pd.bdate_range("2024-01-02", periods=28)
  requested_end = pd.Timestamp("2024-03-29")
  rows = [
    {
      "stock_code": "000001.SZ",
      "time": timestamp,
      "open": 10.0,
      "high": 11.0,
      "low": 9.0,
      "close": 10.5,
      "volume": 100.0,
      "suspend_flag": 0,
      "adjustment_valid": True,
    }
    for timestamp in valid_dates
  ]
  # A valid duplicate followed by the QMT NaN placeholder must not make the
  # final canonical stock-date valid.
  rows.extend(
    [
      {
        **rows[-1],
        "time": requested_end,
      },
      {
        "stock_code": "000001.SZ",
        "time": requested_end,
        "open": float("nan"),
        "high": float("nan"),
        "low": float("nan"),
        "close": float("nan"),
        "volume": float("nan"),
        "suspend_flag": 0,
        "adjustment_valid": True,
      },
    ]
  )

  report = build_quality_report(
    pd.DataFrame(rows),
    requested_codes=["000001.SZ"],
    requested_start=valid_dates[0],
    requested_end=requested_end,
    metadata_codes=["000001.SZ"],
    minimum_observations=29,
  )
  coverage = report.coverage[0]

  assert report.row_count == 30
  assert report.duplicate_rows == 2
  assert report.missing_price_rows == 1
  assert report.invalid_ohlc_rows == 1
  assert coverage.valid_rows == 28
  assert coverage.has_start_coverage is True
  assert coverage.has_end_coverage is False
  assert coverage.has_minimum_observations is False
  assert report.insufficient_history_codes == ("000001.SZ",)
