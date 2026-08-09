"""Serializable research result models."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReturnKind = Literal["close_response", "next_open"]
BenchmarkKind = Literal["absolute", "csi300", "market_equal_weight"]


class _ResultModel(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)


class EventRecord(_ResultModel):
  """Stable event-level fields.

  Forward outcomes are kept in a mapping because their keys depend on the
  configured horizons.  Batch calculations use a DataFrame directly; this
  model is primarily an interchange/documentation contract.
  """

  stock_code: str
  event_date: date
  relative_volume: float
  relative_amount: float | None = None
  log_volume_zscore: float | None = None
  price_position: float
  event_return: float
  event_direction: Literal["down", "flat", "up"]
  relative_volume_bin: str
  price_position_bin: str
  is_abnormal_volume: bool = True
  is_primary_shock_event: bool = True
  is_normal_volume: bool = False
  is_volume_breakout: bool = False
  is_high_position_stall: bool = False
  outcomes: dict[str, float | None] = Field(default_factory=dict)
  quality_flags: tuple[str, ...] = ()


class GroupStatistic(_ResultModel):
  dimensions: dict[str, str]
  return_kind: ReturnKind
  horizon: int
  benchmark: BenchmarkKind
  sample_size: int
  unique_dates: int = 0
  mean: float | None
  median: float | None
  positive_rate: float | None
  p05: float | None
  p25: float | None
  p75: float | None
  p95: float | None
  mae_mean: float | None
  mfe_mean: float | None
  ci_low: float | None
  ci_high: float | None
  p_value: float | None
  q_value: float | None = None
  significant: bool | None = None


class EventCurvePoint(_ResultModel):
  return_kind: ReturnKind
  horizon: int
  benchmark: BenchmarkKind
  sample_size: int
  unique_dates: int = 0
  mean: float | None
  median: float | None
  positive_rate: float | None
  ci_low: float | None
  ci_high: float | None


class ComparisonStatistic(_ResultModel):
  """Normal-volume comparison after date-level cross-sectional averaging."""

  dimensions: dict[str, str]
  return_kind: ReturnKind
  horizon: int
  benchmark: BenchmarkKind
  shock_sample_size: int
  normal_sample_size: int
  unique_dates: int
  shock_mean: float | None
  shock_median: float | None = None
  normal_mean: float | None
  normal_median: float | None = None
  spread_mean: float | None
  spread_median: float | None = None
  ci_low: float | None
  ci_high: float | None
  p_value: float | None
  q_value: float | None = None
  significant: bool | None = None


class RegressionCoefficient(_ResultModel):
  term: str
  estimate: float | None
  std_error: float | None
  t_stat: float | None
  p_value: float | None
  ci_low: float | None
  ci_high: float | None
  q_value: float | None = None
  significant: bool | None = None


class RegressionResult(_ResultModel):
  return_kind: ReturnKind
  horizon: int
  dependent_variable: str
  nobs: int
  r_squared: float | None
  coefficients: list[RegressionCoefficient] = Field(default_factory=list)
  covariance: Literal["two_way_cluster"] = "two_way_cluster"
  warnings: list[str] = Field(default_factory=list)


class StudyResult(_ResultModel):
  study_id: str
  version: str
  event_count: int
  analysis_sample_count: int = 0
  grouped_statistics: list[GroupStatistic] = Field(default_factory=list)
  event_curve: list[EventCurvePoint] = Field(default_factory=list)
  comparison: list[ComparisonStatistic] = Field(default_factory=list)
  comparison_sensitivity: dict[str, list[ComparisonStatistic]] = Field(
    default_factory=dict
  )
  regressions: list[RegressionResult] = Field(default_factory=list)
  robustness: dict[str, list[GroupStatistic]] = Field(default_factory=dict)
  warnings: list[str] = Field(default_factory=list)
