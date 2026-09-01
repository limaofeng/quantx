from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from quantx_research.next_day_selection_metrics import (
  evaluate_probability,
  evaluate_ranking,
)
from quantx_research.next_day_selection_training import (
  _model_choice,
  build_next_day_labels,
  walk_forward_folds,
)


def test_label_uses_exact_next_session_and_never_skips_forward() -> None:
  calendar = pd.DatetimeIndex(["2026-08-28", "2026-08-31", "2026-09-01"])
  panel = pd.DataFrame(
    {
      "stock_code": ["000001.SZ", "000001.SZ", "600000.SH", "600000.SH"],
      "event_date": ["2026-08-28", "2026-09-01", "2026-08-28", "2026-08-31"],
      "open": [10.0, 12.0, 10.0, 10.0],
      "close": [10.0, 13.0, 10.0, 11.0],
      "outcome_valid": [True, True, True, True],
    }
  )

  labeled = build_next_day_labels(panel, calendar).set_index(
    ["stock_code", "event_date"]
  )

  missing_t1 = labeled.loc[("000001.SZ", pd.Timestamp("2026-08-28"))]
  valid_t1 = labeled.loc[("600000.SH", pd.Timestamp("2026-08-28"))]
  assert pd.isna(missing_t1["label"])
  assert valid_t1["target_date"] == pd.Timestamp("2026-08-31")
  assert valid_t1["label"] == 1.0
  assert valid_t1["next_open_to_close_return"] == pytest.approx(0.1)


def test_walk_forward_uses_30_train_6_calibration_1_validation() -> None:
  months = list(pd.period_range("2022-01", periods=48, freq="M"))

  folds = walk_forward_folds(
    months,
    minimum_training_months=30,
    calibration_months=6,
  )

  assert len(folds) == 12
  assert len(folds[0].train_months) == 30
  assert len(folds[0].calibration_months) == 6
  assert folds[0].validation_month == months[36]
  assert folds[-1].validation_month == months[-1]


def test_model_choice_prefers_logistic_within_half_percent() -> None:
  assert _model_choice(0.2009, 0.2) == "LOGISTIC"
  assert _model_choice(0.202, 0.2) == "LIGHTGBM"


def test_probability_and_date_equal_ranking_metrics_are_reported() -> None:
  rows = []
  for day in pd.date_range("2026-01-05", periods=8, freq="B"):
    for index in range(60):
      label = int(index < 20)
      rows.append(
        {
          "event_date": day,
          "stock_code": f"{index:06d}.SZ",
          "label": label,
          "next_open_to_close_return": 0.01 if label else -0.005,
          "probability": 0.9 - index / 100.0,
        }
      )
  panel = pd.DataFrame(rows)
  labels = panel["label"].to_numpy(dtype=float)
  probabilities = np.clip(panel["probability"].to_numpy(), 0.01, 0.99)

  probability = evaluate_probability(labels, probabilities, bins=10)
  ranking = evaluate_ranking(panel, bootstrap_samples=100, seed=7)

  assert set(["brier", "brier_skill", "log_loss", "ece", "roc_auc", "pr_auc"]).issubset(
    probability
  )
  assert ranking["top_20"]["precision"] == 1.0
  assert ranking["top_20"]["up_rate_lift_ci_low"] > 0
