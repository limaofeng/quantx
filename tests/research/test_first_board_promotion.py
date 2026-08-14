from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from quantx_research.studies.first_board_promotion import (
  DEFAULT_FEATURE_COLUMNS,
  FirstBoardPromotionStudy,
  FirstBoardResearchConfig,
)


def _panel(days: int = 14, rows_per_segment: int = 3) -> pd.DataFrame:
  rows: list[dict[str, object]] = []
  start = datetime(2026, 1, 5, 10, 0)
  for day in range(days):
    signal_at = start + timedelta(days=day)
    for segment_index, segment in enumerate(("MAIN", "GROWTH")):
      for stock in range(rows_per_segment):
        strong = (day + stock + segment_index) % 3 != 0
        row: dict[str, object] = {
          "instrument_code": f"{segment_index}{stock:05d}.SZ",
          "trade_date": signal_at.date(),
          "segment": segment,
          "signal_at": signal_at,
          "feature_as_of": signal_at - timedelta(seconds=1),
          "outcome_at": signal_at + timedelta(days=1, hours=5),
          "eligible": True,
          "first_board_close": int(strong),
          "next_day_limit_touch": int(strong),
          "next_day_limit_seal": int(strong and stock % 2 == 0),
          "net_return_pct": 3.0 if strong else -1.0,
          "v1_net_return_pct": 0.5,
          "all_near_limit_net_return_pct": 0.25,
          "historical_rules_complete": True,
        }
        for feature_index, feature in enumerate(DEFAULT_FEATURE_COLUMNS):
          row[feature] = float(strong) + feature_index * 0.01 + day * 0.001
        rows.append(row)
  return pd.DataFrame(rows)


def _config() -> FirstBoardResearchConfig:
  return FirstBoardResearchConfig(
    min_train_trading_days=5,
    refit_interval_days=2,
    logistic_iterations=80,
    bootstrap_samples=200,
    required_shadow_days=2,
    required_samples_per_segment=2,
  )


def test_walk_forward_predictions_never_train_on_current_or_future_date() -> None:
  result = FirstBoardPromotionStudy(_config()).run(_panel())

  assert not result.predictions.empty
  assert (
    result.predictions["model_train_end_date"] < result.predictions["trade_date"]
  ).all()
  assert set(result.predictions["segment"]) == {"MAIN", "GROWTH"}
  assert {
    "first_board_close_probability",
    "next_day_limit_touch_probability",
    "next_day_limit_seal_probability",
    "expected_net_return_pct",
    "predicted_cvar95_loss_pct",
  } <= set(result.predictions)


def test_future_feature_timestamp_is_rejected() -> None:
  panel = _panel()
  panel.loc[0, "feature_as_of"] = panel.loc[0, "signal_at"] + timedelta(seconds=1)

  with pytest.raises(ValueError, match="future data detected"):
    FirstBoardPromotionStudy(_config()).run(panel)


def test_same_day_outcome_is_rejected_for_t_plus_one_contract() -> None:
  panel = _panel()
  panel.loc[0, "outcome_at"] = panel.loc[0, "signal_at"] + timedelta(hours=1)

  with pytest.raises(ValueError, match=r"T\+1 outcome"):
    FirstBoardPromotionStudy(_config()).run(panel)


def test_incomplete_historical_rules_block_release_claim() -> None:
  panel = _panel()
  panel["historical_rules_complete"] = True
  panel.loc[0, "historical_rules_complete"] = False

  result = FirstBoardPromotionStudy(_config()).run(panel)

  assert result.historical_rules_complete is False
  assert result.release_ready_for_paper is False
  assert any("不得宣称" in warning for warning in result.warnings)


def test_bootstrap_comparisons_and_release_evidence_are_deterministic() -> None:
  study = FirstBoardPromotionStudy(_config())
  first = study.run(_panel())
  second = study.run(_panel())

  assert first.comparisons == second.comparisons
  assert {item.baseline for item in first.comparisons} == {
    "V1_RADAR",
    "ALL_NEAR_LIMIT",
  }
  evidence = first.release_evidence()
  assert evidence["stage"] == "SHADOW"
  assert evidence["model_version"] == _config().model_version
  assert np.isfinite(float(evidence["bootstrap_ci_lower_pct"]))
