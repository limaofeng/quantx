from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quantx_research.core import (
  DateBlockBootstrap,
  GroupStatistic,
  StudyConfig,
  apply_fdr,
  calculate_comparison_statistics,
  calculate_grouped_statistics,
  calculate_robustness_statistics,
)
from quantx_research.core.regression import run_panel_regressions
from quantx_research.studies import VolumeShockStudy
from quantx_research.studies.volume_shock import _study_warnings


def _group_stat(p_value: float, sample_size: int = 100) -> GroupStatistic:
  return GroupStatistic(
    dimensions={"cell": str(p_value)},
    return_kind="close_response",
    horizon=1,
    benchmark="absolute",
    sample_size=sample_size,
    mean=0.01,
    median=0.01,
    positive_rate=0.6,
    p05=-0.02,
    p25=-0.01,
    p75=0.02,
    p95=0.03,
    mae_mean=-0.02,
    mfe_mean=0.03,
    ci_low=0.005,
    ci_high=0.015,
    p_value=p_value,
  )


def test_study_warnings_disclose_current_master_survivorship_bias() -> None:
  warnings = _study_warnings(pd.DataFrame(), StudyConfig())

  assert any(
    "当前证券主表快照" in warning
    and "未完整重建历史上市、退市成分" in warning
    and "生存者偏差" in warning
    for warning in warnings
  )


def test_date_block_bootstrap_is_deterministic() -> None:
  frame = pd.DataFrame(
    {
      "event_date": pd.date_range("2026-01-01", periods=40).repeat(4),
      "value": np.linspace(-0.02, 0.04, 160),
    }
  )
  first = DateBlockBootstrap(
    frame["event_date"], samples=200, seed=42, confidence_level=0.95
  )
  second = DateBlockBootstrap(
    frame["event_date"], samples=200, seed=42, confidence_level=0.95
  )

  inference = first.infer(frame, "value")
  assert inference == second.infer(frame, "value")
  assert all(value is not None for value in inference)


def test_benjamini_hochberg_is_monotone_and_suppresses_small_cells() -> None:
  statistics = [
    _group_stat(0.01),
    _group_stat(0.04),
    _group_stat(0.03),
    _group_stat(0.001, sample_size=5),
  ]

  adjusted = apply_fdr(statistics, alpha=0.05, minimum_cell_samples=30)

  assert [item.q_value for item in adjusted[:3]] == pytest.approx([0.03, 0.04, 0.04])
  assert all(item.significant for item in adjusted[:3])
  assert adjusted[3].q_value is None
  assert adjusted[3].significant is None


def test_panel_regression_recovers_known_interaction_coefficient() -> None:
  rng = np.random.default_rng(20260729)
  event_dates = pd.date_range("2022-01-03", periods=50)
  stock_codes = [f"{index:06d}.SZ" for index in range(20)]
  frame = pd.DataFrame(
    [
      {"event_date": event_date, "stock_code": stock_code}
      for event_date in event_dates
      for stock_code in stock_codes
    ]
  )
  size = len(frame)
  frame["relative_volume"] = rng.choice([0.9, 1.1, 1.8, 2.5], size=size)
  frame["price_position"] = rng.uniform(0.0, 1.0, size)
  frame["momentum_20"] = rng.normal(0.0, 0.08, size)
  frame["volatility_20"] = rng.uniform(0.01, 0.05, size)
  frame["log_average_amount_20"] = rng.normal(18.0, 0.8, size)
  shock = (frame["relative_volume"] >= 1.5).astype(float)
  frame["is_primary_shock_event"] = shock.astype(bool)
  centered_position = frame["price_position"] - frame["price_position"].mean()
  frame["close_return_h1"] = (
    0.012 * shock
    - 0.008 * centered_position
    + 0.03 * shock * centered_position
    + 0.01 * (frame["momentum_20"] - frame["momentum_20"].mean())
    + rng.normal(0.0, 0.002, size)
  )
  config = StudyConfig.model_validate(
    {
      "outcomes": {
        "horizons": [1],
        "include_close_response": True,
        "include_next_open_return": False,
        "include_benchmark_excess": False,
        "include_cross_section_excess": False,
      },
      "statistics": {
        "bootstrap_samples": 100,
        "minimum_cell_samples": 30,
      },
    }
  )

  result = run_panel_regressions(frame, config)[0]
  coefficients = {item.term: item.estimate for item in result.coefficients}

  assert result.nobs == size
  assert coefficients["shock_position_interaction"] == pytest.approx(0.03, abs=0.004)
  interaction_coefficient = next(
    item for item in result.coefficients if item.term == "shock_position_interaction"
  )
  assert interaction_coefficient.q_value is not None
  assert interaction_coefficient.significant is True
  assert all(
    item.q_value is None
    for item in result.coefficients
    if item.term != "shock_position_interaction"
  )
  assert result.covariance == "two_way_cluster"

  frame["market_excess_close_h1"] = frame["close_return_h1"]
  frame["csi300_excess_close_h1"] = np.nan
  frame.loc[:9, "csi300_excess_close_h1"] = frame.loc[:9, "close_return_h1"]
  coverage_config = config.model_copy(
    update={
      "outcomes": config.outcomes.model_copy(
        update={
          "include_benchmark_excess": True,
          "include_cross_section_excess": True,
        }
      )
    }
  )

  coverage_result = run_panel_regressions(frame, coverage_config)[0]

  assert coverage_result.dependent_variable == "csi300_excess_close_h1"
  assert coverage_result.nobs == 10
  assert not coverage_result.coefficients


def test_comparison_uses_daily_equal_weight_spreads_and_high_low_interaction() -> None:
  dates = pd.bdate_range("2024-01-02", periods=40)
  levels = {
    "low": (0.03, 0.01),
    "mid": (0.04, 0.01),
    "high": (0.07, 0.01),
  }
  rows = []
  for event_date in dates:
    for position, (shock_return, normal_return) in levels.items():
      rows.extend(
        [
          {
            "event_date": event_date,
            "stock_code": f"{position}-shock",
            "price_position_bin": position,
            "relative_volume": 2.0,
            "is_primary_shock_event": True,
            "close_return_h5": shock_return,
          },
          {
            "event_date": event_date,
            "stock_code": f"{position}-normal",
            "price_position_bin": position,
            "relative_volume": 1.0,
            "is_primary_shock_event": False,
            "close_return_h5": normal_return,
          },
          {
            "event_date": event_date,
            "stock_code": f"{position}-cooldown-suppressed",
            "price_position_bin": position,
            "relative_volume": 4.0,
            "is_primary_shock_event": False,
            "close_return_h5": 10.0,
          },
        ]
      )
  config = StudyConfig.model_validate(
    {
      "outcomes": {
        "horizons": [5],
        "include_close_response": True,
        "include_next_open_return": False,
        "include_benchmark_excess": False,
        "include_cross_section_excess": False,
      },
      "statistics": {
        "bootstrap_samples": 100,
        "moving_block_length": 10,
        "minimum_cell_samples": 2,
        "minimum_inference_dates": 30,
        "run_regression": False,
      },
    }
  )

  statistics = calculate_comparison_statistics(pd.DataFrame(rows), config)
  by_dimensions = {
    (
      item.dimensions["comparison"],
      item.dimensions["price_position_bin"],
    ): item
    for item in statistics
  }

  low = by_dimensions[("shock_minus_normal", "low")]
  assert low.shock_sample_size == 40
  assert low.normal_sample_size == 40
  assert low.unique_dates == 40
  assert low.spread_mean == pytest.approx(0.02)
  assert low.spread_median == pytest.approx(0.02)
  assert low.ci_low is not None
  assert low.p_value is not None
  assert low.q_value is not None

  interaction = by_dimensions[("high_minus_low", "high_minus_low")]
  assert interaction.shock_mean == pytest.approx(0.04)
  assert interaction.normal_mean == pytest.approx(0.0)
  assert interaction.spread_mean == pytest.approx(0.04)
  assert interaction.spread_median == pytest.approx(0.04)
  assert interaction.unique_dates == 40


def test_comparison_and_grouped_inference_require_thirty_valid_dates() -> None:
  dates = pd.bdate_range("2024-01-02", periods=29)
  rows = [
    {
      "event_date": event_date,
      "stock_code": f"{position}-{cohort}",
      "price_position_bin": position,
      "rvol_bin": "[1.5,2)",
      "event_direction": "up",
      "relative_volume": 2.0 if cohort == "shock" else 1.0,
      "is_primary_shock_event": cohort == "shock",
      "close_return_h20": 0.03 if cohort == "shock" else 0.01,
    }
    for event_date in dates
    for position in ("low", "high")
    for cohort in ("shock", "normal")
  ]
  config = StudyConfig.model_validate(
    {
      "outcomes": {
        "horizons": [20],
        "include_close_response": True,
        "include_next_open_return": False,
        "include_benchmark_excess": False,
        "include_cross_section_excess": False,
      },
      "statistics": {
        "bootstrap_samples": 100,
        "moving_block_length": 25,
        "minimum_cell_samples": 2,
        "minimum_inference_dates": 30,
        "run_regression": False,
      },
    }
  )
  frame = pd.DataFrame(rows)

  comparisons = calculate_comparison_statistics(frame, config)
  assert all(item.unique_dates == 29 for item in comparisons)
  assert all(item.p_value is None for item in comparisons)
  assert all(item.q_value is None for item in comparisons)
  shock_events = frame[frame["relative_volume"] >= 1.5]
  grouped = calculate_grouped_statistics(shock_events, config)
  assert grouped
  assert all(item.unique_dates == 29 for item in grouped)
  assert all(item.p_value is None for item in grouped)


def test_amount_and_zscore_robustness_are_not_intersected_with_rvol_shocks() -> None:
  dates = pd.bdate_range("2024-01-02", periods=35)
  frame = pd.DataFrame(
    {
      "event_date": dates,
      "stock_code": [f"{index:06d}.SZ" for index in range(len(dates))],
      "price_position_bin": ["low"] * len(dates),
      "event_direction": ["up"] * len(dates),
      "relative_volume": [1.0] * len(dates),
      "relative_amount": [2.0] * len(dates),
      "log_volume_zscore": [3.0] * len(dates),
      "close_return_h1": [0.01] * len(dates),
    }
  )
  config = StudyConfig.model_validate(
    {
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
        "minimum_inference_dates": 30,
        "run_regression": False,
      },
    }
  )

  result = calculate_robustness_statistics(frame, config)

  assert {"relative_amount_shock", "log_volume_zscore"} <= result.keys()
  assert all(
    statistic.sample_size == len(frame)
    for statistics in result.values()
    for statistic in statistics
  )


def test_volume_shock_study_returns_structured_primary_and_robustness_results() -> None:
  dates = pd.bdate_range("2024-01-02", periods=325)
  rows: list[dict[str, object]] = []
  for stock_index in range(10):
    code = f"{stock_index:06d}.SZ"
    close = 10.0 + stock_index + np.arange(len(dates)) * 0.01
    volume = np.full(len(dates), 100.0)
    volume[[252, 270, 290]] = 300.0
    for index, event_date in enumerate(dates):
      rows.append(
        {
          "stock_code": code,
          "time": event_date,
          "open": close[index] * 0.999,
          "high": close[index] * 1.01,
          "low": close[index] * 0.99,
          "close": close[index],
          "volume": volume[index],
          "amount": volume[index] * close[index] * 100,
          "suspend_flag": 0,
          "adjustment_valid": True,
        }
      )
  config = StudyConfig.model_validate(
    {
      "universe": {"lookback_years": 5, "minimum_listing_days": 0},
      "outcomes": {
        "horizons": [1],
        "include_close_response": True,
        "include_next_open_return": True,
        "include_benchmark_excess": False,
        "include_cross_section_excess": True,
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
  )

  analysis_sample, events, result = VolumeShockStudy(config).run_with_analysis_sample(
    pd.DataFrame(rows)
  )

  assert len(events) == 30
  assert len(analysis_sample) > len(events)
  assert analysis_sample["is_normal_volume"].any()
  assert int(analysis_sample["is_primary_shock_event"].sum()) == len(events)
  assert (events["relative_volume"] >= config.event.relative_volume_threshold).all()
  assert events["is_primary_shock_event"].all()
  assert result.event_count == 30
  assert result.analysis_sample_count == len(analysis_sample)
  assert result.grouped_statistics
  assert result.event_curve
  assert {"relative_amount_shock", "cooldown_5d", "cooldown_20d"}.issubset(
    result.robustness
  )
  assert {"cooldown_5d", "cooldown_20d"} == set(result.comparison_sensitivity)
  assert any("event_direction" in warning for warning in result.warnings)
  dumped = result.model_dump(mode="json")
  assert dumped["study_id"] == "volume-shock"

  event_only = VolumeShockStudy(config).analyze(events)
  assert event_only.analysis_sample_count == 0
  assert event_only.comparison == []
  assert event_only.regressions == []
  assert event_only.robustness == {}
  assert any("未提供阈值前完整分析样本" in item for item in event_only.warnings)
