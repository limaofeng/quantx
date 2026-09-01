from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
from quantx_domain.selection_factors import SELECTION_FACTOR_DEFINITIONS
from quantx_worker.prefector.flows.stock_probability_inference_flow import (
  _factor_snapshot,
  _prediction_rows,
)


def test_factor_snapshot_uses_only_current_eligible_ordinary_a_shares() -> None:
  indicators = pd.DataFrame(
    {
      "stock_code": ["600000.SH", "000001.SZ", "510300.SH", "600001.SH"],
      "snapshot_date": [date(2026, 9, 1)] * 4,
      "name": ["浦发银行", "平安银行", "沪深300ETF", "ST示例"],
      "instrument_type": ["stock", "stock", "etf", "stock"],
      "volume": [1000.0] * 4,
      "amount": [10000.0] * 4,
      "open_date": [date(2000, 1, 1)] * 4,
      "expire_date": [None] * 4,
      "instrument_status": [0] * 4,
      "valid_history": [1000] * 4,
      "current_price": [10.0, 20.0, 30.0, 40.0],
      "change_pct": [1.0, 2.0, 99.0, 100.0],
    }
  )
  for definition in SELECTION_FACTOR_DEFINITIONS:
    for source in definition.source_indicators:
      if source not in indicators:
        indicators[source] = 1.0

  factors, metadata = _factor_snapshot(
    indicators,
    as_of_date=date(2026, 9, 1),
    minimum_valid_history=252,
    minimum_factor_completeness=0.9,
  )

  assert metadata["stock_code"].tolist() == ["600000.SH", "000001.SZ"]
  assert len(factors) == 2
  assert factors["change_pct__rank"].tolist() == [0.5, 1.0]


def test_low_completeness_outlier_is_removed_before_cross_sectional_ranking() -> None:
  indicators = pd.DataFrame(
    {
      "stock_code": ["600000.SH", "000001.SZ", "600001.SH"],
      "snapshot_date": [date(2026, 9, 1)] * 3,
      "name": ["浦发银行", "平安银行", "示例股份"],
      "instrument_type": ["stock"] * 3,
      "volume": [1000.0] * 3,
      "amount": [10000.0] * 3,
      "open_date": [date(2000, 1, 1)] * 3,
      "expire_date": [None] * 3,
      "instrument_status": [0] * 3,
      "valid_history": [1000] * 3,
      "current_price": [10.0] * 3,
      "change_pct": [1.0, 2.0, 100.0],
    }
  )
  for definition in SELECTION_FACTOR_DEFINITIONS:
    for source in definition.source_indicators:
      if source not in indicators:
        indicators[source] = 1.0
  for definition in SELECTION_FACTOR_DEFINITIONS[:8]:
    indicators.loc[2, definition.source_indicators[0]] = np.nan

  factors, metadata = _factor_snapshot(
    indicators,
    as_of_date=date(2026, 9, 1),
    minimum_valid_history=252,
    minimum_factor_completeness=0.9,
  )

  assert metadata["stock_code"].tolist() == ["600000.SH", "000001.SZ"]
  assert factors["change_pct__rank"].tolist() == [0.5, 1.0]


def test_candidate_ranks_are_not_backfilled_when_a_top_name_fails_confidence() -> None:
  count = 52
  metadata = pd.DataFrame(
    {
      "stock_code": [f"{600000 + index:06d}.SH" for index in range(count)],
      "is_ordinary_stock": [True] * count,
      "is_st": [False] * count,
      "is_suspended": [False] * count,
      "has_delisting_risk": [False] * count,
      "valid_history": [1000] * count,
      "factor_completeness": [0.98] * count,
    }
  )
  selected = np.linspace(0.9, 0.65, count)
  logistic = selected.copy()
  lightgbm = selected.copy()
  logistic[0] = 0.99
  lightgbm[0] = 0.01
  predicted = {
    "selected": selected,
    "raw": np.linspace(2.0, 0.5, count),
    "logistic": logistic,
    "lightgbm": lightgbm,
    "ood_fit": np.full(count, 0.95),
  }
  bundle = SimpleNamespace(
    metrics={
      "frozen_test": {
        "probability": {
          "calibration_bins": [
            {
              "index": 0,
              "lower": 0.0,
              "upper": 1.0,
              "sample_count": 2000,
              "realized_rate": 0.62,
            }
          ]
        }
      }
    }
  )
  rule = SimpleNamespace(
    minimum_valid_history=252,
    minimum_factor_completeness=0.9,
    minimum_probability=0.6,
    minimum_confidence=0.6,
    minimum_ood_fit=0.8,
    level_a_size=20,
    level_b_size=30,
  )

  rows = _prediction_rows(metadata, predicted, bundle, rule)

  assert rows[0]["rank"] == 1
  assert rows[0]["candidate_level"] is None
  assert "CONFIDENCE_BELOW_0_60" in rows[0]["reason_codes"]
  assert rows[1]["rank"] == 2
  assert rows[1]["candidate_level"] == "A"
  assert rows[20]["candidate_level"] == "B"
  assert rows[50]["candidate_level"] is None
