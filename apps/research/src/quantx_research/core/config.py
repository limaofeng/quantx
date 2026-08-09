"""Configuration models for reproducible offline research studies."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)


class UniverseConfig(_StrictModel):
  """Universe selection and research date policy."""

  instrument_type: str = "stock"
  stock_codes: tuple[str, ...] | None = None
  lookback_years: int = Field(default=5, ge=1, le=30)
  end_date: date | Literal["latest"] = "latest"
  benchmark_code: str = "000300.SH"
  minimum_listing_days: int = Field(default=120, ge=0)

  @field_validator("stock_codes")
  @classmethod
  def _validate_stock_codes(
    cls,
    value: tuple[str, ...] | None,
  ) -> tuple[str, ...] | None:
    if value is None:
      return None
    normalized = tuple(dict.fromkeys(str(code).strip().upper() for code in value))
    invalid = [
      code for code in normalized if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", code)
    ]
    if invalid:
      raise ValueError(f"invalid A-share stock_codes: {', '.join(invalid)}")
    if not normalized:
      raise ValueError("stock_codes cannot be empty")
    return normalized


class EventConfig(_StrictModel):
  """Volume-shock event definition."""

  relative_volume_window: int = Field(default=20, ge=2)
  relative_amount_window: int = Field(default=20, ge=2)
  log_volume_zscore_window: int = Field(default=60, ge=3)
  log_volume_zscore_threshold: float = Field(default=2.0, gt=0)
  relative_volume_threshold: float = Field(default=1.5, gt=0)
  normal_relative_volume_min: float = Field(default=0.8, gt=0)
  normal_relative_volume_max: float = Field(default=1.2, gt=0)
  relative_volume_bins: tuple[float, ...] = (0.0, 1.0, 1.5, 2.0, 3.0)
  cooldown_days: int = Field(default=10, ge=0)
  flat_return_threshold_pct: float = Field(default=1.0, ge=0, le=100)
  breakout_window: int = Field(default=20, ge=2)

  @field_validator("relative_volume_bins")
  @classmethod
  def _validate_volume_bins(cls, value: tuple[float, ...]) -> tuple[float, ...]:
    return _strictly_increasing_edges(value, minimum=0.0)

  @model_validator(mode="after")
  def _validate_normal_volume_range(self) -> "EventConfig":
    if self.normal_relative_volume_max <= self.normal_relative_volume_min:
      raise ValueError(
        "normal_relative_volume_max must exceed normal_relative_volume_min"
      )
    if self.normal_relative_volume_max > self.relative_volume_threshold:
      raise ValueError(
        "normal relative-volume range must not overlap the shock threshold"
      )
    return self


class ConditioningConfig(_StrictModel):
  """Variables used to condition event outcomes."""

  price_position_window: int = Field(default=252, ge=2)
  price_position_bins: tuple[float, ...] = (0.0, 0.3, 0.7, 1.0)

  @field_validator("price_position_bins")
  @classmethod
  def _validate_position_bins(cls, value: tuple[float, ...]) -> tuple[float, ...]:
    edges = _strictly_increasing_edges(value, minimum=0.0)
    if edges[0] != 0.0 or edges[-1] != 1.0:
      raise ValueError("price_position_bins must start at 0.0 and end at 1.0")
    return edges


class OutcomeConfig(_StrictModel):
  """Forward-return definitions."""

  horizons: tuple[int, ...] = (1, 3, 5, 10, 20)
  include_close_response: bool = True
  include_next_open_return: bool = True
  include_benchmark_excess: bool = True
  include_cross_section_excess: bool = True

  @field_validator("horizons")
  @classmethod
  def _validate_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
    normalized = tuple(sorted(set(value)))
    if not normalized or normalized[0] < 1:
      raise ValueError("horizons must contain positive trading-day counts")
    return normalized

  @model_validator(mode="after")
  def _require_an_outcome(self) -> "OutcomeConfig":
    if not self.include_close_response and not self.include_next_open_return:
      raise ValueError("at least one forward-return definition must be enabled")
    return self


class StatisticsConfig(_StrictModel):
  """Statistical inference settings."""

  bootstrap_method: Literal["moving_block"] = "moving_block"
  bootstrap_samples: int = Field(default=1000, ge=100)
  moving_block_length: int | Literal["horizon"] = "horizon"
  random_seed: int = 42
  confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
  minimum_cell_samples: int = Field(default=30, ge=2)
  minimum_inference_dates: int = Field(default=30, ge=30)
  fdr_alpha: float = Field(default=0.05, gt=0, lt=1)
  run_regression: bool = True
  # Kept as a constrained compatibility field for existing resolved configs.
  # The actual model preference is fixed as CSI300 -> market -> absolute and
  # never selected from observed model fit or outcome coverage.
  regression_benchmark: Literal["csi300"] = "csi300"

  @field_validator("moving_block_length")
  @classmethod
  def _validate_moving_block_length(
    cls,
    value: int | Literal["horizon"],
  ) -> int | Literal["horizon"]:
    if value != "horizon" and value < 1:
      raise ValueError("moving_block_length must be positive or 'horizon'")
    return value

  def block_length(self, horizon: int) -> int:
    """Return the preregistered block length, never shorter than the outcome."""
    configured = (
      horizon
      if self.moving_block_length == "horizon"
      else int(self.moving_block_length)
    )
    return max(int(horizon), configured)


class QualityConfig(_StrictModel):
  """Input quality gates."""

  minimum_history_rows: int = Field(default=252, ge=2)
  minimum_total_events: int = Field(default=100, ge=1)
  minimum_usable_symbols: int = Field(default=1, ge=1)
  minimum_history_coverage_ratio: float = Field(default=0.8, gt=0, le=1)
  minimum_end_coverage_ratio: float = Field(default=0.8, gt=0, le=1)
  minimum_benchmark_coverage_ratio: float = Field(default=0.8, gt=0, le=1)
  exclude_corporate_action_windows_without_adjustment: bool = True


class RuntimeConfig(_StrictModel):
  """Runtime-only controls; these do not change statistical definitions."""

  batch_size: int = Field(default=300, ge=1)
  output_root: str = ".runtime/research-runs"
  minimum_available_memory_gib: float = Field(default=4.0, ge=1.0)
  memory_sample_interval_seconds: float = Field(default=0.5, ge=0.1, le=10.0)


class StudyConfig(_StrictModel):
  """Complete, serializable configuration for one study run.

  The nested shape matches ``configs/volume_shock_v1.yaml``.  A small legacy
  normalization layer keeps the earlier flat draft usable without making it
  the public format.
  """

  study: str = "volume-shock"
  version: str = "v1"
  universe: UniverseConfig = Field(default_factory=UniverseConfig)
  event: EventConfig = Field(default_factory=EventConfig)
  conditioning: ConditioningConfig = Field(default_factory=ConditioningConfig)
  outcomes: OutcomeConfig = Field(default_factory=OutcomeConfig)
  statistics: StatisticsConfig = Field(default_factory=StatisticsConfig)
  quality: QualityConfig = Field(default_factory=QualityConfig)
  runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
  date_range: tuple[date, date] | None = None

  @model_validator(mode="before")
  @classmethod
  def _normalize_legacy_shape(cls, value: Any) -> Any:
    if not isinstance(value, dict):
      return value
    data = dict(value)

    universe = data.get("universe")
    if isinstance(universe, str):
      data["universe"] = {
        "instrument_type": "stock" if universe.lower() == "ashare" else universe
      }

    event = data.get("event")
    if isinstance(event, dict):
      aliases = {
        "rvol_window": "relative_volume_window",
        "rvol_bins": "relative_volume_bins",
        "rvol_threshold": "relative_volume_threshold",
      }
      normalized = dict(event)
      for old, new in aliases.items():
        if old in normalized and new not in normalized:
          normalized[new] = normalized.pop(old)
      data["event"] = normalized

    outcomes = data.get("outcomes")
    if isinstance(outcomes, dict):
      normalized_outcomes = dict(outcomes)
      normalized_outcomes.pop("entry", None)
      benchmark = normalized_outcomes.pop("benchmark", None)
      if benchmark:
        universe_data = dict(data.get("universe") or {})
        universe_data.setdefault("benchmark_code", str(benchmark))
        data["universe"] = universe_data
      data["outcomes"] = normalized_outcomes
    return data

  @field_validator("date_range")
  @classmethod
  def _validate_date_range(
    cls, value: tuple[date, date] | None
  ) -> tuple[date, date] | None:
    if value is not None and value[1] < value[0]:
      raise ValueError("date_range end must not precede start")
    return value

  @model_validator(mode="after")
  def _validate_study(self) -> "StudyConfig":
    if self.study != "volume-shock":
      raise ValueError(f"unsupported study: {self.study}")
    if self.event.relative_volume_threshold < self.event.relative_volume_bins[0]:
      raise ValueError("relative_volume_threshold is below the first bin edge")
    return self

  @property
  def study_id(self) -> str:
    return self.study

  @property
  def required_lookback(self) -> int:
    """Number of valid pre-event rows required by configured features."""
    return max(
      self.quality.minimum_history_rows,
      self.event.relative_volume_window,
      self.event.relative_amount_window,
      self.event.log_volume_zscore_window,
      self.event.breakout_window,
      self.conditioning.price_position_window,
      21,  # pre-event 20-day momentum needs T-21
    )


def _strictly_increasing_edges(
  value: tuple[float, ...], *, minimum: float
) -> tuple[float, ...]:
  if len(value) < 2:
    raise ValueError("at least two bin edges are required")
  edges = tuple(float(item) for item in value)
  if edges[0] < minimum:
    raise ValueError(f"bin edges must start at or above {minimum}")
  if any(right <= left for left, right in zip(edges, edges[1:])):
    raise ValueError("bin edges must be strictly increasing")
  return edges
