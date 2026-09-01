from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  build_selection_factor_frame,
  factor_schema_manifest,
  selection_factor_completeness,
  selection_feature_columns,
)
from quantx_domain.selection_model import geometric_confidence


def test_selection_factors_are_transformed_within_each_date() -> None:
  panel = pd.DataFrame(
    {
      "snapshot_date": ["2026-08-31"] * 3 + ["2026-09-01"] * 3,
      "change_pct": [1.0, 2.0, 100.0, 1001.0, 1002.0, 1100.0],
      "kdj_cross_up": [0.0, 1.0, 2.0, 0.0, np.nan, 1.0],
      "current_price": [10.0] * 6,
      "ma5": [9.0, 10.0, 11.0, 9.0, 10.0, 11.0],
    }
  )

  factors = build_selection_factor_frame(panel)

  assert factors.loc[:2, "change_pct__z"].mean() == pytest.approx(0.0, abs=1e-10)
  assert factors.loc[3:, "change_pct__z"].mean() == pytest.approx(0.0, abs=1e-10)
  assert factors.loc[0, "change_pct__rank"] == pytest.approx(1 / 3)
  assert factors.loc[3, "change_pct__rank"] == pytest.approx(1 / 3)
  assert factors.loc[2, "kdj_cross_up__missing"] == 1.0
  assert math.isnan(factors.loc[2, "kdj_cross_up"])
  assert factors.loc[0, "ma5_distance__rank"] == pytest.approx(1 / 3)
  assert tuple(factors.columns) == selection_feature_columns()


def test_factor_schema_is_versioned_and_hash_verifiable() -> None:
  manifest = factor_schema_manifest()

  assert manifest["factor_set_version"] == FACTOR_SET_VERSION
  assert manifest["factor_set_hash"] == FACTOR_SET_HASH
  assert len(FACTOR_SET_HASH) == 64


def test_factor_completeness_is_available_before_cross_sectional_transforms() -> None:
  panel = pd.DataFrame(
    {
      "change_pct": [1.0, 100.0],
      "current_price": [10.0, 10.0],
      "ma5": [9.0, np.nan],
      "kdj_cross_up": [1.0, 2.0],
    }
  )

  completeness = selection_factor_completeness(panel)

  assert completeness.iloc[0] > completeness.iloc[1]
  assert completeness.between(0.0, 1.0).all()


def test_geometric_confidence_uses_all_four_evidence_components() -> None:
  confidence = geometric_confidence(
    factor_completeness=0.9,
    ood_fit=0.8,
    logistic_probability=0.65,
    lightgbm_probability=0.6,
    calibration_bin_samples=800,
  )

  assert confidence == pytest.approx((0.9 * 0.8 * 0.95 * 0.8) ** 0.25)
