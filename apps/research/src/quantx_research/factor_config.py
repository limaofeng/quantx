"""Strict, reproducible configuration for daily factor association research."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator
from quantx_domain.factors import FACTOR_DEFINITIONS, normalize_conditions

from quantx_research.core.config import (
  OutcomeConfig,
  RuntimeConfig,
  StatisticsConfig,
  UniverseConfig,
  _StrictModel,
)


class FactorUniverseConfig(UniverseConfig):
  instrument_type: Literal["stock"] = "stock"
  exclude_st: bool = False
  include_industries: tuple[str, ...] = ()
  exclude_industries: tuple[str, ...] = ()
  minimum_listing_days: int = Field(default=0, ge=0)

  @model_validator(mode="after")
  def historical_filters(self) -> "FactorUniverseConfig":
    if self.exclude_st or self.include_industries or self.exclude_industries:
      raise ValueError(
        "历史 ST/行业分类尚未验证，不能忽略这些条件生成精确研究；"
        "如需总体参考报告，请显式使用 exclude_st=false 和空行业条件"
      )
    return self

  def identity(self) -> dict[str, object]:
    return {
      "instrument_type": self.instrument_type,
      "exclude_st": self.exclude_st,
      "include_industries": list(self.include_industries),
      "exclude_industries": list(self.exclude_industries),
    }


class FactorOutcomeConfig(OutcomeConfig):
  horizons: tuple[int, ...] = tuple(range(1, 21))
  include_close_response: Literal[True] = True
  include_next_open_return: Literal[True] = True
  include_benchmark_excess: Literal[False] = False
  include_cross_section_excess: Literal[False] = False

  @model_validator(mode="after")
  def bounded_horizons(self) -> "FactorOutcomeConfig":
    if max(self.horizons) > 60:
      raise ValueError("factor-study 观察周期不得超过60个交易日")
    return self


class FactorStatisticsConfig(StatisticsConfig):
  run_regression: Literal[False] = False


class FactorStudyConfig(_StrictModel):
  study: Literal["factor-study"] = "factor-study"
  version: Literal["v1"] = "v1"
  factor_ids: tuple[str, ...] = ()
  conditions: tuple[dict[str, object], ...] = ()
  universe: FactorUniverseConfig = Field(default_factory=FactorUniverseConfig)
  date_range: tuple[date, date] | None = None
  outcomes: FactorOutcomeConfig = Field(default_factory=FactorOutcomeConfig)
  statistics: FactorStatisticsConfig = Field(default_factory=FactorStatisticsConfig)
  runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

  @model_validator(mode="after")
  def validate_research(self) -> "FactorStudyConfig":
    definitions = {item.id: item for item in FACTOR_DEFINITIONS}
    normalized = normalize_conditions(list(self.conditions))
    ids = tuple(sorted(set(self.factor_ids)))
    required = set(ids) | {item["factor_id"] for item in normalized}
    if not required:
      raise ValueError("factor_ids 或 conditions 至少指定一个研究对象")
    for factor_id in required:
      definition = definitions.get(factor_id)
      if definition is None:
        raise ValueError(f"未知因子: {factor_id}")
      if not definition.research_supported:
        raise ValueError(f"因子暂不支持历史研究: {factor_id}")
    if self.date_range and self.date_range[1] < self.date_range[0]:
      raise ValueError("date_range end must not precede start")
    if self.statistics.run_regression:
      raise ValueError("factor-study 不执行自动回归或权重优化")
    calendar_years = (
      self.date_range[1].year - self.date_range[0].year + 1
      if self.date_range
      else self.universe.lookback_years + 1
    )
    rows_per_group = (calendar_years + 2) * len(self.outcomes.horizons) * 2
    group_counts = [
      3 if definitions[factor_id].kind == "binary" else 6 for factor_id in ids
    ]
    if normalized:
      group_counts.append(len(normalized) + 2)
    if (
      max(group_counts, default=0) * rows_per_group > 12_000
      or sum(group_counts) * rows_per_group > 60_000
    ):
      raise ValueError(
        "研究统计超出页面安全读取上限；请拆分因子批次或减少条件/观察周期/历史年数"
      )
    object.__setattr__(self, "factor_ids", ids)
    object.__setattr__(self, "conditions", tuple(normalized))
    return self

  @property
  def study_id(self) -> str:
    return self.study

  @property
  def required_factor_ids(self) -> tuple[str, ...]:
    return tuple(
      sorted(
        set(self.factor_ids) | {str(item["factor_id"]) for item in self.conditions}
      )
    )

  @property
  def required_lookback(self) -> int:
    wanted = set(self.required_factor_ids)
    return max(item.lookback for item in FACTOR_DEFINITIONS if item.id in wanted)
