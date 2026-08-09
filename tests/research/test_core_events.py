from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quantx_research.core import (
  StudyConfig,
  add_forward_outcomes,
  apply_event_cooldown,
  build_volume_shock_events,
  normalize_market_panel,
)


def _config(**overrides) -> StudyConfig:
  payload = {
    "universe": {"lookback_years": 5, "minimum_listing_days": 0},
    "outcomes": {
      "horizons": [1],
      "include_close_response": True,
      "include_next_open_return": False,
      "include_benchmark_excess": False,
      "include_cross_section_excess": False,
    },
    "statistics": {
      "bootstrap_samples": 100,
      "minimum_cell_samples": 2,
      "run_regression": False,
    },
    "quality": {
      "minimum_history_rows": 252,
      "minimum_total_events": 1,
    },
  }
  payload.update(overrides)
  return StudyConfig.model_validate(payload)


def test_universe_stock_codes_are_normalized_and_validated() -> None:
  config = _config(
    universe={
      "lookback_years": 5,
      "minimum_listing_days": 0,
      "stock_codes": ["000001.sz", "000001.SZ", "600519.sh"],
    }
  )

  assert config.universe.stock_codes == ("000001.SZ", "600519.SH")
  with pytest.raises(ValueError, match="invalid A-share stock_codes"):
    _config(
      universe={
        "lookback_years": 5,
        "minimum_listing_days": 0,
        "stock_codes": ["AAPL"],
      }
    )


def test_empty_panel_returns_an_empty_event_contract() -> None:
  empty_panel = pd.DataFrame(
    columns=["stock_code", "time", "open", "high", "low", "close", "volume"]
  )

  events = build_volume_shock_events(empty_panel, _config())

  assert events.empty
  assert {
    "event_date",
    "relative_volume",
    "price_position",
    "_session_ordinal",
  }.issubset(events.columns)


def test_volume_features_exclude_event_day_from_baselines_and_position() -> None:
  dates = pd.bdate_range("2023-01-02", periods=275)
  close = np.linspace(10.0, 20.0, len(dates))
  high = close + 0.5
  low = close - 0.5
  volume = np.full(len(dates), 100.0)
  event_index = 252
  volume[event_index] = 200.0
  # These extreme T values must not enter the T-1 price-position window.
  high[event_index] = 1_000.0
  low[event_index] = 0.01
  close[event_index] = 25.0
  panel = pd.DataFrame(
    {
      "stock_code": "000001.SZ",
      "time": dates,
      "open": close * 0.999,
      "high": high,
      "low": low,
      "close": close,
      "volume": volume,
      "amount": volume * close * 100,
      "suspend_flag": 0,
      "adjustment_valid": True,
    }
  )

  events = build_volume_shock_events(panel, _config(), cooldown_days=0)

  event = events.loc[events["event_date"] == dates[event_index]].iloc[0]
  prior_slice = slice(event_index - 252, event_index)
  expected_position = (close[event_index - 1] - low[prior_slice].min()) / (
    high[prior_slice].max() - low[prior_slice].min()
  )
  assert event["relative_volume"] == pytest.approx(2.0)
  assert event["price_position"] == pytest.approx(expected_position)
  assert event["event_direction"] == "up"
  assert bool(event["is_volume_breakout"])


def test_forward_outcomes_use_market_sessions_and_do_not_skip_missing_bar() -> None:
  dates = pd.bdate_range("2026-01-05", periods=4)
  panel = pd.DataFrame(
    [
      {
        "stock_code": code,
        "time": day,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 100,
      }
      for code, prices in (
        ("000001.SZ", (10.0, None, 12.0, 13.0)),
        ("000002.SZ", (20.0, 21.0, 22.0, 23.0)),
      )
      for day, price in zip(dates, prices)
      if price is not None
    ]
  )
  normalized = normalize_market_panel(panel)

  outcomes = add_forward_outcomes(
    normalized,
    (1, 2),
    include_next_open_return=False,
    include_benchmark_excess=False,
    include_cross_section_excess=False,
  )
  first = outcomes[
    (outcomes["stock_code"] == "000001.SZ") & (outcomes["event_date"] == dates[0])
  ].iloc[0]
  assert np.isnan(first["close_return_h1"])
  assert first["close_return_h2"] == pytest.approx(0.2)
  assert np.isnan(first["mfe_close_h2"])
  assert np.isnan(first["mae_close_h2"])


def test_cooldown_zero_preserves_candidates_and_positive_value_uses_market_days() -> (
  None
):
  dates = pd.bdate_range("2026-01-01", periods=30)
  candidates = pd.DataFrame(
    {
      "stock_code": ["000001.SZ"] * 5,
      "event_date": dates[[1, 5, 12, 13, 23]],
      "_session_ordinal": [1, 5, 12, 13, 23],
    }
  )

  assert len(apply_event_cooldown(candidates, 0)) == 5
  cooled = apply_event_cooldown(candidates, 10)
  assert cooled["_session_ordinal"].tolist() == [1, 12, 23]


def test_default_yaml_shape_and_optional_date_range_are_json_serializable() -> None:
  config = StudyConfig.model_validate(
    {
      "study": "volume-shock",
      "universe": {
        "instrument_type": "stock",
        "lookback_years": 5,
        "end_date": "latest",
      },
      "event": {
        "relative_volume_window": 20,
        "relative_volume_bins": [0, 1, 1.5, 2, 3],
      },
    }
  )

  dumped = config.model_dump(mode="json")
  assert dumped["universe"]["end_date"] == "latest"
  assert dumped["event"]["relative_volume_window"] == 20
  assert dumped["event"]["normal_relative_volume_min"] == 0.8
  assert dumped["event"]["normal_relative_volume_max"] == 1.2
  assert dumped["statistics"]["bootstrap_method"] == "moving_block"
  assert dumped["statistics"]["moving_block_length"] == "horizon"
  assert dumped["statistics"]["minimum_inference_dates"] == 30
  assert config.statistics.block_length(20) == 20
  assert dumped["date_range"] is None

  with pytest.raises(ValueError, match="minimum_inference_dates"):
    StudyConfig.model_validate({"statistics": {"minimum_inference_dates": 29}})
