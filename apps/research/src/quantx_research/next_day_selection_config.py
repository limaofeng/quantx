"""Strict configuration for the manual next-day probability training run."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from quantx_research.core.config import RuntimeConfig, _StrictModel


class SelectionDataConfig(_StrictModel):
  date_range: tuple[date, date]
  market_data_archive: Path | None = None
  verified_panel_path: Path | None = None
  benchmark_code: str = "000300.SH"
  stock_codes: tuple[str, ...] | None = None
  minimum_listing_days: int = Field(default=252, ge=252)
  historical_st_membership_path: Path | None = None
  historical_industry_membership_path: Path | None = None
  historical_delisting_status_path: Path | None = None

  @model_validator(mode="after")
  def validate_sources(self) -> "SelectionDataConfig":
    if self.date_range[1] < self.date_range[0]:
      raise ValueError("date_range end must not precede start")
    if self.market_data_archive is not None and self.verified_panel_path is not None:
      raise ValueError("market_data_archive 与 verified_panel_path 只能设置一个")
    return self

  @property
  def historical_universe_complete(self) -> bool:
    return all(
      path is not None
      for path in (
        self.historical_st_membership_path,
        self.historical_industry_membership_path,
        self.historical_delisting_status_path,
      )
    )


class WalkForwardConfig(_StrictModel):
  frozen_test_months: Literal[12] = 12
  minimum_training_months: Literal[30] = 30
  calibration_months: Literal[6] = 6
  validation_months: Literal[1] = 1


class LogisticGridConfig(_StrictModel):
  c_values: tuple[Literal[0.1, 1.0, 10.0], ...] = (0.1, 1.0, 10.0)
  max_iter: Literal[1000] = 1000


class LightGbmGridConfig(_StrictModel):
  num_leaves: tuple[Literal[15, 31], ...] = (15, 31)
  reg_lambda: tuple[Literal[1.0, 5.0], ...] = (1.0, 5.0)
  learning_rate: Literal[0.03] = 0.03
  n_estimators: Literal[500] = 500
  min_child_samples: Literal[100] = 100
  subsample: Literal[0.8] = 0.8
  colsample_bytree: Literal[0.8] = 0.8


class CalibrationConfig(_StrictModel):
  bins: int = Field(default=10, ge=5, le=50)
  isotonic_minimum_positives: Literal[20_000] = 20_000
  isotonic_minimum_relative_brier_improvement: Literal[0.01] = 0.01


class EvaluationConfig(_StrictModel):
  bootstrap_samples: int = Field(default=2000, ge=100, le=20_000)


class CandidateGateConfig(_StrictModel):
  brier_skill_minimum: Literal[0.0] = 0.0
  ece_maximum: Literal[0.03] = 0.03
  minimum_probability: Literal[0.6] = 0.6
  minimum_factor_completeness: Literal[0.9] = 0.9
  minimum_valid_history: Literal[252] = 252
  level_a_size: Literal[20] = 20
  level_b_size: Literal[30] = 30


class NextDaySelectionConfig(_StrictModel):
  study: Literal["next-day-selection"] = "next-day-selection"
  version: Literal["v1"] = "v1"
  random_seed: int = 20260901
  data: SelectionDataConfig
  walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
  logistic: LogisticGridConfig = Field(default_factory=LogisticGridConfig)
  lightgbm: LightGbmGridConfig = Field(default_factory=LightGbmGridConfig)
  calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
  evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
  candidate_gate: CandidateGateConfig = Field(default_factory=CandidateGateConfig)
  runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

  @property
  def study_id(self) -> str:
    return self.study


def load_next_day_selection_config(path: str | Path) -> NextDaySelectionConfig:
  payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
  if not isinstance(payload, dict):
    raise ValueError("模型训练配置根节点必须是 YAML mapping")
  return NextDaySelectionConfig.model_validate(payload)
