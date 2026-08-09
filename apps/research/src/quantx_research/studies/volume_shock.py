"""Volume-shock event study orchestration."""

from __future__ import annotations

import pandas as pd

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import StudyResult
from quantx_research.core.regression import run_panel_regressions
from quantx_research.core.statistics import (
  DateBlockBootstrap,
  calculate_comparison_statistics,
  calculate_event_curve,
  calculate_grouped_statistics,
  calculate_robustness_statistics,
)
from quantx_research.core.volume_shock import (
  apply_event_cooldown,
  build_volume_analysis_sample,
  build_volume_shock_events,
  volume_shock_events_from_sample,
)


class VolumeShockStudy:
  """Abnormal daily volume × pre-event price-position study."""

  study_id = "volume-shock"
  version = "v1"
  required_columns = (
    "stock_code",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
  )

  def __init__(self, config: StudyConfig | None = None) -> None:
    self.config = config or StudyConfig()

  @property
  def required_lookback(self) -> int:
    return self.config.required_lookback

  def build_events(
    self,
    panel: pd.DataFrame,
    config: StudyConfig | None = None,
    benchmark: pd.DataFrame | None = None,
  ) -> pd.DataFrame:
    resolved = config or self.config
    return build_volume_shock_events(panel, resolved, benchmark=benchmark)

  def build_analysis_sample(
    self,
    panel: pd.DataFrame,
    config: StudyConfig | None = None,
    benchmark: pd.DataFrame | None = None,
  ) -> pd.DataFrame:
    resolved = config or self.config
    return build_volume_analysis_sample(
      panel,
      resolved,
      benchmark=benchmark,
    )

  def analyze(
    self,
    events: pd.DataFrame,
    config: StudyConfig | None = None,
    *,
    analysis_sample: pd.DataFrame | None = None,
  ) -> StudyResult:
    resolved = config or self.config
    has_analysis_sample = analysis_sample is not None
    full_sample = (
      _with_shock_identity(analysis_sample, events)
      if analysis_sample is not None
      else pd.DataFrame(columns=events.columns)
    )
    bootstrap = DateBlockBootstrap(
      full_sample["event_date"]
      if "event_date" in full_sample
      else pd.Series(dtype="datetime64[ns]"),
      samples=resolved.statistics.bootstrap_samples,
      seed=resolved.statistics.random_seed,
      confidence_level=resolved.statistics.confidence_level,
    )
    warnings = _study_warnings(events, resolved)
    if not has_analysis_sample:
      warnings.append(
        "未提供阈值前完整分析样本；为避免事件条件选择偏差，"
        "正常量对照、主回归和量能稳健性均未运行。"
      )
    return StudyResult(
      study_id=resolved.study_id,
      version=resolved.version,
      event_count=len(events),
      analysis_sample_count=len(full_sample),
      grouped_statistics=calculate_grouped_statistics(
        events, resolved, bootstrap=bootstrap
      )
      if not events.empty
      else [],
      event_curve=calculate_event_curve(events, resolved, bootstrap=bootstrap)
      if not events.empty
      else [],
      comparison=calculate_comparison_statistics(full_sample, resolved)
      if not full_sample.empty
      else [],
      regressions=run_panel_regressions(full_sample, resolved)
      if not full_sample.empty
      else [],
      robustness=calculate_robustness_statistics(
        full_sample,
        resolved,
        bootstrap=DateBlockBootstrap(
          full_sample["event_date"],
          samples=resolved.statistics.bootstrap_samples,
          seed=resolved.statistics.random_seed,
          confidence_level=resolved.statistics.confidence_level,
        ),
      )
      if not full_sample.empty
      else {},
      warnings=warnings,
    )

  def run(
    self,
    panel: pd.DataFrame,
    config: StudyConfig | None = None,
    benchmark: pd.DataFrame | None = None,
  ) -> tuple[pd.DataFrame, StudyResult]:
    """Build events and analyze them, including cooldown sensitivity checks."""
    _, events, result = self.run_with_analysis_sample(
      panel,
      config,
      benchmark,
    )
    return events, result

  def run_with_analysis_sample(
    self,
    panel: pd.DataFrame,
    config: StudyConfig | None = None,
    benchmark: pd.DataFrame | None = None,
  ) -> tuple[pd.DataFrame, pd.DataFrame, StudyResult]:
    """Return the eligible analysis panel, threshold events, and results."""
    resolved = config or self.config
    analysis_sample = build_volume_analysis_sample(
      panel,
      resolved,
      benchmark=benchmark,
    )
    candidates = volume_shock_events_from_sample(
      analysis_sample,
      resolved,
      cooldown_days=0,
    )
    events = apply_event_cooldown(candidates, resolved.event.cooldown_days)
    analysis_sample = _with_shock_identity(analysis_sample, events)
    result = self.analyze(
      events,
      resolved,
      analysis_sample=analysis_sample,
    )

    robustness = dict(result.robustness)
    comparison_sensitivity = dict(result.comparison_sensitivity)
    for cooldown in (5, 20):
      if cooldown == resolved.event.cooldown_days:
        continue
      sensitivity_events = apply_event_cooldown(candidates, cooldown)
      if sensitivity_events.empty:
        robustness[f"cooldown_{cooldown}d"] = []
        comparison_sensitivity[f"cooldown_{cooldown}d"] = []
        continue
      sensitivity_sample = _with_shock_identity(
        analysis_sample,
        sensitivity_events,
      )
      comparison_sensitivity[f"cooldown_{cooldown}d"] = calculate_comparison_statistics(
        sensitivity_sample, resolved
      )
      sensitivity_bootstrap = DateBlockBootstrap(
        analysis_sample["event_date"],
        samples=resolved.statistics.bootstrap_samples,
        seed=resolved.statistics.random_seed,
        confidence_level=resolved.statistics.confidence_level,
      )
      robustness[f"cooldown_{cooldown}d"] = calculate_grouped_statistics(
        sensitivity_events,
        resolved,
        dimensions=("price_position_bin", "event_direction"),
        bootstrap=sensitivity_bootstrap,
      )
    return (
      analysis_sample,
      events,
      result.model_copy(
        update={
          "robustness": robustness,
          "comparison_sensitivity": comparison_sensitivity,
        }
      ),
    )


def _study_warnings(events: pd.DataFrame, config: StudyConfig) -> list[str]:
  warnings = [
    "结果描述历史相关性，不构成因果证据或投资建议。",
    "研究总体来自当前证券主表快照，未完整重建历史上市、退市成分，"
    "可能存在生存者偏差；结果不代表严格历史全市场。",
    "v1 不使用行业固定效应，因为当前行业成分缺少历史有效期。",
    "v1 不依据当前证券名称回推历史 ST 状态。",
    "主比较使用预注册正常量区间，并先按交易日与价格位置做截面等权；"
    "差异仍是历史条件关联，不是因果效应。",
    "主比较与主回归的 shock 身份来自配置冷却后的事件；连续异常量日不会"
    "绕过冷却重复计入主事件组。",
    "置信区间与显著性使用按交易日期的移动区块 Bootstrap；"
    "独立日期不足配置门槛时不报告推断。",
    "主回归固定按沪深300超额、全市场等权超额、绝对收益的预注册顺序选择"
    "因变量，不依据样本拟合或覆盖结果切换。",
    "event_direction 使用事件日收盘收益，只用于事后分组描述，"
    "不是 T-1 可得条件，也不能解释为事前选股信号。",
  ]
  if len(events) < config.quality.minimum_total_events:
    warnings.append(
      f"有效事件仅 {len(events)} 个，低于配置门槛 "
      f"{config.quality.minimum_total_events}。"
    )
  if config.outcomes.include_benchmark_excess and not any(
    column.startswith("csi300_excess_") for column in events.columns
  ):
    warnings.append("未提供完整基准行情，沪深300超额收益未计算。")
  if (
    config.quality.exclude_corporate_action_windows_without_adjustment
    and "adjustment_valid" not in events.columns
  ):
    warnings.append("输入未提供 adjustment_valid；核心按上游已经完成时点可得复权处理。")
  return warnings


def _with_shock_identity(
  analysis_sample: pd.DataFrame,
  shock_events: pd.DataFrame,
) -> pd.DataFrame:
  """Attach cooldown-adjusted event membership without filtering the sample."""
  result = analysis_sample.copy()
  if result.empty:
    result["is_primary_shock_event"] = pd.Series(dtype=bool)
    return result
  sample_keys = pd.MultiIndex.from_arrays(
    [
      result["stock_code"].astype(str),
      pd.to_datetime(result["event_date"], errors="coerce"),
    ]
  )
  if shock_events.empty:
    result["is_primary_shock_event"] = False
    return result
  event_keys = pd.MultiIndex.from_arrays(
    [
      shock_events["stock_code"].astype(str),
      pd.to_datetime(shock_events["event_date"], errors="coerce"),
    ]
  ).drop_duplicates()
  result["is_primary_shock_event"] = sample_keys.isin(event_keys)
  return result
