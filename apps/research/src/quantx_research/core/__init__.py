"""Pure computation contracts for QuantX offline research."""

from quantx_research.core.config import (
  ConditioningConfig,
  EventConfig,
  OutcomeConfig,
  QualityConfig,
  RuntimeConfig,
  StatisticsConfig,
  StudyConfig,
  UniverseConfig,
)
from quantx_research.core.forward_returns import (
  add_forward_outcomes,
  market_calendar,
  required_outcome_columns,
)
from quantx_research.core.models import (
  ComparisonStatistic,
  EventCurvePoint,
  EventRecord,
  GroupStatistic,
  RegressionCoefficient,
  RegressionResult,
  StudyResult,
)
from quantx_research.core.protocols import ResearchStudy
from quantx_research.core.regression import (
  regression_input_columns,
  run_panel_regressions,
)
from quantx_research.core.statistics import (
  DateBlockBootstrap,
  apply_comparison_fdr,
  apply_fdr,
  calculate_comparison_statistics,
  calculate_event_curve,
  calculate_grouped_statistics,
  calculate_robustness_statistics,
)
from quantx_research.core.volume_shock import (
  add_volume_features,
  analysis_bounds,
  apply_event_cooldown,
  build_volume_analysis_sample,
  build_volume_shock_events,
  event_record_columns,
  normalize_market_panel,
  select_volume_analysis_sample,
)

__all__ = [
  "ConditioningConfig",
  "ComparisonStatistic",
  "EventConfig",
  "EventCurvePoint",
  "EventRecord",
  "GroupStatistic",
  "OutcomeConfig",
  "QualityConfig",
  "RegressionCoefficient",
  "RegressionResult",
  "ResearchStudy",
  "RuntimeConfig",
  "StatisticsConfig",
  "StudyConfig",
  "StudyResult",
  "UniverseConfig",
  "DateBlockBootstrap",
  "add_forward_outcomes",
  "add_volume_features",
  "analysis_bounds",
  "apply_comparison_fdr",
  "apply_event_cooldown",
  "apply_fdr",
  "build_volume_analysis_sample",
  "build_volume_shock_events",
  "calculate_comparison_statistics",
  "calculate_event_curve",
  "calculate_grouped_statistics",
  "calculate_robustness_statistics",
  "event_record_columns",
  "market_calendar",
  "normalize_market_panel",
  "required_outcome_columns",
  "regression_input_columns",
  "run_panel_regressions",
  "select_volume_analysis_sample",
]
